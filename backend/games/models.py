from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q

from questions.models import AnswerOption, ChoiceQuestion, NumericQuestion

MAX_PLAYERS = 3

PLAYER_COLORS = [
    ('red', 'Red'),
    ('blue', 'Blue'),
    ('yellow', 'Yellow'),
]


def _finished_after_started(prefix):
    return models.CheckConstraint(
        condition=Q(started_at__isnull=True) | Q(finished_at__isnull=True) | Q(finished_at__gte=F('started_at')),
        name=f'{prefix}_finished_after_started',
    )


class Game(models.Model):
    WAITING = 'waiting'
    IN_PROGRESS = 'in_progress'
    FINISHED = 'finished'
    CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (WAITING, 'Waiting'),
        (IN_PROGRESS, 'In Progress'),
        (FINISHED, 'Finished'),
        (CANCELLED, 'Cancelled'),
    ]

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_games',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=WAITING)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=['waiting', 'in_progress', 'finished', 'cancelled']),
                name='game_status_valid',
            ),
            _finished_after_started('game'),
        ]

    def __str__(self) -> str:
        return f'Game #{self.pk} ({self.get_status_display()})'

    def get_current_round(self):
        """The round with the highest number, or None if the game has no rounds yet."""
        return self.rounds.order_by('-number').first()

    def clean(self):
        super().clean()
        if self.status in (self.IN_PROGRESS, self.FINISHED) and self.started_at is None:
            raise ValidationError({'started_at': 'A started game must have a start time.'})
        if self.status == self.FINISHED and self.finished_at is None:
            raise ValidationError({'finished_at': 'A finished game must have a finish time.'})


class GamePlayer(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='players')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='game_players',
    )
    player_order = models.PositiveSmallIntegerField()
    color = models.CharField(max_length=10, choices=PLAYER_COLORS, default='red')
    score = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['game', 'player_order']
        constraints = [
            models.UniqueConstraint(fields=['game', 'user'], name='gameplayer_unique_user_per_game'),
            models.UniqueConstraint(fields=['game', 'player_order'], name='gameplayer_unique_order_per_game'),
            models.UniqueConstraint(fields=['game', 'color'], name='gameplayer_unique_color_per_game'),
            models.CheckConstraint(
                condition=Q(player_order__gte=1, player_order__lte=MAX_PLAYERS),
                name='gameplayer_order_in_range',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.user} in game #{self.game_id} (#{self.player_order})'

    def clean(self):
        super().clean()
        if self.game_id is None:
            return
        others = GamePlayer.objects.filter(game_id=self.game_id).exclude(pk=self.pk)
        if others.count() >= MAX_PLAYERS:
            raise ValidationError(f'A game can have at most {MAX_PLAYERS} players.')
        if self._state.adding:
            self._ensure_game_is_waiting('join')

    def _ensure_game_is_waiting(self, action):
        # Read (and lock) the game row from the database, not a possibly stale self.game, so a
        # join/leave cannot interleave with game initialization, which locks the same row.
        if not Game.objects.select_for_update().filter(pk=self.game_id, status=Game.WAITING).exists():
            raise ValidationError(f'Players can only {action} a game that is still waiting to start.')

    def save(self, *args, **kwargs):
        # The line-up is frozen once the game starts (M05): no new players after that.
        with transaction.atomic():
            if self._state.adding:
                self._ensure_game_is_waiting('join')
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Deleting the whole game still cascades; only removing a single player is blocked.
        with transaction.atomic():
            self._ensure_game_is_waiting('leave')
            return super().delete(*args, **kwargs)

# This is the new class
class WaitingRoom(models.Model):
    # Here I define the model which is used to store the users who are waiting to join a game. Each user can only be in the waiting room once, and the waiting room is ordered by the time the user joined.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='waiting_room',
    )
    created_at = models.DateTimeField(auto_now_add=True)

# Here I define the meta class which is used to order the waiting room by the time the user joined and by the primary key of the user.
    class Meta:
        ordering = ['created_at', 'pk']
