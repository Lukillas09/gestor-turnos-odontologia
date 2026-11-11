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
DATOS_CLINICOS_COMPARTIDOS_ENTRE_ODONTOLOGOS=False
ACCESO_CLINICO_EMERGENCIA_SECONDS=900
```

### Base de datos Supabase

```env
DATABASE_URL=postgres://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

La URL debe venir de Supabase y debe incluir `sslmode=require`. En Railway usar el **pooler de
Supavisor en modo sesion** (puerto 5432, usuario `postgres.<ref>`): es IPv4. La conexion directa
`db.<ref>.supabase.co` resuelve solo a IPv6 salvo que el proyecto tenga el complemento IPv4, y
Railway no sale por IPv6. El modo transaccion (puerto 6543) pierde el estado de sesion y se
documenta en [postgresql.md](postgresql.md).

Variables opcionales de conexion (segundos; `0` desactiva el limite), con sus defaults:

```env
DATABASE_CONN_MAX_AGE=60
DATABASE_CONNECT_TIMEOUT_SECONDS=5
DATABASE_LOCK_TIMEOUT_SECONDS=5
DATABASE_STATEMENT_TIMEOUT_SECONDS=20
DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS=60
```

Su detalle, como verificarlas con `SHOW` desde la aplicacion y el comportamiento con el pooler en
modo transaccion estan en [postgresql.md](postgresql.md).

### Protecciones públicas en PostgreSQL

Rate limiting e idempotencia se almacenan en la misma base PostgreSQL/Supabase que usa Django. Redis no es necesario para iniciar la aplicación ni para compartir límites entre workers. Configuración recomendada en Railway:

```env
REDIS_URL=
TURNOS_PUBLIC_REDIS_REQUIRED=False
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
TURNOS_PUBLIC_BOOKING_IP_LIMIT=10
TURNOS_PUBLIC_BOOKING_IP_WINDOW_SECONDS=900
TURNOS_PUBLIC_BOOKING_DNI_LIMIT=5
TURNOS_PUBLIC_BOOKING_DNI_WINDOW_SECONDS=3600
TURNOS_PUBLIC_BOOKING_TURNSTILE_AFTER_ATTEMPTS=3
TURNOS_PUBLIC_BOOKING_MAX_PENDING_PER_DNI=2
TURNOS_PUBLIC_BOOKING_IDEMPOTENCY_SECONDS=3600
TURNOS_PUBLIC_BOOKING_PROCESSING_SECONDS=120
TURNOS_PUBLIC_BOOKING_DUPLICATE_WINDOW_SECONDS=86400
TURNOS_PUBLIC_BOOKING_NEARBY_DAYS_LIMIT=14
TURNOS_PUBLIC_BOOKING_HORARIOS_CACHE_SECONDS=0
WEB_CONCURRENCY=2
```

La migración crea `LimitePublico` e `IdempotenciaSolicitudPublica`; `scripts/release.sh` la aplica con el resto antes de iniciar el servicio. Las ventanas de rate limit son fijas y la idempotencia usa un lease corto para recuperar operaciones si Railway detiene un worker.

Redis puede agregarse en el futuro como acelerador configurando `REDIS_URL` y un TTL de horarios mayor a cero. Sin Redis, Django usa `LocMemCache` únicamente para esa optimización. Ninguna caché interviene en OTP, acciones, rate limiting, idempotencia o la validación final de una reserva.

Para limpiar filas expiradas durante mantenimiento:

```bash
python manage.py limpiar_protecciones_publicas --dry-run
python manage.py limpiar_protecciones_publicas --batch-size 500 --max-batches 20
```

El comando puede ejecutarse manualmente o desde el mecanismo periódico ya disponible. No se deben agregar pings para mantener Railway despierto.

### Turnstile opcional

Crear las claves en Cloudflare Turnstile para el dominio real o el dominio de Railway. Luego cargar:

