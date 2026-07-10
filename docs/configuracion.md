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

## Acceso a Datos Clinicos

| Variable | Default | Uso |
| --- | --- | --- |
| `DATOS_CLINICOS_COMPARTIDOS_ENTRE_ODONTOLOGOS` | `False` | Si queda apagado, un odontologo normal solo lee datos clinicos de pacientes activos asociados. Si se activa, habilita lectura compartida entre odontologos con auditoria y sin permisos de escritura. |
| `ACCESO_CLINICO_EMERGENCIA_SECONDS` | `900` | Duracion del acceso clinico de emergencia para superusuarios. Es por paciente, exige motivo y queda auditado. |

En produccion se recomienda mantener `DATOS_CLINICOS_COMPARTIDOS_ENTRE_ODONTOLOGOS=False` salvo una decision explicita de politica clinica.

## Perfil del Consultorio

La identidad visible del consultorio se configura desde:

```text
/configuracion/consultorio/
```

Es una configuración singleton de la app `consultorio`, con `pk=1`. Permite editar nombre comercial, nombre corto, logo, datos de contacto, textos de portada, política de cancelación, color principal y reglas de reserva pública sin modificar código ni redeployar.

Puntos importantes:

- No agrega multi-tenancy.
- No guarda secretos ni variables de entorno.
- No modifica `DEFAULT_FROM_EMAIL`; esa dirección sigue dependiendo del dominio verificado del proveedor de email.
- El logo se guarda con el storage default de Django, local o Supabase Storage según `MEDIA_STORAGE_BACKEND`.
- Cuando se reemplaza, quita o restaura el logo, el archivo anterior se elimina como limpieza no crítica después del commit de base de datos. Si esa limpieza falla en el storage, la configuración nueva queda guardada y se registra un warning seguro.
- Los errores al subir el logo nuevo siguen siendo errores reales: no se silencian y no disparan el borrado del logo anterior.
- El context processor global expone defaults seguros si la fila todavía no existe y no escribe en base durante requests públicos.

### Reservas públicas

La misma pantalla incluye una sección `Reservas públicas` con parámetros persistidos en base:

| Campo | Default | Rango | Uso |
| --- | --- | --- | --- |
| `ventana_reserva_publica_dias` | `14` | `1` a `90` | Cantidad de días visibles y reservables desde hoy inclusive. |
| `permitir_reserva_publica_mismo_dia` | `True` | Booleano | Permite que pacientes tomen turnos para la fecha actual si cumplen la anticipación mínima. |
| `anticipacion_minima_reserva_publica_minutos` | `120` | `0` a `10080` | Tiempo mínimo entre el momento actual y el inicio del turno público. |

Estas reglas aplican a selección pública, endpoint JSON de horarios, formulario final, URLs directas y reprogramación pública. Los turnos internos no usan esta ventana pública, pero sí respetan disponibilidad, superposición y excepciones de agenda.

## Seguridad del Flujo Publico de Turnos

El flujo publico de autogestion usa un desafio OTP por email, sesion temporal verificada, permisos persistentes de un solo uso por turno y rate limiting en cache. En produccion `REDIS_URL` debe apuntar a Redis para que los limites funcionen entre procesos/instancias.

La solicitud publica de un turno crea un `Turno` pendiente y conserva una `SolicitudTurnoPublica` asociada como auditoria. El email es obligatorio para pacientes nuevos y para pacientes existentes activos sin email registrado. En pacientes existentes, el email enviado desde la web no sobrescribe automaticamente `Paciente.email`: queda como propuesta en `SolicitudTurnoPublica.email_enviado` y se aplica solo si un usuario autorizado selecciona ese campo durante la revision.

El POST final de creación de solicitud pública agrega defensa contra abuso: rate limit por IP y DNI hasheados, token de idempotencia por formulario, deduplicación exacta, máximo configurable de solicitudes pendientes por DNI y Turnstile progresivo. Los mensajes públicos son neutrales y no indican si el DNI existe, si el paciente está archivado, qué límite se alcanzó ni cuántos turnos pendientes tiene.

