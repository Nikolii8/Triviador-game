from django.core.management.base import BaseCommand, CommandError

from games.capture import capture_state, choose_territory
from games.errors import RoundError, RoundNotFound
from games.models import Game, GamePlayer
from territories.models import Territory


class Command(BaseCommand):
    help = 'Territory picks after a city capture round (M07): show whose turn it is, or make a pick.'

    def add_arguments(self, parser):
        actions = parser.add_subparsers(dest='action', required=True)
        actions.add_parser('show', help='Free territories, whose turn it is and the scores.').add_argument(
            'game_id', type=int
        )
        pick = actions.add_parser('pick', help='Pick a free territory for a player.')
        pick.add_argument('game_id', type=int)
        pick.add_argument('username')
        pick.add_argument('territory', help='Territory slug, e.g. sredets.')

    def handle(self, *args, action, game_id, **options):
        try:
            game = Game.objects.filter(pk=game_id).first()
            if game is None:
                raise RoundNotFound(f'Game #{game_id} does not exist.')
            if action == 'pick':
                player = GamePlayer.objects.filter(game=game, user__username=options['username']).first()
                territory = Territory.objects.filter(game=game, slug=options['territory']).first()
                entry = choose_territory(game.pk, player.pk if player else 0, territory.pk if territory else 0)
                self.stdout.write(
                    self.style.SUCCESS(
                        f'{entry.player.user} took {entry.territory.name}: '
                        f'{entry.player_score_before} + {entry.points} = {entry.player_score_after} '
                        f'({entry.calculation})'
                    )
                )
        except RoundError as exc:
            raise CommandError(f'[{exc.kind}] {" ".join(exc.messages)}') from None
        self._show(game)

    def _show(self, game):
        state = capture_state(game)
        names = dict(game.players.values_list('pk', 'user__username'))
        if state['phase_finished']:
            self.stdout.write('Conquest phase finished: every territory has an owner.')
        else:
            self.stdout.write(f"Free territories ({len(state['free_territories'])}): {', '.join(state['free_territories'])}")
        turn = state['turn']
        if turn:
            self.stdout.write(
                f"{names[turn['player']]}'s turn, {turn['remaining']} pick(s) left; "
                f"allowed: {', '.join(turn['allowed'])}"
            )
        else:
            self.stdout.write('No picks pending.')
        self.stdout.write('Scores: ' + ', '.join(f'{names[pk]} {score}' for pk, score in state['scores'].items()))
