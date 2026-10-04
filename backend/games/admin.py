from django.contrib import admin

from .models import Game, GamePlayer, Round, RoundAnswer


class GamePlayerInline(admin.TabularInline):
    model = GamePlayer
    extra = 0
    autocomplete_fields = ('user',)
    readonly_fields = ('joined_at',)


class RoundInline(admin.TabularInline):
    model = Round
    extra = 0
    fields = ('number', 'status', 'question_type', 'choice_question', 'numeric_question', 'started_at', 'finished_at')
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
