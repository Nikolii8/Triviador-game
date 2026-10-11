from django.contrib import admin, messages

from territories.models import Capital, Territory

from .initialization import GameInitializationError, initialize_game
from .models import Game, GamePlayer, Round, RoundAnswer


class GamePlayerInline(admin.TabularInline):
    model = GamePlayer
    extra = 0
    autocomplete_fields = ('user',)
    readonly_fields = ('joined_at',)

    # Once a game has started its line-up is frozen.
    def has_add_permission(self, request, obj=None):
        return (obj is None or obj.status == Game.WAITING) and super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return (obj is None or obj.status == Game.WAITING) and super().has_delete_permission(request, obj)


class RoundInline(admin.TabularInline):
    model = Round
    extra = 0
    fields = ('number', 'status', 'choice_question', 'numeric_question', 'question_type', 'started_at', 'finished_at')
    readonly_fields = ('question_type',)
    autocomplete_fields = ('choice_question', 'numeric_question')
    show_change_link = True


class RoundAnswerInline(admin.TabularInline):
    model = RoundAnswer
    extra = 0
    raw_id_fields = ('player', 'selected_option')


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_by', 'status', 'created_at', 'started_at', 'finished_at')
    list_filter = ('status',)
    search_fields = ('created_by__username', 'players__user__username')
    autocomplete_fields = ('created_by',)
    readonly_fields = ('created_at',)
    list_select_related = ('created_by',)
    inlines = [GamePlayerInline, RoundInline]
    actions = ['initialize_selected_games']

    @admin.action(description='Initialize selected games (build map, assign capitals, start)')
    def initialize_selected_games(self, request, queryset):
        for game_id in queryset.values_list('pk', flat=True):
            try:
                initialize_game(game_id)
            except GameInitializationError as exc:
                self.message_user(request, f'Game #{game_id}: {" ".join(exc.messages)}', messages.ERROR)
            else:
                self.message_user(request, f'Game #{game_id} initialized and started.', messages.SUCCESS)

    def get_deleted_objects(self, objs, request):
        # A game's territories and capitals belong to it and go with it, even though
        # the admin does not allow deleting them one by one.
        deleted, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        perms_needed -= {Territory._meta.verbose_name, Capital._meta.verbose_name}
        return deleted, model_count, perms_needed, protected


@admin.register(GamePlayer)
class GamePlayerAdmin(admin.ModelAdmin):
    list_display = ('user', 'game', 'player_order', 'score', 'is_active')
    list_filter = ('is_active', 'game__status')
    search_fields = ('user__username',)
    autocomplete_fields = ('user',)
    raw_id_fields = ('game',)
    list_select_related = ('user', 'game')


@admin.register(Round)
class RoundAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'game', 'number', 'status', 'question_type')
    list_filter = ('status', 'question_type')
    readonly_fields = ('question_type',)
    raw_id_fields = ('game',)
    autocomplete_fields = ('choice_question', 'numeric_question')
    list_select_related = ('game',)
    inlines = [RoundAnswerInline]


@admin.register(RoundAnswer)
class RoundAnswerAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'round', 'player', 'is_correct', 'points_awarded', 'submitted_at')
    list_filter = ('is_correct', 'round__question_type')
    raw_id_fields = ('round', 'player', 'selected_option')
    list_select_related = ('round', 'player__user')
