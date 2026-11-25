"""Numeric rounds (M06): create, start, answer and evaluate a `city_capture` round.

Service layer: every operation here runs in one transaction and either applies all of its
changes or raises a RoundError and changes nothing. The error class tells the caller why:

- InvalidRoundInput  - the request itself is malformed (e.g. a non-integer answer);
- RoundStateError    - the request is well-formed but not allowed right now
                       (round not started, time is up, already answered, ...);
- RoundNotFound      - the game, round or player does not exist.

Round lifecycle (stored statuses in brackets): pending -> active [open] -> completed [evaluated].
A round asks 1 to 3 question steps (RoundQuestion). Start, one answer per player, 10 seconds,
evaluation and the limit of 3 are shared by every round type and both question kinds; what
differs is how an answer is checked (number vs option) and what follows a step:

- city capture (M06): numeric only; all three players; a full tie or no answers -> new numeric
  question for that group; places already decided are kept.
- battle / capital attack (M08, created in games.battles): starts with a choice question for the
  attacker and the defender. One correct answer wins; two correct -> numeric question; none
  correct -> another choice question. Once numeric, it stays numeric (only first place needed).
"""

import random
import re
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from questions.models import ChoiceQuestion, NumericQuestion

from . import capture
from .errors import (  # noqa: F401 (re-exported)
    BattleTargetChanged,
    InvalidRoundInput,
    RoundError,
    RoundNotFound,
    RoundStateError,
)
from .models import MAX_PLAYERS, Game, GamePlayer, Round, RoundAnswer, RoundQuestion, RoundQuestionOption

ANSWER_TIME = timedelta(seconds=10)
MAX_QUESTIONS_PER_ROUND = 3
# Places each round type decides. Fixed when the round is created, never extended later.
PLACES_NEEDED = {Round.CITY_CAPTURE: 2}

_INTEGER = re.compile(r'[+-]?\d+')
_INT_MIN, _INT_MAX = -(2**31), 2**31 - 1  # what an IntegerField holds on every supported database


def now():
    """The trusted current time. The only clock this module uses, so tests can replace it."""
    return timezone.now()


# --- operations ------------------------------------------------------------------------------


def create_round(game_id, *, round_type=Round.CITY_CAPTURE, rng=None):
    """Create the game's next round as `pending` with a random unused numeric question.

    The time limit does not start yet; see start_round().
    """
    rng = rng or random.SystemRandom()
    if round_type not in PLACES_NEEDED:
        raise InvalidRoundInput(f'Unknown round type {round_type!r}.')

    with transaction.atomic():
        # Lock the game so two simultaneous creations cannot both pass the checks below.
        try:
            game = Game.objects.select_for_update().get(pk=game_id)
        except Game.DoesNotExist:
            raise RoundNotFound(f'Game #{game_id} does not exist.') from None
        _ensure_game_is_active(game)

        players = list(game.players.order_by('player_order'))
        if len(players) != MAX_PLAYERS:
            raise RoundStateError(f'A round needs the game\'s {MAX_PLAYERS} players (game #{game.pk} has {len(players)}).')
        if game.rounds.exclude(status=Round.EVALUATED).exists():
            raise RoundStateError(f'Game #{game.pk} already has an unfinished round.')
        if capture.pending_right(game) is not None:
            raise RoundStateError(f'Game #{game.pk} still has territory picks to make from the last round.')
        free_count = capture.free_territories(game).count()
        if free_count == 0:
            raise RoundStateError(f'Game #{game.pk} has no free territory left: the conquest phase is over.')

        question = _pick_unused_question(game, rng)
        if question is None:
            raise RoundStateError('No unused numeric question is left for this game.')

        number = (game.rounds.aggregate(highest=Max('number'))['highest'] or 0) + 1
        # Fixed before the start: how many places are worth deciding depends on the free territories.
        places_needed = min(PLACES_NEEDED[round_type], capture.places_needed_for(free_count))
        round_ = Round(
            game=game,
            number=number,
            round_type=round_type,
            places_needed=places_needed,
            free_territories=free_count,
            numeric_question=question,
        )
        round_.full_clean()
        round_.save()
        _ask(round_, 1, question, players, first_place=1, last_place=places_needed)
    return round_


