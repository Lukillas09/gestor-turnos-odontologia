# Configuración

El proyecto se configura por variables de entorno. En desarrollo se puede usar `.env`; en Railway las variables se cargan desde el panel del servicio.

El loader actual lee:

- `.env` en la raíz del repo.
- `app/.env`, si existe.

## Plantillas

- `.env.example`: desarrollo local.
- `.env.railway-supabase.example`: Railway + Supabase.

Nunca subir `.env` ni claves reales al repositorio.

## Variables Django

| Variable | Obligatoria | Uso |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Sí en producción | Clave criptográfica de Django. |
| `DJANGO_DEBUG` | Sí | `True` local, `False` deploy. |
| `DJANGO_ALLOWED_HOSTS` | Sí si `DEBUG=False` | Dominios permitidos separados por coma. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Recomendado en deploy | Orígenes HTTPS confiables separados por coma. |
| `DJANGO_LOG_LEVEL` | No | Nivel de logs. Default: `INFO`. |

Ejemplo:

```env
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=clave-segura
DJANGO_ALLOWED_HOSTS=tu-app.up.railway.app
DJANGO_CSRF_TRUSTED_ORIGINS=https://tu-app.up.railway.app
DJANGO_LOG_LEVEL=INFO
```

## Seguridad HTTPS

| Variable | Default | Uso |
| --- | --- | --- |
| `DJANGO_SECURE_SSL_REDIRECT` | `False` | Redirige HTTP a HTTPS. |
| `DJANGO_SESSION_COOKIE_SECURE` | `not DEBUG` | Cookie de sesión solo por HTTPS. |
| `DJANGO_CSRF_COOKIE_SECURE` | `not DEBUG` | Cookie CSRF solo por HTTPS. |
| `DJANGO_SECURE_PROXY_SSL_HEADER` | `False` | Permite confiar en `X-Forwarded-Proto=https`. |
| `DJANGO_SECURE_HSTS_SECONDS` | `0` | HSTS. Dejar en `0` hasta validar dominio real. |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | `False` | HSTS para subdominios. |
| `DJANGO_SECURE_HSTS_PRELOAD` | `False` | Preload HSTS. |

Para Railway:

```env
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
DJANGO_SECURE_PROXY_SSL_HEADER=True
DJANGO_SECURE_HSTS_SECONDS=0
```

## Seguridad del Flujo Publico de Turnos

El flujo publico de autogestion usa un desafio OTP por email, sesion temporal verificada, permisos persistentes de un solo uso por turno y rate limiting en cache. En produccion `REDIS_URL` debe apuntar a Redis para que los limites funcionen entre procesos/instancias.

| Variable | Default | Uso |
| --- | --- | --- |
| `REDIS_URL` | vacio | URL de Redis para cache y rate limiting distribuido. |
| `TURNOS_PUBLIC_REDIS_REQUIRED` | `not DEBUG` | Exige Redis cuando la app corre fuera de desarrollo. |
| `TURNOS_PUBLIC_ACCESS_REQUEST_LIMIT` | `5` | Cantidad maxima de solicitudes de acceso por IP/DNI hasheados dentro de la ventana. |
| `TURNOS_PUBLIC_ACCESS_REQUEST_WINDOW_SECONDS` | `900` | Ventana de rate limit para solicitudes de acceso. |
| `TURNOS_PUBLIC_OTP_ATTEMPTS` | `5` | Intentos maximos antes de invalidar un desafio OTP. |
| `TURNOS_PUBLIC_OTP_SECONDS` | `600` | Tiempo de vida del codigo OTP. |
| `TURNOS_PUBLIC_SESSION_SECONDS` | `900` | Duracion de la sesion publica verificada. |
| `TURNOS_PUBLIC_RESEND_SECONDS` | `60` | Cooldown minimo entre reenvios de codigo. |
| `TURNOS_PUBLIC_RESEND_LIMIT` | `3` | Reenvios maximos por IP/DNI hasheados dentro de la ventana. |
| `TURNOS_PUBLIC_RESEND_WINDOW_SECONDS` | `3600` | Ventana de rate limit para reenvios. |
| `TURNOS_PUBLIC_ACTION_TOKEN_SECONDS` | `900` | Tiempo de vida de permisos de cancelar/reprogramar generados para la sesion. |
| `TURNOS_PUBLIC_ACTION_LIMIT` | `20` | Cantidad maxima de acciones publicas verificadas por IP dentro de la ventana. |
| `TURNOS_PUBLIC_ACTION_WINDOW_SECONDS` | `900` | Ventana de rate limit para cancelar/reprogramar desde la sesion publica. |
| `TURNSTILE_ENABLED` | `False` | Activa Cloudflare Turnstile despues de superar el umbral configurado. |
| `TURNSTILE_SITE_KEY` | vacio | Site key publica de Turnstile. |
| `TURNSTILE_SECRET_KEY` | vacio | Secret key privada de Turnstile. |
| `TURNSTILE_REQUIRED_AFTER_ATTEMPTS` | `3` | Umbral de intentos desde el cual se exige Turnstile. |
| `TURNSTILE_TIMEOUT_SECONDS` | `5` | Timeout para verificar Turnstile. |
## Cifrado de Tokens OAuth

