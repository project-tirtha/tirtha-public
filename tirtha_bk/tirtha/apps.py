from django.apps import AppConfig


class TirthaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tirtha"

    def ready(self):
        # Imported for its side effects: importing the module is what connects
        # every @receiver in it. Nothing here uses the name, which is exactly
        # why `ruff --fix` deleted this line in Phase 1.2 and silently
        # disconnected all of them -- directory creation on Site save, the
        # image quarantine mover, post_migrate defaults and admin notification
        # mail all stopped firing, with no error anywhere. Keep the noqa.
        import tirtha.signals  # noqa: F401

        return super().ready()