def start_round(round_id):
    """Activate a pending round: its first question gets a common start and a 10 s deadline."""
    with transaction.atomic():
        moment = now()
        round_ = _lock_round(round_id)
        if round_.status != Round.PENDING:
            raise RoundStateError(f'Only a pending round can be started (round is {round_.get_status_display()}).')

        round_.status = Round.OPEN
        round_.started_at = moment
        round_.save(update_fields=['status', 'started_at'])

        question = round_.questions.get(sequence=1)
        _activate(question, moment)
    return round_


def submit_answer(round_id, player_id, value, *, rng=None):
    """Record `player_id`'s answer to the round's active question step and return it.

    `value` is a whole number for a numeric step, or the id of one of the step's own options
    (an AnswerOption id) for a choice step. The answer time is taken by the application when the
    attempt is processed, before the deadline check; it is never supplied by the caller. Once
    every participant has answered, the step is evaluated right away (see evaluate_question()).
    """
    moment = now()  # first thing: the trusted time of this attempt
    with transaction.atomic():
        round_ = _lock_round(round_id)
        question = _active_question(round_)

        if not question.participants.filter(pk=player_id).exists():
            if not GamePlayer.objects.filter(pk=player_id).exists():
                raise RoundNotFound(f'Player #{player_id} does not exist.')
            raise RoundStateError(f'Player #{player_id} is not a participant of the current question.')

        if question.kind == RoundQuestion.CHOICE:
            option = parse_option_answer(question, value)
            fields = {'selected_option_id': option.option_id, 'is_correct': option.is_correct}
        else:
            number = parse_integer_answer(value)
            fields = {'numeric_value': number, 'is_correct': number == question.correct_answer}

        if moment >= question.deadline:
            raise RoundStateError('Time is up: the answer arrived at or after the deadline.')
        if question.answers.filter(player_id=player_id).exists():
            raise RoundStateError('This player has already answered this question.')

        try:
            with transaction.atomic():
                answer = RoundAnswer.objects.create(
                    round=round_, question=question, player_id=player_id, submitted_at=moment, **fields
                )
        except IntegrityError:
            # A simultaneous attempt by the same player won the race (unique question+player).
            raise RoundStateError('This player has already answered this question.') from None

        if question.answers.count() == question.participants.count():
            try:
                with transaction.atomic():
                    _evaluate(round_, question, moment, rng or random.SystemRandom())
            except BattleTargetChanged:
                # The answer stays recorded; the result cannot be applied (evaluate_question says why).
                pass
    return answer


def evaluate_question(round_id, *, question_id=None, rng=None):
    """Evaluate the round's active question without a new answer and return the round.

    Allowed once every participant has answered or the deadline has passed; earlier it is
    refused. Either decides the ranking and completes the round, or asks the next question.

    With `question_id` (used by the close_expired_rounds worker) only that question is evaluated:
    if it has meanwhile been closed by someone else, nothing changes and RoundStateError says so.
    """
    with transaction.atomic():
        moment = now()
        round_ = _lock_round(round_id)
        if question_id is None:
            question = _active_question(round_)
        else:
            try:
                question = round_.questions.get(pk=question_id)
            except RoundQuestion.DoesNotExist:
                raise RoundNotFound(f'Question #{question_id} does not exist in round #{round_id}.') from None
        if question.status != RoundQuestion.ACTIVE:
            raise RoundStateError('This question is not active: it has already been closed.')
        answered = question.answers.count()
        if moment < question.deadline and answered < question.participants.count():
            raise RoundStateError('The question is still running: not everyone has answered and time is not up.')
        if not _evaluate(round_, question, moment, rng or random.SystemRandom()):
            raise RoundStateError('This question was closed by another process in the meantime.')
    round_.refresh_from_db()
    return round_


