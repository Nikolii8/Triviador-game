from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from games.models import Game, GamePlayer

MAX_CAPITAL_HEALTH = 3


class TerritoryQuerySet(models.QuerySet):
    def for_game(self, game):
        return self.filter(game=game)

    def owned_by(self, player):
        return self.filter(owner=player)


class Territory(models.Model):
    """A territory of one specific game: its identity (name, slug) and its state (owner, score).

    Every game has its own records, so two games each have their own "Sredets".
    """

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='territories')
    name = models.CharField(max_length=50)
    slug = models.SlugField(max_length=50)
    neighbors = models.ManyToManyField(
        'self',
        through='Adjacency',
        through_fields=('from_territory', 'to_territory'),
        symmetrical=True,
        blank=True,
    )
    owner = models.ForeignKey(
        GamePlayer,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='territories',
    )
    score = models.IntegerField(default=0, validators=[MinValueValidator(0)])

    objects = TerritoryQuerySet.as_manager()

    class Meta:
        ordering = ['game', 'name']
        verbose_name_plural = 'territories'
        constraints = [
            models.UniqueConstraint(fields=['game', 'name'], name='territory_unique_name_per_game'),
            models.UniqueConstraint(fields=['game', 'slug'], name='territory_unique_slug_per_game'),
            models.CheckConstraint(condition=Q(score__gte=0), name='territory_score_non_negative'),
        ]

    def __str__(self) -> str:
        return f'{self.name} (game #{self.game_id})'

    def clean(self):
        super().clean()
        if self.owner_id is not None and self.game_id is not None and self.owner.game_id != self.game_id:
            raise ValidationError({'owner': 'The owner must be a player of the same game.'})


class Adjacency(models.Model):
    """One direction of a border. Territory.neighbors stores both directions of every border."""

    from_territory = models.ForeignKey(Territory, on_delete=models.CASCADE, related_name='+')
    to_territory = models.ForeignKey(Territory, on_delete=models.CASCADE, related_name='+')

    class Meta:
        verbose_name_plural = 'adjacencies'
        constraints = [
            models.UniqueConstraint(fields=['from_territory', 'to_territory'], name='adjacency_unique_pair'),
            models.CheckConstraint(
                condition=~Q(from_territory=F('to_territory')),
                name='adjacency_no_self_reference',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.from_territory_id} -> {self.to_territory_id}'

    def clean(self):
        super().clean()
        if self.from_territory_id is None or self.to_territory_id is None:
            return
        if self.from_territory_id == self.to_territory_id:
            raise ValidationError('A territory cannot be its own neighbor.')
        if self.from_territory.game_id != self.to_territory.game_id:
            raise ValidationError('Neighbors must belong to the same game.')


class CapitalManager(models.Manager):
    def for_player(self, player):
        """The player's capital, or None when it has not been assigned (e.g. before M05 initialization)."""
        return self.filter(player=player).select_related('territory').first()


class Capital(models.Model):
    """Marks a territory as a player's capital and tracks how much defense it has left.

    The game is reached through the territory (capital.game) instead of being stored again.
    """

    territory = models.OneToOneField(Territory, on_delete=models.CASCADE, related_name='capital')
    player = models.OneToOneField(GamePlayer, on_delete=models.CASCADE, related_name='capital')
    health = models.PositiveSmallIntegerField(
        default=MAX_CAPITAL_HEALTH,
        validators=[MinValueValidator(0), MaxValueValidator(MAX_CAPITAL_HEALTH)],
    )

    objects = CapitalManager()

    class Meta:
        ordering = ['territory__game', 'player__player_order']
        constraints = [
            models.CheckConstraint(
                condition=Q(health__gte=0, health__lte=MAX_CAPITAL_HEALTH),
                name='capital_health_in_range',
            ),
        ]

    def __str__(self) -> str:
        return f'Capital of {self.player.user} at {self.territory.name}'

    @property
    def game(self):
        return self.territory.game

    @property
    def is_destroyed(self):
        return self.health == 0

    def clean(self):
        super().clean()
        if self.territory_id is None or self.player_id is None:
            return
        if self.player.game_id != self.territory.game_id:
            raise ValidationError('The player and the territory must belong to the same game.')
        if self.territory.owner_id != self.player_id:
            raise ValidationError({'territory': 'A capital must be on a territory owned by its player.'})
