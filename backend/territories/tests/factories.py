"""Test helpers that write map records directly.

This is test setup only, not game initialization: M04 has no code that builds
a game's map automatically (that arrives in M05).
"""

from django.contrib.auth import get_user_model

from games.models import Game, GamePlayer
from territories.map_definition import PROJECT_MAP
from territories.models import Territory

User = get_user_model()


def make_user(username):
    return User.objects.create_user(username=username, email=f'{username}@example.com', password=None)


def make_game(name='host'):
    return Game.objects.create(created_by=make_user(name))


def add_player(game, username, order):
    return GamePlayer.objects.create(game=game, user=make_user(username), player_order=order)


def build_map(game, definition=PROJECT_MAP):
    """Create the game's territories and borders exactly as the definition says; return them by slug."""
    territories = {
        slug: Territory.objects.create(game=game, name=name, slug=slug) for slug, name in definition.territories
    }
    for a, b in definition.adjacencies:
        territories[a].neighbors.add(territories[b])
    return territories
