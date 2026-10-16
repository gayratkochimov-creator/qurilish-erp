from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "projects"
    verbose_name = "Obyektlar"

    def ready(self):
        # SQLite tezligi: synchronous=NORMAL (har yozishda 2 marta emas, 1 marta fsync — buzilish
        # xavfisiz), kesh 20 MB, vaqtinchalik jadvallar xotirada. WAL ataylab YOQILMAGAN:
        # PythonAnywhere fayl tizimi tarmoq diski — WAL unda ishonchsiz. (2026-10-16 — tezlik)
        from django.db.backends.signals import connection_created

        def _sqlite_sozla(sender, connection, **kw):
            if connection.vendor != "sqlite":
                return
            try:
                with connection.cursor() as c:
                    c.execute("PRAGMA synchronous=NORMAL")
                    c.execute("PRAGMA cache_size=-20000")
                    c.execute("PRAGMA temp_store=MEMORY")
            except Exception:
                pass
        connection_created.connect(_sqlite_sozla, dispatch_uid="qerp_sqlite_sozla")
