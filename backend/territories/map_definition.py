"""The project's map: one fixed set of territories and adjacencies shared by every game.

This module only *describes* the map and checks that a description is valid.
It never touches the database; creating a game's territory records from it is
the job of game initialization (M05).
"""

from collections import deque
from dataclasses import dataclass
from itertools import combinations

from django.core.exceptions import ValidationError

MIN_TERRITORIES = 9
MAX_TERRITORIES = 21
PLAYERS_PER_GAME = 3
MIN_NEIGHBORS = 2


@dataclass(frozen=True)
class MapDefinition:
    territories: tuple  # ((slug, name), ...)
    adjacencies: tuple  # ((slug_a, slug_b), ...), each border listed once

    @property
    def slugs(self):
        return [slug for slug, _ in self.territories]

    def neighbors(self):
        """Adjacency lists keyed by slug (both directions)."""
        graph = {slug: set() for slug in self.slugs}
        for a, b in self.adjacencies:
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)
        return graph

    def edge_set(self):
        """Borders as unordered pairs, so (a, b) and (b, a) are the same border."""
        return {frozenset(pair) for pair in self.adjacencies}

    def validate(self):
        validate_map_definition(self)


def _find_capital_triple(graph):
    """First three territories that are pairwise non-adjacent, or None. Deterministic, no randomness."""
    for triple in combinations(sorted(graph), PLAYERS_PER_GAME):
        if all(b not in graph[a] for a, b in combinations(triple, 2)):
            return triple
    return None


def _is_connected(graph):
    if not graph:
        return False
    start = next(iter(graph))
    seen = {start}
    queue = deque([start])
    while queue:
        for neighbor in graph[queue.popleft()]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return len(seen) == len(graph)


def validate_map_definition(definition):
    """Raise ValidationError listing every rule the map definition breaks."""
    errors = []
    slugs = definition.slugs
    names = [name for _, name in definition.territories]
    count = len(slugs)

    if not (MIN_TERRITORIES <= count <= MAX_TERRITORIES and count % PLAYERS_PER_GAME == 0):
        errors.append(
            f'The map must have {MIN_TERRITORIES}-{MAX_TERRITORIES} territories and a multiple of '
            f'{PLAYERS_PER_GAME} (got {count}).'
        )
    if len(set(slugs)) != count:
        errors.append('Territory slugs must be unique.')
    if len(set(names)) != len(names):
        errors.append('Territory names must be unique.')

    known = set(slugs)
    seen_pairs = set()
    for a, b in definition.adjacencies:
        if a not in known or b not in known:
            errors.append(f'Adjacency {a} - {b} refers to an unknown territory.')
        if a == b:
            errors.append(f'Territory {a} cannot be its own neighbor.')
        pair = frozenset((a, b))
        if pair in seen_pairs:
            errors.append(f'Adjacency {a} - {b} is listed more than once.')
        seen_pairs.add(pair)

    if errors:
        # The graph checks below assume clean slugs and pairs.
        raise ValidationError(errors)

    graph = definition.neighbors()
    for slug in slugs:
        if len(graph[slug]) < MIN_NEIGHBORS:
            errors.append(f'Territory {slug} has {len(graph[slug])} neighbor(s); at least {MIN_NEIGHBORS} required.')
    if not _is_connected(graph):
        errors.append('Every territory must be reachable from every other territory.')
    if _find_capital_triple(graph) is None:
        errors.append(f'The map needs at least {PLAYERS_PER_GAME} pairwise non-adjacent territories for capitals.')

    if errors:
        raise ValidationError(errors)


