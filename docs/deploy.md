# Deploy en Railway usando Supabase

Esta guia deja Railway como hosting principal de la aplicacion Django y mantiene Supabase como base PostgreSQL.

Railway hostea la app. Supabase sigue siendo la fuente de datos. No crear una base PostgreSQL nueva en Railway para este proyecto.

## Estado del repositorio

El proyecto ya tiene lo necesario para desplegar:

- Gunicorn en `requirements.txt`.
- WhiteNoise configurado en `app/config/settings.py`.
- Lectura de `DATABASE_URL` desde variables de entorno.
- `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` configurables por entorno.
- Scripts separados para build, migraciones y start.
- `railway.json` con comandos de build, pre-deploy y start.

## Comandos de Railway

Build command:

```bash
bash scripts/build.sh
```

Start command:

```bash
bash scripts/start.sh
```

Pre-deploy command para migraciones:

```bash
bash scripts/release.sh
```

Si se prefiere ejecutar migraciones manualmente:

```bash
cd app && python manage.py migrate --noinput
```

Para crear superusuario en Railway:

```bash
cd app && python manage.py createsuperuser
```

## Railway desde GitHub

1. Entrar a Railway.
2. Crear un proyecto nuevo.
3. Elegir `Deploy from GitHub repo`.
4. Seleccionar `Lukillas09/gestor-turnos-odontologia`.
5. No agregar PostgreSQL de Railway.
6. Cargar las variables de entorno.
7. Confirmar que Railway use `bash scripts/build.sh` y `bash scripts/start.sh`.
8. Ejecutar migraciones con `bash scripts/release.sh` si el pre-deploy no corrio.

## Variables de entorno

Usar `.env.railway-supabase.example` como plantilla.

Variables obligatorias:

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
DATABASE_URL=postgres://usuario:password@host.supabase.co:5432/postgres?sslmode=require
WEB_CONCURRENCY=2
```

Email por API HTTP:

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=clave-real-del-proveedor
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
TURNOS_RECORDATORIO_HORAS=24
```

Supabase Storage, si se usan adjuntos clinicos:

```env
MEDIA_STORAGE_BACKEND=config.storage_backends.SupabaseStorage
SUPABASE_STORAGE_URL=https://tu-proyecto.supabase.co
SUPABASE_STORAGE_BUCKET=historias-clinicas
SUPABASE_STORAGE_SERVICE_ROLE_KEY=valor-real
SUPABASE_STORAGE_TIMEOUT=30
SUPABASE_STORAGE_CACHE_CONTROL=3600
SUPABASE_STORAGE_SIGNED_URL_SECONDS=300
```

Google Calendar:

```env
GOOGLE_CALENDAR_CLIENT_ID=valor-real
GOOGLE_CALENDAR_CLIENT_SECRET=valor-real
GOOGLE_CALENDAR_REDIRECT_URI=https://tu-app.up.railway.app/turnos/google-calendar/callback/
GOOGLE_CALENDAR_SCOPES=https://www.googleapis.com/auth/calendar.events
```

El proyecto usa estos nombres exactos para Google Calendar: `GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET` y `GOOGLE_CALENDAR_REDIRECT_URI`.

## Supabase

No hace falta cambiar Supabase para migrar el hosting:

- Mantener la misma `DATABASE_URL`.
- Verificar que la URL incluya `sslmode=require`.
- Mantener el bucket privado `historias-clinicas` si se usan adjuntos.
- Mantener backups de PostgreSQL y Storage fuera del repositorio.

## Google Cloud Console

En el cliente OAuth web agregar la nueva redirect URI:

```text
https://TU-DOMINIO-RAILWAY/turnos/google-calendar/callback/
```

Ese valor debe coincidir exactamente con `GOOGLE_CALENDAR_REDIRECT_URI`.

Si la pantalla de consentimiento esta en modo Testing, agregar como test user el email del odontologo que conecta Google Calendar.

## Archivos estaticos

`scripts/build.sh` ejecuta:

```bash
python manage.py collectstatic --noinput
```

WhiteNoise sirve los estaticos desde Django. La carpeta generada `app/staticfiles/` no se versiona.

## Checklist final

- Railway conectado al repo de GitHub.
- Variables cargadas en Railway.
- `DATABASE_URL` apunta a Supabase, no a Railway PostgreSQL.
- Build finaliza correctamente.
- Migraciones ejecutadas.
- Superusuario creado.
- Google Cloud tiene la redirect URI de Railway.
- Login, pacientes, turnos, agenda, emails y Google Calendar probados en staging.
- Backups de Supabase PostgreSQL y Storage probados.

## Referencias

- Railway Django: https://docs.railway.com/guides/django
- Railway config-as-code: https://docs.railway.com/config-as-code/reference
- Gunicorn: https://gunicorn.org/
- WhiteNoise: https://whitenoise.readthedocs.io/