El OTP publico consulta exclusivamente el email persistido en `Paciente.email`. Un email propuesto no se usa para codigos de acceso ni para notificaciones sensibles antes de la revision interna. Cuando se aplica un email nuevo, `email_verificado_en` queda en `None` hasta que el paciente complete correctamente el OTP. Las solicitudes que no generan turno, como pacientes archivados, quedan en `/turnos/alertas-administrativas/`.

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
| `TURNOS_PUBLIC_BOOKING_IP_LIMIT` | `10` | Intentos máximos de creación de solicitud pública por IP hasheada dentro de la ventana. `0` deshabilita este límite. |
| `TURNOS_PUBLIC_BOOKING_IP_WINDOW_SECONDS` | `900` | Ventana del rate limit de creación por IP. |
| `TURNOS_PUBLIC_BOOKING_DNI_LIMIT` | `5` | Intentos máximos de creación de solicitud pública por DNI normalizado y hasheado dentro de la ventana. `0` deshabilita este límite. |
| `TURNOS_PUBLIC_BOOKING_DNI_WINDOW_SECONDS` | `3600` | Ventana del rate limit de creación por DNI. |
| `TURNOS_PUBLIC_BOOKING_TURNSTILE_AFTER_ATTEMPTS` | `3` | Intentos previos desde los que se exige Turnstile en la creación pública cuando `TURNSTILE_ENABLED=True`. `0` lo exige desde el primer POST. |
| `TURNOS_PUBLIC_BOOKING_MAX_PENDING_PER_DNI` | `2` | Máximo de solicitudes públicas futuras con turno pendiente para el mismo DNI. `0` lo deshabilita y no se recomienda en producción. |
| `TURNOS_PUBLIC_BOOKING_IDEMPOTENCY_SECONDS` | `3600` | Vigencia del token de idempotencia usado para evitar doble click, recarga o reenvío del POST. |
| `TURNOS_PUBLIC_BOOKING_DUPLICATE_WINDOW_SECONDS` | `86400` | Ventana para reutilizar alertas administrativas sin turno y evitar duplicados repetidos. Los turnos activos exactos se deduplican mientras sigan pendientes o confirmados. |
| `TURNSTILE_ENABLED` | `False` | Activa Cloudflare Turnstile como desafío complementario; no reemplaza rate limiting ni máximos duros. |
| `TURNSTILE_SITE_KEY` | vacio | Site key publica de Turnstile. |
| `TURNSTILE_SECRET_KEY` | vacio | Secret key privada de Turnstile. |
| `TURNSTILE_REQUIRED_AFTER_ATTEMPTS` | `3` | Umbral de intentos desde el cual se exige Turnstile en el flujo OTP de autogestion. |
| `TURNSTILE_TIMEOUT_SECONDS` | `5` | Timeout para verificar Turnstile. |

En desarrollo y tests puede usarse `LocMemCache`; en producción debe usarse Redis compartido y `TURNOS_PUBLIC_REDIS_REQUIRED=True`. Si Redis falla con esa configuración, la creación pública responde 503 con un mensaje genérico para evitar solicitudes ilimitadas por proceso. Si Turnstile está apagado, siguen activos los límites por IP/DNI, idempotencia, deduplicación y máximo de pendientes.

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

## Herramientas de Desarrollo

Las dependencias de calidad están separadas en `requirements-dev.txt` para no aumentar el entorno de producción de Railway.

Desde la raíz del repo:

```powershell
pip install -r requirements-dev.txt
python -m black --check app scripts --exclude migrations
python -m ruff check app scripts
python -m mypy app/config app/consultorio app/turnos/public_access app/turnos/solicitudes_publicas app/turnos/forms app/turnos/views
python -m bandit -r app -x "*/migrations/*,*/tests/*" -ll
python -m pip_audit -r requirements.txt
```

Coverage se mide desde `app/` y genera `coverage.xml`:

```powershell
python -m coverage erase
python -m coverage run manage.py test --verbosity 2
python -m coverage report -m
python -m coverage xml
```

Playwright se usa solo para smoke tests E2E:

```powershell
python -m playwright install chromium
cd app
python manage.py test turnos.tests_e2e --verbosity 2
```
