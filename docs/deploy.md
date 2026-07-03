# Deploy en Railway usando Supabase

Railway es el hosting de la aplicación Django. Supabase se mantiene como proveedor de PostgreSQL y Storage. No crear una base PostgreSQL nueva en Railway para este proyecto.

## Estado del Repositorio

El repo ya incluye:

- `requirements.txt` con Django, Gunicorn, WhiteNoise y psycopg.
- `Procfile`.
- `railway.json`.
- `scripts/build.sh`.
- `scripts/release.sh`.
- `scripts/start.sh`.
- Configuración por variables de entorno.
- `DATABASE_URL` compatible con PostgreSQL/Supabase.
- WhiteNoise para archivos estáticos.

## Comandos de Railway

Build command:

```bash
bash scripts/build.sh
```

Pre-deploy command:

```bash
bash scripts/release.sh
```

Start command:

```bash
bash scripts/start.sh
```

El archivo `railway.json` ya declara estos comandos.

## Crear Servicio en Railway

1. Crear un proyecto en Railway.
2. Elegir `Deploy from GitHub repo`.
3. Seleccionar el repositorio `Lukillas09/gestor-turnos-odontologia`.
4. Crear un servicio web.
5. No agregar PostgreSQL de Railway.
6. Cargar variables de entorno.
7. Esperar el build.
8. Verificar que el deploy quede en `Deployment successful`.

Si Railway tiene builds pausados o incidentes de plataforma, el commit puede quedar en `Queued`. En ese caso el código está en GitHub, pero la app seguirá sirviendo el deploy anterior hasta que Railway complete el build.

## Variables de Entorno Obligatorias

Usar `.env.railway-supabase.example` como base.

### Django

```env
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=clave-segura-generada
DJANGO_ALLOWED_HOSTS=tu-app.up.railway.app
DJANGO_CSRF_TRUSTED_ORIGINS=https://tu-app.up.railway.app
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
DJANGO_SECURE_PROXY_SSL_HEADER=True
DJANGO_LOG_LEVEL=INFO
OAUTH_TOKEN_ENCRYPTION_KEY=clave-fernet-generada
```

### Base de datos Supabase

```env
DATABASE_URL=postgres://usuario:password@host.supabase.co:5432/postgres?sslmode=require
```

La URL debe venir de Supabase y debe incluir `sslmode=require`.

### Redis para rate limiting publico

Agregar un servicio Redis al proyecto de Railway o usar un proveedor Redis externo compatible. Cargar la URL en:

```env
REDIS_URL=redis://usuario:password@host:puerto/0
TURNOS_PUBLIC_REDIS_REQUIRED=True
TURNOS_PUBLIC_ACCESS_REQUEST_LIMIT=5
TURNOS_PUBLIC_ACCESS_REQUEST_WINDOW_SECONDS=900
TURNOS_PUBLIC_OTP_ATTEMPTS=5
TURNOS_PUBLIC_OTP_SECONDS=600
TURNOS_PUBLIC_SESSION_SECONDS=900
TURNOS_PUBLIC_RESEND_SECONDS=60
TURNOS_PUBLIC_RESEND_LIMIT=3
TURNOS_PUBLIC_RESEND_WINDOW_SECONDS=3600
TURNOS_PUBLIC_ACTION_TOKEN_SECONDS=900
TURNOS_PUBLIC_ACTION_LIMIT=20
TURNOS_PUBLIC_ACTION_WINDOW_SECONDS=900
```

Sin `REDIS_URL`, la app debe fallar en deploy con `TURNOS_PUBLIC_REDIS_REQUIRED=True`. Esto evita rate limits locales por proceso en produccion.

### Turnstile opcional

Crear las claves en Cloudflare Turnstile para el dominio real o el dominio de Railway. Luego cargar:

```env
TURNSTILE_ENABLED=True
TURNSTILE_SITE_KEY=site-key
TURNSTILE_SECRET_KEY=secret-key
TURNSTILE_VERIFY_URL=https://challenges.cloudflare.com/turnstile/v0/siteverify
TURNSTILE_REQUIRED_AFTER_ATTEMPTS=3
TURNSTILE_TIMEOUT_SECONDS=5
```

Puede mantenerse apagado en staging inicial con `TURNSTILE_ENABLED=False`, pero Redis debe quedar activo para los limites.

### Deploy

```env
WEB_CONCURRENCY=2
```