```env
TURNSTILE_ENABLED=True
TURNSTILE_SITE_KEY=site-key
TURNSTILE_SECRET_KEY=secret-key
TURNSTILE_VERIFY_URL=https://challenges.cloudflare.com/turnstile/v0/siteverify
TURNSTILE_REQUIRED_AFTER_ATTEMPTS=3
TURNOS_PUBLIC_BOOKING_TURNSTILE_AFTER_ATTEMPTS=3
TURNSTILE_TIMEOUT_SECONDS=5
```

Puede mantenerse apagado en staging inicial con `TURNSTILE_ENABLED=False`. Turnstile es complementario: un token válido no reinicia los contadores persistidos en PostgreSQL ni permite superar el límite duro por IP, DNI o máximo de pendientes.

### Deploy

```env
WEB_CONCURRENCY=2
GUNICORN_TIMEOUT=30
GUNICORN_MAX_REQUESTS=1000
GUNICORN_MAX_REQUESTS_JITTER=100
```

`scripts/start.sh` pasa estos valores a gunicorn. `GUNICORN_TIMEOUT` tambien lo lee Django:
`DATABASE_STATEMENT_TIMEOUT_SECONDS + DATABASE_LOCK_TIMEOUT_SECONDS` (25 s por defecto) debe ser
menor, para que la base corte una consulta o una espera de lock antes de que gunicorn mate al
worker a mitad de una transaccion. `GUNICORN_MAX_REQUESTS` recicla cada worker pasado ese numero de
requests (mas el jitter, para que no se reinicien todos a la vez); `0` lo desactiva.

Conexiones a la base: los workers `sync` usan una conexion cada uno, asi que el total es
`WEB_CONCURRENCY` mas las de `release.sh` y los cron (unas 4 con los valores por defecto), muy
por debajo del limite del pooler y de `max_connections`. Si se cambia el tipo de worker o se
sube `WEB_CONCURRENCY`, recalcular contra el limite del plan de Supabase.

`scripts/release.sh` aplica las migraciones sin `statement_timeout` y con `lock_timeout` de 30 s
(`DATABASE_MIGRATION_STATEMENT_TIMEOUT_SECONDS`, `DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS`).

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

`GOOGLE_CALENDAR_CLIENT_SECRET_FILE` permite usar como fallback un JSON OAuth web. El alias
legacy `GOOGLE_CALENDAR_CLIENT_SECRETS_FILE` sigue aceptado, pero no es necesario si se usan
`CLIENT_ID` y `CLIENT_SECRET` completos.

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
- `DATABASE_URL` apunta a Supabase (pooler en modo sesion, `sslmode=require`).
- Timeouts de la base verificados con `SHOW` desde la aplicacion
  (ver [postgresql.md](postgresql.md)).
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

## Checklist de entrega al cliente (Supabase)

Producción **no** debe estar en un plan que pause proyectos inactivos: un proyecto pausado deja
la aplicación sin base de datos hasta que alguien lo reactive, y los planes gratuitos de Supabase
lo hacen tras un período sin actividad. Antes de entregar el sistema, verificar en el panel de
Supabase y dejarlo anotado:

- El plan del proyecto es de pago y no pausa proyectos inactivos; el proyecto figura **activo**.
- Los backups automáticos (y, si el plan lo ofrece, Point-in-Time Recovery) están activos, y hay
  un backup lógico propio restaurado en prueba (ver [backups.md](backups.md)).
- El tamaño de cómputo y el límite de conexiones del plan cubren el uso previsto (ver la cuenta de
  conexiones en la sección Deploy y el límite del pooler).
- SSL obligatorio activo y `sslmode=require` en `DATABASE_URL`.
- Restricciones de red o complemento IPv4 revisados; Railway se conecta por el pooler IPv4.
- Los advisors de seguridad y rendimiento están revisados. Los índices "unused" de un entorno sin
  tráfico no se borran. RLS sin políticas es intencional: bloquea la Data API y Django usa un rol
  con `bypassrls`.
- `rls_auto_enable()` revisada según [supabase_rls_auto_enable.md](runbooks/supabase_rls_auto_enable.md).
- Alertas de uso (base de datos, conexiones, egress) configuradas y con destinatario responsable.
- Titularidad de las cuentas de Supabase y Railway definida y credenciales entregadas por un
  canal seguro.

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
