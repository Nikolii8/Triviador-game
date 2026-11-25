from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from games.battles import attackable_territories, create_battle
from games.errors import RoundError, RoundNotFound
from games.models import GamePlayer
from territories.models import Territory


class Command(BaseCommand):
    help = (
        'Create a battle (M08): <game id> <attacker username> <target slug>. Without a target, list what '
        'the attacker may attack. Then run it with numeric_round start / answer / evaluate.'
    )

    def add_arguments(self, parser):
        parser.add_argument('game_id', type=int)
        parser.add_argument('attacker')
        parser.add_argument('target', nargs='?', help='Territory slug, e.g. sredets.')

    def handle(self, *args, game_id, attacker, target, **options):
        try:
            player = GamePlayer.objects.filter(game_id=game_id, user__username=attacker).first()
            if player is None:
                raise RoundNotFound(f'{attacker} does not play in game #{game_id}.')
            if target is None:
                names = [f'{t.name} ({t.slug}, {t.owner.user})' for t in attackable_territories(player).order_by('name')]
                self.stdout.write(f'{attacker} may attack: ' + (', '.join(names) or 'nothing'))
                return
            territory = Territory.objects.filter(game_id=game_id, slug=target).first()
            round_ = create_battle(game_id, player.pk, territory.pk if territory else 0)
        except RoundError as exc:
            raise CommandError(f'[{exc.kind}] {" ".join(exc.messages)}') from None
        self.stdout.write(self.style.SUCCESS(f'Round #{round_.pk} ({round_.round_type}) created.'))
        call_command('numeric_round', 'show', str(round_.pk), stdout=self.stdout)