def expired_questions(moment=None):
    """Active questions whose deadline has passed, including any missed while nothing was running."""
    return RoundQuestion.objects.filter(
        status=RoundQuestion.ACTIVE,
        deadline__lte=moment or now(),
    ).order_by('deadline', 'pk')


def round_state(round_id, *, at=None):
    """Everything needed to show a round after a reload, as plain data (read-only).

    The correct answer of a still active question is not included.
    """
    try:
        round_ = Round.objects.select_related('winner__user').get(pk=round_id)
    except Round.DoesNotExist:
        raise RoundNotFound(f'Round #{round_id} does not exist.') from None
    moment = at or now()

    questions = []
    current = None
    for question in round_.questions.prefetch_related('participants__user', 'answers__player__user', 'options'):
        data = {
            'sequence': question.sequence,
            'kind': question.kind,
            'bank_question': question.choice_question_id or question.numeric_question_id,
            'status': question.status,
            'text': question.text,
            'options': [{'id': option.option_id, 'text': option.text} for option in question.options.all()],
            'places': [question.first_place, question.last_place],
            'participants': [player.pk for player in question.participants.all()],
            'started_at': question.started_at,
            'deadline': question.deadline,
            'answered': sorted(answer.player_id for answer in question.answers.all()),
        }
        if question.status == RoundQuestion.ACTIVE:
            remaining = max(question.deadline - moment, timedelta(0))
            data['remaining_seconds'] = remaining.total_seconds()
            current = data
        else:
            data['correct_answer'] = (
                question.correct_answer
                if question.kind == RoundQuestion.NUMERIC
                else next((o.option_id for o in question.options.all() if o.is_correct), None)
            )
            data['answers'] = [
                {
                    'player': answer.player_id,
                    'value': answer.numeric_value if question.kind == RoundQuestion.NUMERIC else answer.selected_option_id,
                    'is_correct': answer.is_correct,
                    'submitted_at': answer.submitted_at,
                }
                for answer in question.answers.all()
            ]
        questions.append(data)

    return {
        'round': round_.pk,
        'game': round_.game_id,
        'number': round_.number,
        'type': round_.round_type,
        'attacker': round_.attacker_id,
        'defender': round_.defender_id,
        'target': round_.target_id,
        'status': round_.status,
        'places_needed': round_.places_needed,
        'started_at': round_.started_at,
        'completed_at': round_.finished_at,
        'winner': round_.winner_id,
        'unresolved_reason': round_.unresolved_reason,
        'ranking': [{'place': answer.place, 'player': answer.player_id} for answer in round_.ranking()],
        'current_question': current,
        'questions': questions,
    }


def parse_integer_answer(value):
    """Return `value` as an int, or raise InvalidRoundInput. Never truncates a fraction."""
    if isinstance(value, bool):
        raise InvalidRoundInput('The answer must be a whole number.')
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and _INTEGER.fullmatch(value.strip()):
        number = int(value.strip())
    else:
        raise InvalidRoundInput(f'The answer must be a whole number (got {value!r}).')
    if not _INT_MIN <= number <= _INT_MAX:
        raise InvalidRoundInput('The answer is out of range.')
    return number


def parse_option_answer(question, value):
    """Return the step's own option chosen by `value` (an AnswerOption id), or raise InvalidRoundInput.

    Only the four options copied into this step are accepted: an existing option of another
    question is just as invalid as a made-up id.
    """
    try:
        option_id = parse_integer_answer(value)
    except InvalidRoundInput:
        raise InvalidRoundInput(f'The answer must be the id of one of the options (got {value!r}).') from None
    option = question.options.filter(option_id=option_id).first()
    if option is None:
        raise InvalidRoundInput(f'Option #{option_id} is not one of the options of this question.')
    return option


# --- internals -------------------------------------------------------------------------------


