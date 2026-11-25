from django.core.management.base import BaseCommand, CommandError

from games.models import GamePlayer, Round
from games.rounds import RoundError, create_round, evaluate_question, round_state, start_round, submit_answer


class Command(BaseCommand):
    help = 'Run a round step by step: city capture (M06) or battle (M08). Choice answers are option ids.'

    def add_arguments(self, parser):
        actions = parser.add_subparsers(dest='action', required=True)
        actions.add_parser('create', help='Create the next round of an active game.').add_argument('game_id', type=int)
        actions.add_parser('start', help='Start a pending round (10 s per question).').add_argument('round_id', type=int)
        answer = actions.add_parser('answer', help="Submit a player's answer.")
        answer.add_argument('round_id', type=int)
        answer.add_argument('username')
        answer.add_argument('value', help='A whole number (numeric question) or an option id (choice question).')
        actions.add_parser('evaluate', help='Evaluate the active question.').add_argument('round_id', type=int)
        actions.add_parser('show', help='Show the round state.').add_argument('round_id', type=int)

    def handle(self, *args, action, **options):
        try:
            if action == 'create':
                round_ = create_round(options['game_id'])
                self.stdout.write(self.style.SUCCESS(f'Round #{round_.pk} (number {round_.number}) created.'))
                round_id = round_.pk
            elif action == 'start':
                start_round(options['round_id'])
                round_id = options['round_id']
            elif action == 'answer':
                round_id = options['round_id']
                player = GamePlayer.objects.filter(
                    game__rounds=round_id, user__username=options['username']
                ).first()
                submit_answer(round_id, player.pk if player else 0, options['value'])
                self.stdout.write(self.style.SUCCESS('Answer accepted.'))
            elif action == 'evaluate':
                evaluate_question(options['round_id'])
                round_id = options['round_id']
            else:
                round_id = options['round_id']
        except RoundError as exc:
            # The error kind (invalid_input / invalid_state / not_found) tells why it was refused.
            raise CommandError(f'[{exc.kind}] {" ".join(exc.messages)}') from None

        self._show(round_state(round_id))

    def _show(self, state):
        names = dict(GamePlayer.objects.filter(game=state['game']).values_list('pk', 'user__username'))
        if state['attacker']:
            self.stdout.write(
                f"{state['type']}: {names[state['attacker']]} attacks {names[state['defender']]}"
                f" (territory #{state['target']})"
            )
        self.stdout.write(
            f"Round {state['number']} of game #{state['game']}: {state['status']}"
            + (f" ({dict(Round.UNRESOLVED_REASON_CHOICES)[state['unresolved_reason']]})" if state['unresolved_reason'] else '')
        )
        for question in state['questions']:
            players = ', '.join(names[pk] for pk in question['participants'])
            line = (
                f"  Q{question['sequence']} {question['kind']} [{question['status']}] {question['text']}"
                f"  players: {players}"
            )
            if 'remaining_seconds' in question:
                line += f"  ({question['remaining_seconds']:.1f} s left)"
            self.stdout.write(line)
            options = {option['id']: option['text'] for option in question['options']}
            if 'remaining_seconds' in question:
                for option_id, text in options.items():
                    self.stdout.write(f'      option {option_id}: {text}')
            for answer in question.get('answers', []):
                if question['kind'] == 'choice':
                    verdict = 'correct' if answer['is_correct'] else 'wrong'
                    self.stdout.write(f"      {names[answer['player']]}: {options[answer['value']]} ({verdict})")
                else:
                    self.stdout.write(
                        f"      {names[answer['player']]}: {answer['value']}  (correct {question['correct_answer']})"
                    )
        for entry in state['ranking']:
            self.stdout.write(f"  Place {entry['place']}: {names[entry['player']]}")
        if state['status'] == Round.EVALUATED:
            self.stdout.write(f"  Winner: {names.get(state['winner'], '-')}")
