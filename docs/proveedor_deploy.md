# Decisión de hosting

Esta guía resume la decisión vigente de infraestructura para ejecutar el sistema fuera de la máquina local.

## Decisión

Para esta etapa se elige:

```text
App Django: Railway
Base de datos: Supabase PostgreSQL
Adjuntos clínicos: Supabase Storage
Recordatorios: GitHub Actions o Railway Cron
Repositorio: GitHub
```

Railway reemplaza al hosting anterior. Supabase se mantiene como base de datos principal.

## Por qué Railway para la app

Railway permite desplegar desde GitHub, configurar variables de entorno, usar Gunicorn y ejecutar comandos de build/start de forma simple.

Para este proyecto conviene porque:

- El repositorio ya tiene `scripts/build.sh`, `scripts/start.sh` y `scripts/release.sh`.
- `railway.json` deja los comandos versionados.
- La app puede seguir usando Supabase sin crear otra base.
- Los logs y variables quedan centralizados en el servicio web.

## Por qué mantener Supabase

Supabase ya contiene PostgreSQL y Storage del proyecto. Cambiar de hosting no requiere migrar datos si se conserva la misma `DATABASE_URL`.

Puntos a cuidar:

- Mantener `sslmode=require`.
-- Hacer backups lógicos propios.
-- Probar restauración.
- No exponer `SUPABASE_STORAGE_SERVICE_ROLE_KEY`.

## Variables para Railway

Usar `.env.railway-supabase.example` como referencia.

Variables base:

```env
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=generar-una-clave-segura
DJANGO_ALLOWED_HOSTS=tu-app.up.railway.app
DJANGO_CSRF_TRUSTED_ORIGINS=https://tu-app.up.railway.app
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
DJANGO_SECURE_PROXY_SSL_HEADER=True
DJANGO_LOG_LEVEL=INFO
OAUTH_TOKEN_ENCRYPTION_KEY=clave-fernet-generada
DATABASE_URL=postgres://usuario:password@host.supabase.co:5432/postgres?sslmode=require
WEB_CONCURRENCY=2
TURNOS_RECORDATORIO_HORAS=24
```

Google Calendar:

```env
GOOGLE_CALENDAR_CLIENT_ID=
GOOGLE_CALENDAR_CLIENT_SECRET=
GOOGLE_CALENDAR_REDIRECT_URI=https://tu-app.up.railway.app/turnos/google-calendar/callback/
GOOGLE_CALENDAR_SCOPES=https://www.googleapis.com/auth/calendar.events
```

Email recomendado:

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=clave-real-del-proveedor
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
```

Supabase Storage:

```env
MEDIA_STORAGE_BACKEND=config.storage_backends.SupabaseStorage
SUPABASE_STORAGE_URL=https://tu-proyecto.supabase.co
SUPABASE_STORAGE_BUCKET=historias-clinicas
SUPABASE_STORAGE_SERVICE_ROLE_KEY=valor-real
```

## Recordatorios

El proyecto ya tiene el comando:

```bash
python manage.py enviar_recordatorios_email --horas 24 --fallar-si-hay-errores
```

Opciones:

- GitHub Actions para una primera automatizacion.
- Railway Cron si se quiere manejar todo desde Railway.

GitHub Actions queda apagado hasta crear:

```text
STAGING_RECORDATORIOS_ACTIVO=true
```

## Riesgos de una arquitectura inicial de bajo costo

-- Los planes gratuitos o iniciales pueden tener límites de uso.
- Supabase requiere backups propios si se cargan datos importantes.
-- Los cron externos no garantizan ejecución al segundo exacto.
- Para uso real con pacientes conviene tener dominio propio, monitoreo y estrategia de restauracion.

## Recomendación

Usar Railway + Supabase para staging y primera demostración controlada.

Antes de producción diaria:

-- Rotar secretos compartidos durante configuración.
- Definir dominio real.
- Probar backups y restauracion.
- Probar Google Calendar con la URL final.
- Probar emails reales desde Railway.
- Revisar logs sin exponer datos clinicos.
