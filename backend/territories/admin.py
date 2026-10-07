from django.contrib import admin

from .models import Capital, Territory


class MapStructureAdmin(admin.ModelAdmin):
    """Territories and capitals are created by game initialization (M05), not by hand.

    The admin can inspect them and adjust the numeric state (score / health),
    which is still validated by the model validators, but cannot add, delete or
    re-link records.
    """

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Territory)
class TerritoryAdmin(MapStructureAdmin):
    list_display = ('name', 'slug', 'game', 'owner', 'score', 'neighbor_names')
    list_filter = ('game', 'owner')
    search_fields = ('name', 'slug')
    list_select_related = ('game', 'owner__user')
    fields = ('game', 'name', 'slug', 'owner', 'score', 'neighbor_names')
    readonly_fields = ('game', 'name', 'slug', 'owner', 'neighbor_names')

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('neighbors')

    @admin.display(description='neighbors')
    def neighbor_names(self, obj):
        return ', '.join(sorted(neighbor.name for neighbor in obj.neighbors.all())) or '-'


@admin.register(Capital)
class CapitalAdmin(MapStructureAdmin):
    list_display = ('player', 'territory', 'health', 'game')
    list_filter = ('territory__game',)
    search_fields = ('player__user__username', 'player__user__profile__nickname', 'territory__name', 'territory__slug')
    list_select_related = ('player__user', 'territory__game')
    fields = ('player', 'territory', 'health')
    readonly_fields = ('player', 'territory')

    @admin.display(description='game')
    def game(self, obj):
        return obj.territory.game
