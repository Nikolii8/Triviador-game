import time

from django.core.management.base import BaseCommand, CommandError

from games import rounds
from games.rounds import RoundError, evaluate_question, expired_questions


class Command(BaseCommand):
    help = 'Evaluate every active round question whose deadline has passed (once, or repeatedly with --loop).'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', help='Keep checking until stopped with Ctrl+C.')
        parser.add_argument('--interval', type=float, default=5, help='Seconds between checks with --loop (default 5).')

    def handle(self, *args, loop, interval, **options):
        if interval <= 0:
            raise CommandError('--interval must be a positive number of seconds.')
        if not loop:
            self.check_once()
            return

        self.stdout.write(f'Checking every {interval:g} s. Press Ctrl+C to stop.')
        try:
            while True:
                self.check_once()
                time.sleep(interval)
        except KeyboardInterrupt:
            # An interrupted evaluation is rolled back by its transaction, so stopping is always safe.
            self.stdout.write('Stopped.')

    def check_once(self):
        moment = rounds.now()  # django.utils.timezone.now(), via the one clock tests can replace
        expired = list(expired_questions(moment).values_list('pk', 'round_id'))
        processed = skipped = 0
        for question_id, round_id in expired:
            try:
                # The existing M06 operation: same ranking rules, follow-up questions and limits.
                evaluate_question(round_id, question_id=question_id)
            except RoundError as exc:
                # Already closed by an answer or another worker, or the game is no longer active.
                skipped += 1
                self.stdout.write(f'  skipped question #{question_id} (round #{round_id}): {" ".join(exc.messages)}')
            else:
                processed += 1
        self.stdout.write(
            f'[{moment:%H:%M:%S}] expired questions: {len(expired)}, processed: {processed}, skipped: {skipped}'
        )
        return processed, skipped
