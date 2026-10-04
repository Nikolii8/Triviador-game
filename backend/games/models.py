from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from questions.models import AnswerOption, ChoiceQuestion, NumericQuestion

MAX_PLAYERS = 3


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
    score = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['game', 'player_order']
        constraints = [
            models.UniqueConstraint(fields=['game', 'user'], name='gameplayer_unique_user_per_game'),
            models.UniqueConstraint(fields=['game', 'player_order'], name='gameplayer_unique_order_per_game'),
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


class Round(models.Model):
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

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='rounds')
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=ROUND_STATUS_CHOICES, default=PENDING)
    question_type = models.CharField(max_length=20, choices=QUESTION_TYPE_CHOICES)
    choice_question = models.ForeignKey(
        ChoiceQuestion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='rounds',
    )
    numeric_question = models.ForeignKey(
        NumericQuestion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='rounds',
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['game', 'number']
        constraints = [
            models.UniqueConstraint(fields=['game', 'number'], name='round_unique_number_per_game'),
            models.CheckConstraint(condition=Q(number__gte=1), name='round_number_positive'),
            models.CheckConstraint(
                condition=Q(status__in=['pending', 'open', 'closed', 'evaluated']),
                name='round_status_valid',
            ),
            # Exactly one question, and it must match question_type.
            models.CheckConstraint(
                condition=(
                    Q(question_type='choice', choice_question__isnull=False, numeric_question__isnull=True)
                    | Q(question_type='numeric', numeric_question__isnull=False, choice_question__isnull=True)
                ),
                name='round_question_matches_type',
            ),
            _finished_after_started('round'),
        ]

    def __str__(self) -> str:
        return f'Round {self.number} of game #{self.game_id}'

    @property
    def question(self):
        return self.choice_question if self.question_type == self.CHOICE else self.numeric_question

    def clean(self):
        super().clean()
        if self.question_type == self.CHOICE:
            if self.choice_question_id is None:
                raise ValidationError({'choice_question': 'A choice round needs a choice question.'})
            if self.numeric_question_id is not None:
                raise ValidationError({'numeric_question': 'A choice round cannot have a numeric question.'})
        elif self.question_type == self.NUMERIC:
            if self.numeric_question_id is None:
                raise ValidationError({'numeric_question': 'A numeric round needs a numeric question.'})
            if self.choice_question_id is not None:
                raise ValidationError({'choice_question': 'A numeric round cannot have a choice question.'})


class RoundAnswer(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='answers')
    player = models.ForeignKey(GamePlayer, on_delete=models.CASCADE, related_name='answers')
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

    class Meta:
        ordering = ['round', 'player__player_order']
        constraints = [
            models.UniqueConstraint(fields=['round', 'player'], name='roundanswer_unique_player_per_round'),
            # An answer holds a selected option or a number, never both (neither = not answered yet).
            models.CheckConstraint(
                condition=Q(selected_option__isnull=True) | Q(numeric_value__isnull=True),
                name='roundanswer_single_answer_kind',
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

        if self.round.question_type == Round.CHOICE:
            if self.numeric_value is not None:
                raise ValidationError({'numeric_value': 'A choice round takes a selected option, not a number.'})
            if self.selected_option_id is not None and self.selected_option.question_id != self.round.choice_question_id:
                raise ValidationError({'selected_option': "The option must belong to the round's question."})
        elif self.selected_option_id is not None:
            raise ValidationError({'selected_option': 'A numeric round takes a number, not an option.'})