| Variable | Obligatoria | Uso |
| --- | --- | --- |
| `OAUTH_TOKEN_ENCRYPTION_KEY` | Si `DJANGO_DEBUG=False` | Clave Fernet para cifrar `access_token` y `refresh_token` de Google Calendar en base de datos. |

Generar una clave:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

En desarrollo, si la variable queda vacia, se deriva una clave desde `DJANGO_SECRET_KEY`. En produccion debe configurarse una clave Fernet explicita y conservarse estable: si cambia, los tokens OAuth ya guardados no podran descifrarse.

## Base de Datos

| Variable | Obligatoria | Uso |
| --- | --- | --- |
| `DATABASE_URL` | No en local, sí en deploy | Configura SQLite o PostgreSQL. |

Si `DATABASE_URL` está vacío, Django usa SQLite local en `app/db.sqlite3`.

Supabase PostgreSQL:

```env
DATABASE_URL=postgres://usuario:password@host.supabase.co:5432/postgres?sslmode=require
```

Si la contraseña tiene caracteres especiales, deben estar codificados para URL.

## Email

### Desarrollo local

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DEFAULT_FROM_EMAIL=turnos@localhost
```

### API HTTP para Railway

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=clave-real
EMAIL_API_URL=
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
EMAIL_TIMEOUT=10
```

Proveedores implementados:

- `resend`
- `brevo`
- `sendinblue` como alias de `brevo`

### SMTP

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=usuario
EMAIL_HOST_PASSWORD=clave
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_TIMEOUT=10
DEFAULT_FROM_EMAIL=Consultorio <turnos@example.com>
```

`EMAIL_USE_TLS` y `EMAIL_USE_SSL` no pueden estar activos al mismo tiempo.

## Recordatorios

```env
TURNOS_RECORDATORIO_HORAS=24
```

El comando `enviar_recordatorios_email` busca turnos confirmados dentro de esa ventana.

## Google Calendar

| Variable | Uso |
| --- | --- |
| `GOOGLE_CALENDAR_CLIENT_ID` | Client ID OAuth web. |
| `GOOGLE_CALENDAR_CLIENT_SECRET` | Client Secret OAuth web. |
| `GOOGLE_CALENDAR_CLIENT_SECRETS_FILE` | Alternativa local con JSON, opcional. |
| `GOOGLE_CALENDAR_REDIRECT_URI` | Callback exacto configurado en Google Cloud. |
| `GOOGLE_CALENDAR_SCOPES` | Scopes separados por coma. |

Scope actual:

```env
GOOGLE_CALENDAR_SCOPES=https://www.googleapis.com/auth/calendar.events
```

Callback local:

```env
GOOGLE_CALENDAR_REDIRECT_URI=http://127.0.0.1:8000/turnos/google-calendar/callback/
```

Callback Railway:

```env
GOOGLE_CALENDAR_REDIRECT_URI=https://tu-app.up.railway.app/turnos/google-calendar/callback/
```

## Storage Clínico

Local:

```env
MEDIA_STORAGE_BACKEND=django.core.files.storage.FileSystemStorage
```

Supabase Storage:

```env
MEDIA_STORAGE_BACKEND=config.storage_backends.SupabaseStorage
SUPABASE_STORAGE_URL=https://tu-proyecto.supabase.co
SUPABASE_STORAGE_BUCKET=historias-clinicas
SUPABASE_STORAGE_SERVICE_ROLE_KEY=clave-service-role
SUPABASE_STORAGE_TIMEOUT=30
SUPABASE_STORAGE_CACHE_CONTROL=3600
SUPABASE_STORAGE_SIGNED_URL_SECONDS=300
```

El bucket debe ser privado. Django genera URLs firmadas temporales para abrir adjuntos.

## Deploy

```env
WEB_CONCURRENCY=2
```

Gunicorn usa esta variable en `scripts/start.sh`.

## Validación de Configuración

Desde `app/`:

```powershell
python manage.py check
python manage.py probar_email tu-email@example.com
python manage.py probar_storage_historias
```

No todos los comandos son obligatorios en local: `probar_storage_historias` requiere variables de Supabase si se usa ese backend.
