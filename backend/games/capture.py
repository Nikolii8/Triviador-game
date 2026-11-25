"""Territory capture and scoring after city capture rounds (M07).

When a city capture round is evaluated (games.rounds), record_round_result() runs in the same
transaction: it awards the win and creates the capture rights. Each pick is then a separate
operation, choose_territory(), that changes the owner, raises Territory.score, credits the capture
points and uses one right, all together or not at all.

Rights per round (the free count is fixed when the round is created):

    free territories | first | second (if decided)
    3 or more        |   2   |   1
    2                |   2   |   0
    1                |   1   |   0
"""

from django.db import transaction
from django.db.models import F

from territories.models import Territory

from .errors import InvalidRoundInput, RoundNotFound, RoundStateError
from .models import CaptureRight, Game, GamePlayer, Round, ScoreEntry
from .scoring import ACTIVE_SYSTEM

FIRST_PICKS = 2
SECOND_PICKS = 1


def places_needed_for(free_count):
    """With one or two free territories only the first can pick, so second place is not needed."""
    return 2 if free_count > FIRST_PICKS else 1


def free_territories(game):
    return Territory.objects.filter(game=game, owner__isnull=True)


def pending_right(game):
    """The right whose turn it is now (from the last evaluated city capture), or None."""
    last = (
        game.rounds.filter(round_type=Round.CITY_CAPTURE, status=Round.EVALUATED).order_by('-number').first()
    )
    if last is None:
        return None
    return last.capture_rights.filter(used__lt=F('allowed')).order_by('turn').select_related('player').first()


def allowed_territories(player):
    """Free territories `player` may pick now: free neighbors of anything they own (capital included).

    Only if no free territory borders any of their territories may they pick any free one.
    """
    free = free_territories(player.game_id)
    adjacent = free.filter(neighbors__owner=player).distinct()
    return adjacent if adjacent.exists() else free


def record_round_result(round_, system=ACTIVE_SYSTEM):
    """Award the win and create the capture rights of an evaluated round. Call once, inside its transaction."""
    if round_.winner_id is None:
        return  # no winner: no award, no rights; the free territories wait for the next round
    _credit(
        round_.winner,
        round_,
        ScoreEntry.ROUND_WIN,
        *system.win(),
    )

    first_allowed = min(FIRST_PICKS, round_.free_territories)
    CaptureRight.objects.create(round=round_, player=round_.winner, turn=1, allowed=first_allowed)
    second = round_.answers.filter(place=2).values_list('player_id', flat=True).first()
    if second is not None and round_.free_territories > FIRST_PICKS:
        CaptureRight.objects.create(round=round_, player_id=second, turn=2, allowed=SECOND_PICKS)


def choose_territory(game_id, player_id, territory_id, *, system=ACTIVE_SYSTEM):
    """`player_id` picks the free territory `territory_id` using their current capture right.

    Returns the ScoreEntry with the before/after values. A refused pick changes nothing and
    does not use the right.
    """
    with transaction.atomic():
        # The game row lock serializes every pick of this game (see README for SQLite).
        try:
            game = Game.objects.select_for_update().get(pk=game_id)
        except Game.DoesNotExist:
            raise RoundNotFound(f'Game #{game_id} does not exist.') from None
        if game.status != Game.IN_PROGRESS:
            raise RoundStateError(f'Game #{game.pk} is not active (it is {game.get_status_display()}).')

        try:
            player = GamePlayer.objects.select_for_update().get(pk=player_id)
        except GamePlayer.DoesNotExist:
            raise RoundNotFound(f'Player #{player_id} does not exist.') from None
        try:
            territory = Territory.objects.select_for_update().get(pk=territory_id)
        except Territory.DoesNotExist:
            raise RoundNotFound(f'Territory #{territory_id} does not exist.') from None

        right = pending_right(game)
        if right is None:
            raise RoundStateError('Nobody has a capture right now.')
        if player.pk != right.player_id:
            if player.game_id != game.pk:
                raise RoundStateError(f'Player #{player.pk} does not play in game #{game.pk}.')
            raise RoundStateError(f'It is not this player\'s turn to pick (waiting for {right.player.user}).')

        if territory.game_id != game.pk:
            raise InvalidRoundInput(f'{territory.name} is not a territory of game #{game.pk}.')
        if territory.owner_id is not None:
            raise RoundStateError(f'{territory.name} is already taken.')
        if not allowed_territories(player).filter(pk=territory.pk).exists():
            raise InvalidRoundInput(f'{territory.name} does not border any of this player\'s territories.')

        points, territory_after, calculation = system.capture(right.turn, territory.score)
        # Conditional changes: they only apply if the territory is still free and the right still
        # has a pick at this moment, so a concurrent pick that got here first makes this one a no-op.
        taken = Territory.objects.filter(pk=territory.pk, owner__isnull=True).update(
            owner=player, score=territory_after
        )
        if taken == 0:
            raise RoundStateError(f'{territory.name} is already taken.')
        spent = CaptureRight.objects.filter(pk=right.pk, used__lt=F('allowed')).update(used=F('used') + 1)
        if spent == 0:
            raise RoundStateError('This capture right has already been used up.')  # rolls back the owner change

        territory_before = territory.score
        territory.owner, territory.score = player, territory_after
        return _credit(
            player,
            right.round,
            ScoreEntry.CAPTURE,
            points,
            calculation,
            right=right,
            territory=territory,
            territory_before=territory_before,
            territory_after=territory_after,
        )


def capture_state(game):
    """Plain data for showing the conquest phase: free territories, rights and scores."""
    right = pending_right(game)
    return {
        'free_territories': sorted(free_territories(game).values_list('name', flat=True)),
        'phase_finished': not free_territories(game).exists(),
        'turn': (
            {
                'player': right.player_id,
                'remaining': right.remaining,
                'allowed': sorted(allowed_territories(right.player).values_list('name', flat=True)),
            }
            if right
            else None
        ),
        'scores': dict(game.players.values_list('pk', 'score')),
    }


def _credit(player, round_, kind, points, calculation, *, right=None, territory=None, territory_before=None,
            territory_after=None):
    """Add `points` to the player's score and record the change with its before/after values."""
    player.refresh_from_db(fields=['score'])
    before = player.score
    GamePlayer.objects.filter(pk=player.pk).update(score=F('score') + points)
    player.score = before + points
    return ScoreEntry.objects.create(
        player=player,
        round=round_,
        kind=kind,
        right=right,
        territory=territory,
        points=points,
        player_score_before=before,
        player_score_after=player.score,
        territory_score_before=territory_before,
        territory_score_after=territory_after,
        calculation=calculation,
    )