# Here I define the string representation of the waiting room which is used to show the user who is waiting to join the game.
    def __str__(self) -> str:
        return f'{self.user} is waiting to join a game'


class Round(models.Model):
    # M06 uses PENDING -> OPEN -> EVALUATED (the milestone's pending -> active -> completed).
    # CLOSED stays available from M03 but the numeric round workflow does not use it.
    PENDING = 'pending'
    OPEN = 'open'
    CLOSED = 'closed'
    EVALUATED = 'evaluated'

    ROUND_STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (OPEN, 'Open'),
        (CLOSED, 'Closed'),
        (EVALUATED, 'Evaluated'),
    ]

    CHOICE = 'choice'
    NUMERIC = 'numeric'

    QUESTION_TYPE_CHOICES = [
        (CHOICE, 'Choice'),
        (NUMERIC, 'Numeric'),
    ]

    # Kind of round (M06). Blank for plain M03 rounds that are not run by games.rounds.
    CITY_CAPTURE = 'city_capture'

    ROUND_TYPE_CHOICES = [
        (CITY_CAPTURE, 'City capture'),
    ]

    # Why a completed round has places without a player.
    NOT_ENOUGH_ANSWERS = 'not_enough_answers'
    QUESTION_LIMIT = 'question_limit'
    BANK_EXHAUSTED = 'bank_exhausted'

    UNRESOLVED_REASON_CHOICES = [
        (NOT_ENOUGH_ANSWERS, 'Not enough accepted answers'),
        (QUESTION_LIMIT, 'Question limit reached'),
        (BANK_EXHAUSTED, 'No unused numeric question left'),
    ]

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='rounds')
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=ROUND_STATUS_CHOICES, default=PENDING)
    round_type = models.CharField(max_length=20, choices=ROUND_TYPE_CHOICES, blank=True, default='')
    # How many places the round decides (1st, 2nd, ...). Fixed when the round is created.
    places_needed = models.PositiveSmallIntegerField(null=True, blank=True)
    # Kept in sync automatically in save(): it is derived from whichever question is set,
    # never entered by hand, so it cannot disagree with the question.
    question_type = models.CharField(max_length=20, choices=QUESTION_TYPE_CHOICES, editable=False)
    choice_question = models.ForeignKey(
        ChoiceQuestion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='rounds',
    )
    # For a numeric round this is the first question asked; follow-ups live in RoundQuestion.
    numeric_question = models.ForeignKey(
        NumericQuestion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='rounds',
    )
    started_at = models.DateTimeField(null=True, blank=True)
    # The milestone's completed_at.
    finished_at = models.DateTimeField(null=True, blank=True)
    winner = models.ForeignKey(
        GamePlayer,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='won_rounds',
    )
    unresolved_reason = models.CharField(
        max_length=30, choices=UNRESOLVED_REASON_CHOICES, blank=True, default=''
    )

    class Meta:
        ordering = ['game', 'number']
        constraints = [
            models.UniqueConstraint(fields=['game', 'number'], name='round_unique_number_per_game'),
            models.CheckConstraint(condition=Q(number__gte=1), name='round_number_positive'),
            models.CheckConstraint(
                condition=Q(status__in=['pending', 'open', 'closed', 'evaluated']),
                name='round_status_valid',
            ),
            # Safety net for writes that bypass save() (e.g. queryset.update()):
            # exactly one question, and it must match question_type.
            models.CheckConstraint(
                condition=(
                    Q(question_type='choice', choice_question__isnull=False, numeric_question__isnull=True)
                    | Q(question_type='numeric', numeric_question__isnull=False, choice_question__isnull=True)
                ),
                name='round_question_matches_type',
            ),
            _finished_after_started('round'),
            # A winner and a reason only exist once the round is evaluated.
            models.CheckConstraint(
                condition=Q(winner__isnull=True) | Q(status='evaluated'),
                name='round_winner_only_when_evaluated',
            ),
            models.CheckConstraint(
                condition=Q(unresolved_reason='') | Q(status='evaluated'),
                name='round_reason_only_when_evaluated',
            ),
            # An evaluated city capture has a finish time, and a winner unless a reason says why not.
            models.CheckConstraint(
                condition=~Q(round_type='city_capture', status='evaluated', finished_at__isnull=True),
                name='round_city_capture_evaluated_has_finish_time',
            ),
            models.CheckConstraint(
                condition=~Q(round_type='city_capture', status='evaluated', winner__isnull=True, unresolved_reason=''),
                name='round_city_capture_no_winner_needs_reason',
            ),
        ]

    def __str__(self) -> str:
        return f'Round {self.number} of game #{self.game_id}'

    @property
    def question(self):
        return self.choice_question if self.choice_question_id is not None else self.numeric_question

    def ranking(self):
        """The decided places so far, as answers ordered by place."""
        return self.answers.filter(place__isnull=False).select_related('player__user').order_by('place')

    def _sync_question_type(self):
        """Set question_type from the question; return False if there is not exactly one question."""
        has_choice = self.choice_question_id is not None
        has_numeric = self.numeric_question_id is not None
        if has_choice == has_numeric:
            return False
        self.question_type = self.CHOICE if has_choice else self.NUMERIC
        return True

    def clean_fields(self, exclude=None):
        # question_type is derived, not user input: sync it first and validate it via clean().
        self._sync_question_type()
        super().clean_fields(exclude={*(exclude or ()), 'question_type'})

    def clean(self):
        super().clean()
        if not self._sync_question_type():
            raise ValidationError('A round must have exactly one question: either a choice or a numeric one.')

        evaluated = self.status == self.EVALUATED
        if self.winner_id is not None:
            if not evaluated:
                raise ValidationError({'winner': 'Only an evaluated round has a winner.'})
            if self.winner.game_id != self.game_id:
                raise ValidationError({'winner': 'The winner must be a player of the same game.'})
        if self.unresolved_reason and not evaluated:
            raise ValidationError({'unresolved_reason': 'Only an evaluated round can have unresolved places.'})

        if self.round_type == self.CITY_CAPTURE:
            if self.question_type != self.NUMERIC:
                raise ValidationError('A city capture round uses a numeric question.')
            if not self.places_needed:
                raise ValidationError({'places_needed': 'A city capture round must fix how many places it decides.'})
            if evaluated and self.finished_at is None:
                raise ValidationError({'finished_at': 'An evaluated round must have a completion time.'})
            if evaluated and self.winner_id is None and not self.unresolved_reason:
                raise ValidationError(
                    {'unresolved_reason': 'A round completed without a winner must say why first place is unresolved.'}
                )

    def save(self, *args, **kwargs):
        if self._sync_question_type():
            update_fields = kwargs.get('update_fields')
            if update_fields is not None and {'choice_question', 'numeric_question'} & set(update_fields):
                kwargs['update_fields'] = {*update_fields, 'question_type'}
        super().save(*args, **kwargs)