def validate_game_map(game, definition=None):
    """Check that a game's saved territories match the map definition exactly.

    Read-only: it never creates, changes or deletes records, picks capitals or
    changes the game status. Raises ValidationError listing all mismatches.
    """
    from .models import Adjacency, Territory

    definition = definition or PROJECT_MAP
    errors = []

    territories = list(Territory.objects.filter(game=game))
    saved = {(territory.slug, territory.name) for territory in territories}
    expected = set(definition.territories)
    if saved != expected:
        missing = sorted(slug for slug, _ in expected - saved)
        extra = sorted(slug for slug, _ in saved - expected)
        errors.append(f'Territories do not match the map (missing/wrong: {missing}, unexpected: {extra}).')

    links = Adjacency.objects.filter(from_territory__game=game) | Adjacency.objects.filter(to_territory__game=game)
    directed = set()
    for link in links.select_related('from_territory', 'to_territory'):
        source, target = link.from_territory, link.to_territory
        if source.game_id != game.pk or target.game_id != game.pk:
            errors.append(f'Adjacency {source.slug} - {target.slug} crosses into another game.')
            continue
        if source.pk == target.pk:
            errors.append(f'Territory {source.slug} is linked to itself.')
            continue
        directed.add((source.slug, target.slug))

    for a, b in directed:
        if (b, a) not in directed:
            errors.append(f'Adjacency {a} -> {b} is not symmetric.')
    if {frozenset(pair) for pair in directed} != definition.edge_set():
        errors.append('Adjacencies do not match the map definition.')

    for territory in territories:
        if territory.owner_id is not None and territory.owner.game_id != game.pk:
            errors.append(f'Territory {territory.slug} is owned by a player from another game.')

    if errors:
        raise ValidationError(errors)


# The project map: 18 territories (6 per player), loosely following the regions
# of Bulgaria from the Danube in the north to the Rhodopes and the coast in the
# south. Every border is listed once below; it applies in both directions.
# The README has the full neighbor list per territory.

PROJECT_MAP = MapDefinition(
    territories=(
        ('skali', 'Skali'),
        ('dunaviya', 'Dunavia'),
        ('leventa', 'Leventa'),
        ('zhitno-pole', 'Zhitno Pole'),
        ('kaliakra', 'Kaliakra'),
        ('sredets', 'Sredets'),
        ('balkania', 'Balkania'),
        ('tsarevo', 'Tsarevo'),
        ('madara', 'Madara'),
        ('zlaten-grozd', 'Zlaten Grozd'),
        ('kukeri', 'Kukeri'),
        ('rozova-dolina', 'Rozova Dolina'),
        ('karakachan', 'Karakachan'),
        ('slanchevo', 'Slanchevo'),
        ('pelikania', 'Pelikania'),
        ('ezera', 'Ezera'),
        ('pirina', 'Pirina'),
        ('chuden-kray', 'Chuden Kray'),
    ),
    adjacencies=(
        ('skali', 'dunaviya'),
        ('skali', 'sredets'),
        ('dunaviya', 'sredets'),
        ('dunaviya', 'balkania'),
        ('dunaviya', 'leventa'),
        ('leventa', 'balkania'),
        ('leventa', 'tsarevo'),
        ('leventa', 'madara'),
        ('leventa', 'zhitno-pole'),
        ('zhitno-pole', 'madara'),
        ('zhitno-pole', 'zlaten-grozd'),
        ('zhitno-pole', 'kaliakra'),
        ('kaliakra', 'zlaten-grozd'),
        ('sredets', 'balkania'),
        ('sredets', 'kukeri'),
        ('sredets', 'ezera'),
        ('sredets', 'chuden-kray'),
        ('balkania', 'tsarevo'),
        ('balkania', 'rozova-dolina'),
        ('balkania', 'chuden-kray'),
        ('tsarevo', 'madara'),
        ('tsarevo', 'karakachan'),
        ('tsarevo', 'rozova-dolina'),
        ('madara', 'zlaten-grozd'),
        ('madara', 'karakachan'),
        ('madara', 'slanchevo'),
        ('zlaten-grozd', 'slanchevo'),
        ('kukeri', 'ezera'),
        ('rozova-dolina', 'karakachan'),
        ('rozova-dolina', 'chuden-kray'),
        ('karakachan', 'slanchevo'),
        ('karakachan', 'pelikania'),
        ('karakachan', 'chuden-kray'),
        ('slanchevo', 'pelikania'),
        ('pelikania', 'chuden-kray'),
        ('ezera', 'pirina'),
        ('ezera', 'chuden-kray'),
        ('pirina', 'chuden-kray'),
    ),
)