def _ensure_game_is_active(game):
    if game.status != Game.IN_PROGRESS:
        raise RoundStateError(f'Game #{game.pk} is not active (it is {game.get_status_display()}).')


def _lock_round(round_id):
    """Lock and return a round of an active game that this module runs (city capture or battle)."""
    try:
        round_ = Round.objects.select_for_update().select_related('game').get(pk=round_id)
    except Round.DoesNotExist:
        raise RoundNotFound(f'Round #{round_id} does not exist.') from None
    if round_.round_type not in Round.MANAGED_TYPES:
        raise RoundStateError(f'Round #{round_.pk} is not run by the round service.')
    _ensure_game_is_active(round_.game)
    return round_


def _active_question(round_):
    if round_.status == Round.PENDING:
        raise RoundStateError('The round has not started yet.')
    if round_.status != Round.OPEN:
        raise RoundStateError('The round is already completed.')
    return round_.questions.get(status=RoundQuestion.ACTIVE)


def _used_question_ids(game, kind=RoundQuestion.NUMERIC):
    """Ids of `kind` bank questions already used in the game. Each model has its own id space."""
    field = 'numeric_question_id' if kind == RoundQuestion.NUMERIC else 'choice_question_id'
    used = set(RoundQuestion.objects.filter(round__game=game).values_list(field, flat=True))
    used |= set(game.rounds.values_list(field, flat=True))
    used.discard(None)
    return used


def _pick_unused_question(game, rng, kind=RoundQuestion.NUMERIC):
    """A random valid bank question of `kind` not used in this game yet, or None."""
    if kind == RoundQuestion.NUMERIC:
        queryset = NumericQuestion.objects.all()
        model = NumericQuestion
    else:
        # Valid: exactly four options, exactly one of them correct.
        queryset = ChoiceQuestion.objects.annotate(
            option_count=Count('answer_options'),
            correct_count=Count('answer_options', filter=Q(answer_options__is_correct=True)),
        ).filter(option_count=4, correct_count=1)
        model = ChoiceQuestion
    candidates = [
        pk
        for pk, text in queryset.exclude(pk__in=_used_question_ids(game, kind)).order_by('pk').values_list('pk', 'text')
        if text.strip()
    ]
    if not candidates:
        return None
    return model.objects.get(pk=rng.choice(candidates))


def _ask(round_, sequence, bank_question, players, *, first_place, last_place, start_at=None):
    """Create a question step from `bank_question` (numeric or choice), copying what is asked."""
    if isinstance(bank_question, ChoiceQuestion):
        question = RoundQuestion.objects.create(
            round=round_,
            sequence=sequence,
            kind=RoundQuestion.CHOICE,
            choice_question=bank_question,
            text=bank_question.text,
            first_place=first_place,
            last_place=last_place,
        )
        RoundQuestionOption.objects.bulk_create(
            RoundQuestionOption(
                question=question, option=option, position=position, text=option.text, is_correct=option.is_correct
            )
            for position, option in enumerate(bank_question.answer_options.order_by('pk'), start=1)
        )
    else:
        question = RoundQuestion.objects.create(
            round=round_,
            sequence=sequence,
            kind=RoundQuestion.NUMERIC,
            numeric_question=bank_question,
            text=bank_question.text,
            correct_answer=bank_question.correct_answer,
            first_place=first_place,
            last_place=last_place,
        )
    question.participants.set(players)
    if start_at is not None:
        _activate(question, start_at)
    return question


def _activate(question, moment):
    question.status = RoundQuestion.ACTIVE
    question.started_at = moment
    question.deadline = moment + ANSWER_TIME
    question.save(update_fields=['status', 'started_at', 'deadline'])


