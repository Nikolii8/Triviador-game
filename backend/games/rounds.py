"""Numeric rounds (M06): create, start, answer and evaluate a `city_capture` round.

Service layer: every operation here runs in one transaction and either applies all of its
changes or raises a RoundError and changes nothing. The error class tells the caller why:

- InvalidRoundInput  - the request itself is malformed (e.g. a non-integer answer);
- RoundStateError    - the request is well-formed but not allowed right now
                       (round not started, time is up, already answered, ...);
- RoundNotFound      - the game, round or player does not exist.

Round lifecycle (stored statuses in brackets): pending -> active [open] -> completed [evaluated].
A round asks 1 to 3 numeric questions (RoundQuestion). The first goes to all three players and
decides places 1..places_needed. A full tie for a needed place, or no answers at all, asks a new
question to just that group; places already decided are kept.
"""

import random
import re
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from questions.models import NumericQuestion

from .models import MAX_PLAYERS, Game, GamePlayer, Round, RoundAnswer, RoundQuestion

ANSWER_TIME = timedelta(seconds=10)
MAX_QUESTIONS_PER_ROUND = 3
# Places each round type decides. Fixed when the round is created, never extended later.
PLACES_NEEDED = {Round.CITY_CAPTURE: 2}

_INTEGER = re.compile(r'[+-]?\d+')
_INT_MIN, _INT_MAX = -(2**31), 2**31 - 1  # what an IntegerField holds on every supported database


def now():
    """The trusted current time. The only clock this module uses, so tests can replace it."""
    return timezone.now()


class RoundError(ValidationError):
    """A refused round operation. Nothing was changed when it is raised."""

    kind = 'error'


class InvalidRoundInput(RoundError):
    kind = 'invalid_input'


class RoundStateError(RoundError):
    kind = 'invalid_state'


class RoundNotFound(RoundError):
    kind = 'not_found'


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

        question = _pick_unused_question(game, rng)
        if question is None:
            raise RoundStateError('No unused numeric question is left for this game.')

        number = (game.rounds.aggregate(highest=Max('number'))['highest'] or 0) + 1
        places_needed = PLACES_NEEDED[round_type]
        round_ = Round(
            game=game,
            number=number,
            round_type=round_type,
            places_needed=places_needed,
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
    """Record `player_id`'s integer answer to the round's active question and return it.

    The answer time is taken by the application when the attempt is processed, before the
    deadline check; it is never supplied by the caller. Once every participant has answered,
    the question is evaluated right away (see evaluate_question()).
    """
    moment = now()  # first thing: the trusted time of this attempt
    with transaction.atomic():
        round_ = _lock_round(round_id)
        question = _active_question(round_)

        if not question.participants.filter(pk=player_id).exists():
            if not GamePlayer.objects.filter(pk=player_id).exists():
                raise RoundNotFound(f'Player #{player_id} does not exist.')
            raise RoundStateError(f'Player #{player_id} is not a participant of the current question.')

        number = parse_integer_answer(value)

        if moment >= question.deadline:
            raise RoundStateError('Time is up: the answer arrived at or after the deadline.')
        if question.answers.filter(player_id=player_id).exists():
            raise RoundStateError('This player has already answered this question.')

        try:
            with transaction.atomic():
                answer = RoundAnswer.objects.create(
                    round=round_,
                    question=question,
                    player_id=player_id,
                    numeric_value=number,
                    is_correct=number == question.correct_answer,
                    submitted_at=moment,
                )
        except IntegrityError:
            # A simultaneous attempt by the same player won the race (unique question+player).
            raise RoundStateError('This player has already answered this question.') from None

        if question.answers.count() == question.participants.count():
            _evaluate(round_, question, moment, rng or random.SystemRandom())
    return answer


def evaluate_question(round_id, *, rng=None):
    """Evaluate the round's active question without a new answer and return the round.

    Allowed once every participant has answered or the deadline has passed; earlier it is
    refused. Either decides the ranking and completes the round, or asks the next question.
    """
    with transaction.atomic():
        moment = now()
        round_ = _lock_round(round_id)
        question = _active_question(round_)
        answered = question.answers.count()
        if moment < question.deadline and answered < question.participants.count():
            raise RoundStateError('The question is still running: not everyone has answered and time is not up.')
        _evaluate(round_, question, moment, rng or random.SystemRandom())
    round_.refresh_from_db()
    return round_


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
    for question in round_.questions.prefetch_related('participants__user', 'answers__player__user'):
        data = {
            'sequence': question.sequence,
            'status': question.status,
            'text': question.text,
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
            data['correct_answer'] = question.correct_answer
            data['answers'] = [
                {'player': answer.player_id, 'value': answer.numeric_value, 'submitted_at': answer.submitted_at}
                for answer in question.answers.all()
            ]
        questions.append(data)

    return {
        'round': round_.pk,
        'game': round_.game_id,
        'number': round_.number,
        'type': round_.round_type,
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


# --- internals -------------------------------------------------------------------------------


def _ensure_game_is_active(game):
    if game.status != Game.IN_PROGRESS:
        raise RoundStateError(f'Game #{game.pk} is not active (it is {game.get_status_display()}).')


def _lock_round(round_id):
    """Lock and return a city capture round of an active game."""
    try:
        round_ = Round.objects.select_for_update().select_related('game').get(pk=round_id)
    except Round.DoesNotExist:
        raise RoundNotFound(f'Round #{round_id} does not exist.') from None
    if round_.round_type != Round.CITY_CAPTURE:
        raise RoundStateError(f'Round #{round_.pk} is not a numeric city capture round.')
    _ensure_game_is_active(round_.game)
    return round_


def _active_question(round_):
    if round_.status == Round.PENDING:
        raise RoundStateError('The round has not started yet.')
    if round_.status != Round.OPEN:
        raise RoundStateError('The round is already completed.')
    return round_.questions.get(status=RoundQuestion.ACTIVE)


def _used_question_ids(game):
    used = set(RoundQuestion.objects.filter(round__game=game).values_list('numeric_question_id', flat=True))
    used |= set(game.rounds.filter(numeric_question__isnull=False).values_list('numeric_question_id', flat=True))
    return used


def _pick_unused_question(game, rng):
    candidates = [
        pk
        for pk, text in NumericQuestion.objects.exclude(pk__in=_used_question_ids(game))
        .order_by('pk')
        .values_list('pk', 'text')
        if text.strip()
    ]
    if not candidates:
        return None
    return NumericQuestion.objects.get(pk=rng.choice(candidates))


def _ask(round_, sequence, bank_question, players, *, first_place, last_place, start_at=None):
    question = RoundQuestion.objects.create(
        round=round_,
        sequence=sequence,
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
    """Close `question`, record the places it decides, then ask the next question or complete the round."""
    question.status = RoundQuestion.COMPLETED
    question.completed_at = moment
    question.save(update_fields=['status', 'completed_at'])

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
        return

    if question.sequence >= MAX_QUESTIONS_PER_ROUND:
        _complete(round_, moment, reason=Round.QUESTION_LIMIT)
        return
    bank_question = _pick_unused_question(round_.game, rng)
    if bank_question is None:
        _complete(round_, moment, reason=Round.BANK_EXHAUSTED)
        return
    _ask(
        round_,
        question.sequence + 1,
        bank_question,
        group,
        first_place=places[0],
        last_place=places[1],
        start_at=moment,
    )


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