class RoundQuestion(models.Model):
    """One numeric question asked within a round: the first one, or a tie-break / retry for a group.

    The question's text and correct answer are copied when it is asked, so later edits in the
    question bank do not change what was asked or how it is scored.
    """

    PENDING = 'pending'
    ACTIVE = 'active'
    COMPLETED = 'completed'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (ACTIVE, 'Active'),
        (COMPLETED, 'Completed'),
    ]

    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='questions')
    sequence = models.PositiveSmallIntegerField()
    numeric_question = models.ForeignKey(
        NumericQuestion,
        on_delete=models.PROTECT,
        related_name='round_questions',
    )
    text = models.TextField()
    correct_answer = models.IntegerField()
    participants = models.ManyToManyField(GamePlayer, related_name='round_questions')
    # The places this question decides, e.g. 1-2 for the first question or 2-2 for a tie for second.
    first_place = models.PositiveSmallIntegerField()
    last_place = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['round', 'sequence']
        constraints = [
            models.UniqueConstraint(fields=['round', 'sequence'], name='roundquestion_unique_sequence'),
            models.UniqueConstraint(
                fields=['round', 'numeric_question'], name='roundquestion_unique_bank_question_per_round'
            ),
            models.CheckConstraint(condition=Q(sequence__gte=1, sequence__lte=3), name='roundquestion_sequence_range'),
            models.CheckConstraint(
                condition=Q(first_place__gte=1, last_place__gte=F('first_place')),
                name='roundquestion_place_range',
            ),
            models.CheckConstraint(
                condition=Q(status__in=['pending', 'active', 'completed']),
                name='roundquestion_status_valid',
            ),
            # An asked question has its start and deadline; a pending one has neither.
            models.CheckConstraint(
                condition=(
                    Q(status='pending', started_at__isnull=True, deadline__isnull=True)
                    | (~Q(status='pending') & Q(started_at__isnull=False, deadline__gt=F('started_at')))
                ),
                name='roundquestion_timing_matches_status',
            ),
            models.CheckConstraint(
                condition=Q(completed_at__isnull=True) | Q(status='completed'),
                name='roundquestion_completed_at_only_when_completed',
            ),
        ]

    def __str__(self) -> str:
        return f'Question {self.sequence} of round {self.round.number} (game #{self.round.game_id})'


