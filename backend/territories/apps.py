from django.apps import AppConfig


class TerritoriesConfig(AppConfig):
    name = 'territories'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        import territories.signals  # noqa: F401
