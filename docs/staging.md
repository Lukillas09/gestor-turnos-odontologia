# Entorno de staging en Railway

Esta guía define el entorno público de pruebas usando Railway como hosting y Supabase como base de datos.

Staging no es producción final. Es un ambiente controlado para probar el flujo completo antes de usar el sistema con pacientes reales.

## Arquitectura

```text
App Django: Railway
Base de datos: Supabase PostgreSQL
Adjuntos clínicos: Supabase Storage privado
Recordatorios: GitHub Actions o Railway Cron
Repositorio: GitHub
```

Railway no reemplaza a Supabase. Railway solo ejecuta la aplicación.

## 1. Preparar Supabase PostgreSQL

1. Entrar al proyecto de Supabase.
2. Copiar la connection string de PostgreSQL.
3. Usarla como `DATABASE_URL`.
4. Confirmar que tenga `sslmode=require`.

Ejemplo:

```env
DATABASE_URL=postgres://usuario:password@host.supabase.co:5432/postgres?sslmode=require
```

## 2. Crear servicio en Railway

1. Crear proyecto en Railway.
2. Elegir `Deploy from GitHub repo`.
3. Seleccionar el repo `gestor-turnos-odontologia`.
4. No crear PostgreSQL de Railway.
5. Cargar variables de entorno.
6. Usar los comandos definidos en `railway.json`.

Comandos esperados:

```text
Build Command: bash scripts/build.sh
Pre-Deploy Command: bash scripts/release.sh
Start Command: bash scripts/start.sh
```

Si el pre-deploy no corre, ejecutar migraciones manualmente:

```bash
cd app
python manage.py migrate --noinput
```

Crear superusuario:

```bash
cd app
python manage.py createsuperuser
```

## 3. Variables de entorno en Railway

Usar `.env.railway-supabase.example` como plantilla.

Variables principales:

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
```

Adjuntos clínicos:

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

Email:

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=clave-real-del-proveedor
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
```

## 4. Configurar Google OAuth

En Google Cloud, dentro del cliente OAuth web, agregar:

```text
https://TU-DOMINIO-RAILWAY/turnos/google-calendar/callback/
```

Ese valor debe coincidir exactamente con `GOOGLE_CALENDAR_REDIRECT_URI`.

Si la pantalla de consentimiento está en modo Testing, agregar como test user el email que va a conectar Google Calendar.

## 5. Configurar recordatorios

El workflow vive en:

```text
.github/workflows/staging_recordatorios.yml
```

Para activarlo en GitHub:

1. Ir a `Settings` -> `Secrets and variables` -> `Actions`.
2. Crear la variable:

```text
STAGING_RECORDATORIOS_ACTIVO=true
```

3. Crear los secrets minimos:

```text
STAGING_DJANGO_SECRET_KEY
STAGING_DJANGO_ALLOWED_HOSTS
STAGING_DJANGO_CSRF_TRUSTED_ORIGINS
STAGING_DATABASE_URL
STAGING_EMAIL_BACKEND
STAGING_EMAIL_API_PROVIDER
STAGING_EMAIL_API_KEY
STAGING_DEFAULT_FROM_EMAIL
```

Valores esperados:

```text
STAGING_DJANGO_ALLOWED_HOSTS=tu-app.up.railway.app
STAGING_DJANGO_CSRF_TRUSTED_ORIGINS=https://tu-app.up.railway.app
STAGING_EMAIL_BACKEND=config.email_backends.EmailApiBackend
STAGING_EMAIL_API_PROVIDER=resend
```

## 6. Prueba completa del flujo

Checklist:

- Abrir la URL publica de Railway.
- Entrar al admin.
- Crear superusuario si todavía no existe.
- Cargar un odontologo activo.
- Cargar disponibilidad.
- Conectar Google Calendar desde la pantalla interna.
- Solicitar un turno desde la página pública.
- Ver que el turno queda pendiente.
- Confirmar el turno.
- Ver que se crea o actualiza el evento en Google Calendar.
- Reprogramar el turno.
- Cancelar el turno.
- Revisar emails en casilla real.
- Ejecutar recordatorios desde GitHub Actions o Railway Cron.
- Crear backup lógico de PostgreSQL.
- Crear backup de Supabase Storage.
- Probar restauración en una base separada.

## Listo para avanzar cuando

- La app responde en Railway.
- PostgreSQL de Supabase guarda datos de staging.
- Google OAuth conecta correctamente.
- El flujo de turno completo funciona.
- Los emails reales llegan desde Railway.
- El workflow o cron de recordatorios corre sin errores.
- Existe una estrategia de backup y restauración probada para PostgreSQL y Storage.