class RoundAnswer(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='answers')
    player = models.ForeignKey(GamePlayer, on_delete=models.CASCADE, related_name='answers')
    # Set for answers given in a numeric round run by games.rounds (M06): the question answered.
    question = models.ForeignKey(
        RoundQuestion,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='answers',
    )
    selected_option = models.ForeignKey(
        AnswerOption,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='round_answers',
    )
    numeric_value = models.IntegerField(null=True, blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    points_awarded = models.IntegerField(default=0)
    submitted_at = models.DateTimeField(null=True, blank=True)
    # The place in the round's ranking this answer earned, if any.
    place = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['round', 'player__player_order']
        constraints = [
            # Plain rounds: one answer per player. Numeric rounds: one answer per player per question.
            models.UniqueConstraint(
                fields=['round', 'player'],
                condition=Q(question__isnull=True),
                name='roundanswer_unique_player_per_round',
            ),
            models.UniqueConstraint(fields=['question', 'player'], name='roundanswer_unique_player_per_question'),
            models.UniqueConstraint(
                fields=['round', 'place'],
                condition=Q(place__isnull=False),
                name='roundanswer_unique_place_per_round',
            ),
            # An answer holds a selected option or a number, never both (neither = not answered yet).
            models.CheckConstraint(
                condition=Q(selected_option__isnull=True) | Q(numeric_value__isnull=True),
                name='roundanswer_single_answer_kind',
            ),
            models.CheckConstraint(
                condition=Q(place__isnull=True) | Q(question__isnull=False, place__gte=1),
                name='roundanswer_place_needs_question',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.player.user} answer in round {self.round.number}'

    def clean(self):
        super().clean()
        if self.round_id is None or self.player_id is None:
            return
        if self.player.game_id != self.round.game_id:
            raise ValidationError({'player': 'The player must belong to the same game as the round.'})
        if self.question_id is not None:
            if self.question.round_id != self.round_id:
                raise ValidationError({'question': 'The question must belong to the same round.'})
            if not self.question.participants.filter(pk=self.player_id).exists():
                raise ValidationError({'player': 'Only a participant of the question can answer it.'})

        if self.round.question_type == Round.CHOICE:
            if self.numeric_value is not None:
                raise ValidationError({'numeric_value': 'A choice round takes a selected option, not a number.'})
            if self.selected_option_id is not None and self.selected_option.question_id != self.round.choice_question_id:
                raise ValidationError({'selected_option': "The option must belong to the round's question."})
        elif self.selected_option_id is not None:
            raise ValidationError({'selected_option': 'A numeric round takes a number, not an option.'})
