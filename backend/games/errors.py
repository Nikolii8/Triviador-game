"""Errors of the game service operations (rounds, captures). Nothing is changed when one is raised.

The class (and its `kind`) tells the caller why the request was refused.
"""

from django.core.exceptions import ValidationError


class RoundError(ValidationError):
    """A refused operation. Nothing was changed when it is raised."""

    kind = 'error'


class InvalidRoundInput(RoundError):
    """The request itself is not acceptable (malformed value, choice the rules do not allow)."""

    kind = 'invalid_input'


class RoundStateError(RoundError):
    """The request is well-formed but not allowed in the current state (wrong turn, time is up, ...)."""

    kind = 'invalid_state'


class RoundNotFound(RoundError):
    """The game, round, player or territory does not exist."""

    kind = 'not_found'


class BattleTargetChanged(RoundStateError):
    """The battle's target no longer belongs to the recorded defender, so its result cannot be applied."""