### Email

Recomendado para Railway:

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=clave-real-del-proveedor
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
TURNOS_RECORDATORIO_HORAS=24
```

También se puede usar SMTP, pero en hosting gratuito suele ser más confiable usar API HTTP.

### Supabase Storage

Necesario si se cargan adjuntos clínicos o fotos en storage externo:

```env
MEDIA_STORAGE_BACKEND=config.storage_backends.SupabaseStorage
SUPABASE_STORAGE_URL=https://tu-proyecto.supabase.co
SUPABASE_STORAGE_BUCKET=historias-clinicas
SUPABASE_STORAGE_SERVICE_ROLE_KEY=clave-service-role
SUPABASE_STORAGE_TIMEOUT=30
SUPABASE_STORAGE_CACHE_CONTROL=3600
SUPABASE_STORAGE_SIGNED_URL_SECONDS=300
```

`SUPABASE_STORAGE_SERVICE_ROLE_KEY` es secreto sensible.

### Google Calendar

```env
GOOGLE_CALENDAR_CLIENT_ID=client-id
GOOGLE_CALENDAR_CLIENT_SECRET=client-secret
GOOGLE_CALENDAR_REDIRECT_URI=https://tu-app.up.railway.app/turnos/google-calendar/callback/
GOOGLE_CALENDAR_SCOPES=https://www.googleapis.com/auth/calendar.events
```

`GOOGLE_CALENDAR_CLIENT_SECRETS_FILE` existe para desarrollo alternativo con JSON, pero no es necesario si se usan `CLIENT_ID` y `CLIENT_SECRET`.

## Google Cloud Console

En el cliente OAuth web agregar:

```text
https://TU-DOMINIO-RAILWAY/turnos/google-calendar/callback/
```

Debe coincidir exactamente con `GOOGLE_CALENDAR_REDIRECT_URI`.

Si la app OAuth está en modo Testing, agregar como test users los emails de los odontólogos que conectarán Calendar.

## Migraciones y Superusuario

Railway puede correr migraciones con `preDeployCommand`. Si se necesita ejecutarlas manualmente:

```bash
cd app
python manage.py migrate --noinput
```

Crear superusuario:

```bash
cd app
python manage.py createsuperuser
```

## Archivos Estáticos

`scripts/build.sh` ejecuta:

```bash
python manage.py collectstatic --noinput
```

WhiteNoise sirve los archivos desde `app/staticfiles/`.

## Checklist de Deploy

- Repo conectado a Railway.
- Variables cargadas.
- `DJANGO_ALLOWED_HOSTS` contiene el dominio Railway sin `https://`.
- `DJANGO_CSRF_TRUSTED_ORIGINS` contiene el mismo dominio con `https://`.
- `DATABASE_URL` apunta a Supabase.
- Build finalizado correctamente.
- Migraciones aplicadas.
- Superusuario creado.
- Google Cloud tiene redirect URI de Railway.
- Login interno probado.
- Landing pública probada.
- Solicitud pública probada.
- Acceso publico OTP, listado de mis turnos, cancelacion y reprogramacion probados.
- Email real probado.
- Google Calendar probado con al menos un odontólogo.
- Adjuntos clínicos probados si Storage está activo.

## Troubleshooting

### `DisallowedHost`

Agregar el dominio exacto a:

```env
DJANGO_ALLOWED_HOSTS=tu-app.up.railway.app
```

Y redeploy.

### CSRF al enviar formularios

Agregar:

```env
DJANGO_CSRF_TRUSTED_ORIGINS=https://tu-app.up.railway.app
```

### Ruta nueva devuelve 404 en Railway

Verificar que el deploy activo corresponda al último commit. Si el nuevo deploy quedó `Queued` o `Failed`, Railway todavía está sirviendo una versión anterior.

### Build falla por incidente Railway

Revisar el banner de Railway y `status.railway.com`. Si el error es de plataforma, no modificar código: esperar y reintentar el deploy.

### Emails no llegan

Validar:

```bash
cd app
python manage.py probar_email tu-email@example.com
```

Revisar `EMAIL_BACKEND`, `EMAIL_API_PROVIDER`, `EMAIL_API_KEY` y `DEFAULT_FROM_EMAIL`.

### Storage falla

Validar:

```bash
cd app
python manage.py probar_storage_historias
```

Revisar bucket privado, URL de Supabase y service role key.
