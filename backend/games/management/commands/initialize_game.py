from django.core.management.base import BaseCommand, CommandError

from games.initialization import GameInitializationError, initialize_game
from territories.models import Capital


class Command(BaseCommand):
    help = 'Initialize a waiting game: build its map, assign the three capitals and start it.'

    def add_arguments(self, parser):
        parser.add_argument('game_id', type=int)

    def handle(self, *args, game_id, **options):
        try:
            game = initialize_game(game_id)
        except GameInitializationError as exc:
            raise CommandError(' '.join(exc.messages)) from None

        self.stdout.write(self.style.SUCCESS(f'Game #{game.pk} initialized: {game.territories.count()} territories.'))
        for capital in Capital.objects.filter(player__game=game).select_related('player__user', 'territory'):
            self.stdout.write(f'  Player {capital.player.player_order} ({capital.player.user}): capital {capital.territory.name}')
