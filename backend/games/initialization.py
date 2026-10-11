"""Game initialization (M05): build a waiting game's map, assign capitals and start it.

Everything happens in one transaction, so a game is either fully initialized
(all territories, borders and three capitals, status in progress) or left
exactly as it was.
"""

import random
from itertools import combinations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from territories.map_definition import PLAYERS_PER_GAME, PROJECT_MAP, validate_game_map, validate_map_definition
from territories.models import Adjacency, Capital, Territory

from .models import Game

INITIAL_CAPITAL_HEALTH = 3


class GameInitializationError(ValidationError):
    """Raised when a game cannot be initialized. Nothing has been changed when it is raised."""


def capital_candidates(definition):
    """Every set of three pairwise non-adjacent territories (as slugs), in a fixed order."""
    graph = definition.neighbors()
    return [
        triple
        for triple in combinations(sorted(graph), PLAYERS_PER_GAME)
        if all(b not in graph[a] for a, b in combinations(triple, 2))
    ]


def initialize_game(game_id, *, rng=None, definition=PROJECT_MAP):
    """Initialize the waiting game `game_id` and return it, or raise GameInitializationError.

    `rng` is any object with `choice` and `sample` (e.g. random.Random(seed)), so tests
    can control the otherwise random capital choice. `definition` defaults to the project map.
    """
    rng = rng or random.SystemRandom()

    with transaction.atomic():
        # Lock the game row so two simultaneous attempts are serialized: the second one
        # waits, then sees the game is no longer waiting. (On SQLite see README: the
        # whole database is locked instead, via transaction_mode IMMEDIATE.)
        try:
            game = Game.objects.select_for_update().get(pk=game_id)
        except Game.DoesNotExist:
            raise GameInitializationError(f'Game #{game_id} does not exist.') from None

        if game.status != Game.WAITING:
            raise GameInitializationError(
                f'Only a waiting game can be initialized (game #{game.pk} is {game.get_status_display()}).'
            )

        players = list(game.players.select_for_update().order_by('player_order'))
        if len(players) != PLAYERS_PER_GAME:
            raise GameInitializationError(
                f'A game needs exactly {PLAYERS_PER_GAME} players to start (game #{game.pk} has {len(players)}).'
            )

        if Territory.objects.filter(game=game).exists() or Capital.objects.filter(player__game=game).exists():
            raise GameInitializationError(
                f'Game #{game.pk} already has map records; a partially prepared game is not repaired automatically.'
            )

        try:
            validate_map_definition(definition)
        except ValidationError as exc:
            raise GameInitializationError(['The map definition is invalid.', *exc.messages]) from None

        candidates = capital_candidates(definition)
        if not candidates:
            raise GameInitializationError('No three pairwise non-adjacent territories are available for capitals.')

        # Random triple, then a random order so who gets which capital is random too.
        capital_slugs = rng.sample(rng.choice(candidates), PLAYERS_PER_GAME)

        territories = {
            slug: Territory(game=game, name=name, slug=slug) for slug, name in definition.territories
        }
        for player, slug in zip(players, capital_slugs):
            territories[slug].owner = player
        Territory.objects.bulk_create(territories.values())

        Adjacency.objects.bulk_create(
            Adjacency(from_territory=territories[source], to_territory=territories[target])
            for a, b in definition.adjacencies
            for source, target in ((a, b), (b, a))
        )

        for player, slug in zip(players, capital_slugs):
            capital = Capital(territory=territories[slug], player=player, health=INITIAL_CAPITAL_HEALTH)
            capital.full_clean()
            capital.save()

        game.status = Game.IN_PROGRESS
        game.started_at = timezone.now()
        game.full_clean()
        game.save(update_fields=['status', 'started_at'])

        # Final consistency check inside the transaction: any mismatch rolls everything back.
        try:
            validate_game_map(game, definition)
        except ValidationError as exc:
            raise GameInitializationError(['The initialized map is inconsistent.', *exc.messages]) from None

    return game
