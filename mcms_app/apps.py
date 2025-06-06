from django.apps import AppConfig

class McmsAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mcms_app'

    def ready(self):
        import mcms_app.signals # Import your signals file here