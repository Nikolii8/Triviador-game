"""Battles (M08): an attacker against the owner of a neighboring territory.

create_battle() records the attacker, the defender (always the target's current owner) and the
target, and asks a choice question to the two of them. The rest of the round (start, answers,
10 s, follow-up steps, limit of 3) is the shared lifecycle in games.rounds. When the round is
evaluated, apply_battle_result() runs in the same transaction and applies the consequences:

- battle, attacker wins:   target changes owner, Territory.score grows, attacker gets win + conquest
- battle, defender wins:   nothing on the map changes, defender gets the win award
- capital_attack, any win: the winner gets the win award; the capital is not changed at all
- no winner:               no award and no change
"""

import random

from django.db import transaction
from django.db.models import Max

from territories.models import Capital, Territory

from . import capture, rounds
from .errors import BattleTargetChanged, InvalidRoundInput, RoundNotFound, RoundStateError
from .models import Game, GamePlayer, Round, RoundQuestion, ScoreEntry
from .scoring import ACTIVE_SYSTEM


def attackable_territories(player):
    """Territories of other players that border something `player` owns. No exception for distance."""
    return (
        Territory.objects.filter(game_id=player.game_id, owner__isnull=False, neighbors__owner=player)
        .exclude(owner=player)
        .distinct()
    )


def create_battle(game_id, attacker_id, target_id, *, defender_id=None, rng=None):
    """Create a pending battle round of `attacker_id` against the owner of `target_id`.

    `defender_id` is optional; if given it must be the target's owner. The round type follows from
    the target: a capital makes it a capital_attack, any other territory a battle.
    """
    rng = rng or random.SystemRandom()
    with transaction.atomic():
        try:
            game = Game.objects.select_for_update().get(pk=game_id)
        except Game.DoesNotExist:
            raise RoundNotFound(f'Game #{game_id} does not exist.') from None
        rounds._ensure_game_is_active(game)

        if capture.free_territories(game).exists():
            raise RoundStateError('Battles start once every territory has an owner.')
        if game.rounds.exclude(status=Round.EVALUATED).exists():
            raise RoundStateError(f'Game #{game.pk} already has an unfinished round.')
        if capture.pending_right(game) is not None:
            raise RoundStateError(f'Game #{game.pk} still has territory picks to make.')

        try:
            attacker = GamePlayer.objects.get(pk=attacker_id)
        except GamePlayer.DoesNotExist:
            raise RoundNotFound(f'Player #{attacker_id} does not exist.') from None
        if attacker.game_id != game.pk:
            raise RoundStateError(f'Player #{attacker.pk} does not play in game #{game.pk}.')
        try:
            target = Territory.objects.select_for_update().get(pk=target_id)
        except Territory.DoesNotExist:
            raise RoundNotFound(f'Territory #{target_id} does not exist.') from None

        if target.game_id != game.pk:
            raise InvalidRoundInput(f'{target.name} is not a territory of game #{game.pk}.')
        if target.owner_id is None:
            raise InvalidRoundInput(f'{target.name} is free; a battle needs another player\'s territory.')
        if target.owner_id == attacker.pk:
            raise InvalidRoundInput(f'{target.name} already belongs to the attacker.')
        if not target.neighbors.filter(owner=attacker).exists():
            raise InvalidRoundInput(f'{target.name} does not border any territory of the attacker.')
        if defender_id is not None and defender_id != target.owner_id:
            raise InvalidRoundInput('The defender is always the owner of the target.')

        question = rounds._pick_unused_question(game, rng, RoundQuestion.CHOICE)
        if question is None:
            raise RoundStateError('No unused valid choice question is left for this game.')

        is_capital = Capital.objects.filter(territory=target).exists()
        round_ = Round(
            game=game,
            number=(game.rounds.aggregate(highest=Max('number'))['highest'] or 0) + 1,
            round_type=Round.CAPITAL_ATTACK if is_capital else Round.BATTLE,
            places_needed=1,
            choice_question=question,
            attacker=attacker,
            defender=target.owner,
            target=target,
        )
        round_.full_clean()
        round_.save()
        rounds._ask(round_, 1, question, [attacker, target.owner], first_place=1, last_place=1)
    return round_


def apply_battle_result(round_, system=ACTIVE_SYSTEM):
    """Apply an evaluated battle's consequences. Runs inside the round's completing transaction.

    Raises BattleTargetChanged (rolling the completion back) if the target no longer belongs to
    the recorded defender: no result, no award, and no other target is chosen instead.
    """
    winner = round_.winner
    if winner is None:
        return  # no winner: no award, no change of ownership

    target = Territory.objects.select_for_update().get(pk=round_.target_id)
    if target.owner_id != round_.defender_id:
        raise BattleTargetChanged(f'{target.name} no longer belongs to the defender; the battle result is not applied.')

    capture.credit(winner, round_, ScoreEntry.ROUND_WIN, *system.win())

    if round_.round_type == Round.BATTLE and winner.pk == round_.attacker_id:
        before = target.score
        points, after, calculation = system.conquest(before)
        # Conditional change: only if the defender still owns it at this moment.
        moved = Territory.objects.filter(pk=target.pk, owner_id=round_.defender_id).update(
            owner_id=round_.attacker_id, score=after
        )
        if moved == 0:
            raise BattleTargetChanged(f'{target.name} changed owner during the battle; the result is not applied.')
        capture.credit(
            winner,
            round_,
            ScoreEntry.CONQUEST,
            points,
            calculation,
            territory=target,
            territory_before=before,
            territory_after=after,
        )