def _evaluate(round_, question, moment, rng):
    """Close `question`, record the places it decides, then ask the next question or complete the round.

    Must run inside the caller's transaction. Returns False and changes nothing if the question
    was no longer active, i.e. someone else has already evaluated it.
    """
    # Conditional change: only switches active -> completed if it is active at this very moment
    # in the database. 0 rows means another process got there first, so do nothing at all.
    closed = RoundQuestion.objects.filter(pk=question.pk, status=RoundQuestion.ACTIVE).update(
        status=RoundQuestion.COMPLETED, completed_at=moment
    )
    if closed == 0:
        return False
    question.status, question.completed_at = RoundQuestion.COMPLETED, moment

    if question.kind == RoundQuestion.CHOICE:
        return _evaluate_choice(round_, question, moment, rng)

    def key(answer):
        # Closer first; at equal closeness, faster first. Both sides of the answer count the same.
        return abs(answer.numeric_value - question.correct_answer), answer.submitted_at

    answers = sorted(question.answers.select_related('player'), key=key)
    place, index, tied = question.first_place, 0, []
    while place <= question.last_place and index < len(answers):
        same = [answer for answer in answers[index:] if key(answer) == key(answers[index])]
        if len(same) > 1:
            tied = same  # equal closeness and equal time: only a new question can split them
            break
        answers[index].place = place
        answers[index].save(update_fields=['place'])
        place += 1
        index += 1

    if not answers:
        # Nobody answered: same group, same places, new question.
        group, places = list(question.participants.all()), (question.first_place, question.last_place)
    elif tied:
        group, places = [answer.player for answer in tied], (place, min(question.last_place, place + len(tied) - 1))
    else:
        # Done. Places left over here had too few answers and are not awarded.
        _complete(round_, moment, reason='')
        return True

    # Once numeric, a round stays numeric: the next step (if any) is numeric too.
    return _next_step(round_, question, moment, rng, RoundQuestion.NUMERIC, group, places)


def _evaluate_choice(round_, question, moment, rng):
    """Choice step: one correct answer wins; two -> numeric step; none -> another choice step.

    A wrong answer counts the same as no answer, and correct answers are not compared by speed.
    """
    correct = [answer for answer in question.answers.select_related('player') if answer.is_correct]
    places = (question.first_place, question.last_place)
    if len(correct) == 1:
        correct[0].place = question.first_place
        correct[0].save(update_fields=['place'])
        _complete(round_, moment, reason='')
        return True
    if correct:
        return _next_step(round_, question, moment, rng, RoundQuestion.NUMERIC, [a.player for a in correct], places)
    return _next_step(round_, question, moment, rng, RoundQuestion.CHOICE, list(question.participants.all()), places)


def _next_step(round_, question, moment, rng, kind, group, places):
    """Ask the next step if the limit allows it and the bank has one; otherwise complete with the reason."""
    if question.sequence >= MAX_QUESTIONS_PER_ROUND:
        _complete(round_, moment, reason=Round.QUESTION_LIMIT)  # takes precedence over an empty bank
        return True
    bank_question = _pick_unused_question(round_.game, rng, kind)
    if bank_question is None:
        _complete(round_, moment, reason=Round.BANK_EXHAUSTED)
        return True
    _ask(
        round_,
        question.sequence + 1,
        bank_question,
        group,
        first_place=places[0],
        last_place=places[1],
        start_at=moment,
    )
    return True


def _complete(round_, moment, *, reason):
    placed = dict(round_.answers.filter(place__isnull=False).values_list('place', 'player_id'))
    if not reason and any(place not in placed for place in range(1, round_.places_needed + 1)):
        reason = Round.NOT_ENOUGH_ANSWERS
    round_.status = Round.EVALUATED
    round_.finished_at = moment
    round_.winner_id = placed.get(1)
    round_.unresolved_reason = reason if len(placed) < round_.places_needed else ''
    round_.full_clean()
    round_.save(update_fields=['status', 'finished_at', 'winner', 'unresolved_reason'])
    # Same transaction: the final ranking and every consequence are saved together, or not at all.
    if round_.round_type == Round.CITY_CAPTURE:
        capture.record_round_result(round_)
    else:
        from .battles import apply_battle_result  # battles builds on this module

        apply_battle_result(round_)
