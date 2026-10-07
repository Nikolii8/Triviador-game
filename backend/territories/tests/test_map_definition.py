from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from territories.map_definition import PROJECT_MAP, MapDefinition, validate_map_definition


def ring(count, extra_edges=()):
    """A cycle of `count` territories t0..t(n-1): connected, 2 neighbors each, has independent triples for n >= 6."""
    territories = tuple((f't{i}', f'T{i}') for i in range(count))
    edges = tuple((f't{i}', f't{(i + 1) % count}') for i in range(count)) + tuple(extra_edges)
    return MapDefinition(territories=territories, adjacencies=edges)


class ProjectMapTests(SimpleTestCase):
    def test_project_map_is_valid(self):
        PROJECT_MAP.validate()

    def test_project_map_size(self):
        count = len(PROJECT_MAP.territories)
        self.assertTrue(9 <= count <= 21)
        self.assertEqual(count % 3, 0)

    def test_every_border_is_symmetric_in_the_neighbor_lists(self):
        graph = PROJECT_MAP.neighbors()
        for slug, neighbors in graph.items():
            for neighbor in neighbors:
                with self.subTest(border=(slug, neighbor)):
                    self.assertIn(slug, graph[neighbor])


class MapDefinitionRuleTests(SimpleTestCase):
    def assert_invalid(self, definition, message_part):
        with self.assertRaises(ValidationError) as ctx:
            validate_map_definition(definition)
        self.assertTrue(
            any(message_part in message for message in ctx.exception.messages),
            ctx.exception.messages,
        )

    def test_allowed_sizes(self):
        for count in (9, 12, 15, 18, 21):
            with self.subTest(count=count):
                validate_map_definition(ring(count))

    def test_rejected_sizes(self):
        for count in (6, 10, 11, 24):
            with self.subTest(count=count):
                self.assert_invalid(ring(count), 'multiple of 3')

    def test_duplicate_slug_or_name(self):
        base = ring(9)
        duplicate_slug = MapDefinition(territories=base.territories[:-1] + (('t0', 'Other'),), adjacencies=base.adjacencies)
        duplicate_name = MapDefinition(territories=base.territories[:-1] + (('t8', 'T0'),), adjacencies=base.adjacencies)
        self.assert_invalid(duplicate_slug, 'slugs must be unique')
        self.assert_invalid(duplicate_name, 'names must be unique')

    def test_self_reference(self):
        self.assert_invalid(ring(9, extra_edges=[('t0', 't0')]), 'its own neighbor')

    def test_duplicate_border_in_either_direction(self):
        self.assert_invalid(ring(9, extra_edges=[('t0', 't1')]), 'more than once')
        self.assert_invalid(ring(9, extra_edges=[('t1', 't0')]), 'more than once')

    def test_unknown_territory(self):
        self.assert_invalid(ring(9, extra_edges=[('t0', 'nowhere')]), 'unknown territory')

    def test_territory_with_fewer_than_two_neighbors(self):
        base = ring(9)
        # Replace the t8-t0 border so t8 is only linked to t7, while the graph stays connected.
        edges = tuple(edge for edge in base.adjacencies if edge != ('t8', 't0')) + (('t0', 't7'),)
        self.assert_invalid(MapDefinition(base.territories, edges), 't8 has 1 neighbor')

    def test_disconnected_map(self):
        # Two separate triangles plus a separate cycle of three: 9 territories, all with 2 neighbors.
        territories = tuple((f't{i}', f'T{i}') for i in range(9))
        edges = []
        for start in (0, 3, 6):
            a, b, c = (f't{start + offset}' for offset in range(3))
            edges += [(a, b), (b, c), (c, a)]
        self.assert_invalid(MapDefinition(territories, tuple(edges)), 'reachable')

    def test_map_without_three_independent_territories(self):
        # Complete graph: every territory borders every other one, so no capital triple exists.
        territories = tuple((f't{i}', f'T{i}') for i in range(9))
        edges = tuple((f't{i}', f't{j}') for i in range(9) for j in range(i + 1, 9))
        self.assert_invalid(MapDefinition(territories, edges), 'non-adjacent')
