"""Scoring systems for city capture rounds (M07).

Two concrete systems are defined so they can be compared on the same results (see README and
games/tests/test_capture.py). The game uses ACTIVE_SYSTEM.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringSystem:
    name: str
    win_points: int
    territory_increment: int
    # Points for a capture: a fixed amount per turn, or None to use the territory's value.
    capture_points_by_turn: dict | None = None

    def capture(self, turn, territory_score_before):
        """Return (points, territory_score_after, calculation text) for one capture."""
        territory_after = territory_score_before + self.territory_increment
        if self.capture_points_by_turn is None:
            # Value-based: the player earns what the territory is worth after the capture.
            points = territory_after
            calculation = (
                f'territory {territory_score_before} + {self.territory_increment} = {territory_after}; '
                f'points = territory value after = {points}'
            )
        else:
            points = self.capture_points_by_turn[turn]
            calculation = (
                f'territory {territory_score_before} + {self.territory_increment} = {territory_after}; '
                f'points = fixed for turn {turn} = {points}'
            )
        return points, territory_after, calculation

    def win(self):
        return self.win_points, f'round win = {self.win_points}'


# Chosen system: a capture is worth what the territory is worth (+100 per capture, value after the
# increase), and winning the round adds a 150 bonus on top of the extra pick.
VALUE_BASED = ScoringSystem(name='value-based', win_points=150, territory_increment=100)

# Rejected alternative: fixed points by turn (first's picks 100, second's 60), small win bonus,
# territory value only counts captures (+1).
RANK_BASED = ScoringSystem(
    name='rank-based', win_points=50, territory_increment=1, capture_points_by_turn={1: 100, 2: 60}
)

ACTIVE_SYSTEM = VALUE_BASED
