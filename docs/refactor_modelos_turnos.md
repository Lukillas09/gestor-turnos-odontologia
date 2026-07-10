# Refactor futuro de `turnos/models.py`

`turnos/models.py` conserva todos los modelos Django en esta fase para no alterar migraciones, import paths históricos ni nombres de tablas.

## Objetivo futuro

Una segunda fase podría convertir `turnos/models.py` en un paquete:

```text
turnos/models/
├── agenda.py
├── appointments.py
├── public_access.py
├── google_calendar.py
└── __init__.py
```

## Estrategia propuesta

1. Mantener los nombres de clases y `app_label="turnos"`.
2. Mover primero funciones puras como upload paths, validadores y constantes.
3. Separar managers/querysets si aparecen dependencias circulares controlables.
4. Reexportar todos los modelos desde `turnos.models.__init__`.
5. Ejecutar `makemigrations --check --dry-run` después de cada paso.
6. Confirmar que las migraciones históricas siguen importando correctamente.

## Riesgos principales

- Imports circulares entre `Turno`, `SolicitudTurnoPublica`, `AccionPublicaTurno` y Google Calendar.
- Migraciones antiguas que esperan funciones o modelos en `turnos.models`.
- Admin, forms, services y tests con imports directos.
- Cambios accidentales en `Meta`, constraints o campos que generen migraciones innecesarias.

## Tests necesarios

- Import público: `from turnos.models import Turno, SolicitudTurnoPublica, GoogleCalendarConexion`.
- `manage.py check`.
- `makemigrations --check --dry-run`.
- Suite completa de turnos, Google Calendar, public access y solicitudes públicas.
- Smoke de admin y shell de Django.

La división debe hacerse en una rama dedicada y sin mezclar cambios funcionales.
