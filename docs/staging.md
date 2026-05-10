# Entorno de staging

Esta guia define el primer entorno de staging del proyecto.

Staging no es produccion final. Es un ambiente publico y controlado para probar el flujo completo antes de usar el sistema con pacientes reales.

## Estado actual

El staging inicial ya fue creado y probado.

```text
URL: https://gestor-turnos-odontologia-staging.onrender.com
App: Render Free Web Service
Base de datos: Supabase Free Postgres
```

Funciona actualmente:

- Deploy desde GitHub hacia Render.
- Aplicacion Django levantada con Gunicorn.
- Archivos estaticos servidos con WhiteNoise.
- Base PostgreSQL de Supabase conectada y migrada.
- Admin de Django accesible en staging.
- Superusuario creado en la base de staging.
- Google OAuth configurado para la URL publica de Render.
- Pantalla de conexion de Google Calendar funcionando para usuarios con perfil de odontologo.
- Backend de email por API HTTP preparado para Resend o Brevo.
- Adjuntos clinicos preparados para bucket privado de Supabase Storage.

Pendiente:

- Rotar secretos que se usaron durante la configuracion inicial.
- Probar el flujo completo de turno en staging con datos de prueba controlados.
- Cargar una API key real de email en Render y validar envio desde staging.
- Activar recordatorios programados con GitHub Actions.
- Probar backup y restauracion.
- Crear bucket privado de Supabase Storage y probar adjuntos clinicos.
- Definir dominio real y configuracion HTTPS final antes de produccion.

Nota sobre emails:

En Render Free los puertos SMTP comunes estan bloqueados. Por eso el proyecto incluye `config.email_backends.EmailApiBackend`, que envia por API HTTP usando Resend o Brevo. Para que staging envie emails reales falta cargar `EMAIL_API_KEY`, `EMAIL_API_PROVIDER` y `DEFAULT_FROM_EMAIL` en Render.

## Decision

El entorno gratuito inicial queda definido asi:

```text
App Django: Render Free Web Service
Base de datos: Supabase Free Postgres
Recordatorios: GitHub Actions Scheduled Workflow
Repositorio: GitHub
```

## Objetivo del staging

El staging tiene que permitir probar:

- Login interno.
- Admin de Django.
- Pacientes, odontologos y turnos.
- Solicitud publica de turnos.
- Confirmacion, reprogramacion y cancelacion.
- Sincronizacion con Google Calendar.
- Emails, aunque al principio pueden quedar en consola por el bloqueo SMTP de Render Free.
- Recordatorios automaticos desde GitHub Actions.

## 1. Preparar Supabase PostgreSQL

1. Crear un proyecto en Supabase.
2. Obtener la connection string de PostgreSQL.
3. Usar la URL en formato `DATABASE_URL`.
4. Agregar `?sslmode=require` si la URL no lo trae.

Ejemplo:

```env
DATABASE_URL=postgres://usuario:password@host.supabase.co:5432/postgres?sslmode=require
```

Notas importantes:

- Supabase Free es util para staging.
- Antes de produccion real hay que definir backups propios y probar restauracion.
- Los backups no deben guardarse en el repositorio.

## 2. Preparar Render

El repositorio incluye `render.yaml`, por lo que se puede crear el servicio desde Blueprint.

Tambien se puede configurar manualmente:

```text
Build Command: bash scripts/build.sh
Start Command: bash scripts/start.sh
```

Si Render permite comando de pre deploy, usar:

```text
bash scripts/release.sh
```

Si no, despues del primer deploy ejecutar migraciones desde la shell de Render:

```bash
cd app
python manage.py migrate --noinput
```

Para crear el primer usuario administrador:

```bash
cd app
python manage.py createsuperuser
```

## 3. Variables de entorno en Render

Usar `.env.render-supabase.example` como plantilla.

Variables principales:

```env
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=generar-una-clave-segura
DJANGO_ALLOWED_HOSTS=tu-app.onrender.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://tu-app.onrender.com
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
DJANGO_SECURE_PROXY_SSL_HEADER=True
DJANGO_LOG_LEVEL=INFO
DATABASE_URL=postgres://usuario:password@host.supabase.co:5432/postgres?sslmode=require
WEB_CONCURRENCY=2
```

Adjuntos clinicos:

```env
MEDIA_STORAGE_BACKEND=config.storage_backends.SupabaseStorage
SUPABASE_STORAGE_URL=https://tu-proyecto.supabase.co
SUPABASE_STORAGE_BUCKET=historias-clinicas
SUPABASE_STORAGE_SERVICE_ROLE_KEY=valor-real
SUPABASE_STORAGE_TIMEOUT=30
SUPABASE_STORAGE_CACHE_CONTROL=3600
SUPABASE_STORAGE_SIGNED_URL_SECONDS=300
```

El bucket `historias-clinicas` debe ser privado. La clave `SUPABASE_STORAGE_SERVICE_ROLE_KEY` no debe exponerse ni subirse al repositorio.

Google Calendar:

```env
GOOGLE_CALENDAR_CLIENT_ID=valor-real
GOOGLE_CALENDAR_CLIENT_SECRET=valor-real
GOOGLE_CALENDAR_REDIRECT_URI=https://tu-app.onrender.com/turnos/google-calendar/callback/
GOOGLE_CALENDAR_SCOPES=https://www.googleapis.com/auth/calendar.events
```

Email recomendado para Render Free:

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=clave-real-del-proveedor
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
```

Para desarrollo o diagnostico se puede volver temporalmente a `django.core.mail.backends.console.EmailBackend` y revisar los emails en logs.

## 4. Configurar Google OAuth

En Google Cloud, dentro del cliente OAuth web, agregar:

```text
https://tu-app.onrender.com/turnos/google-calendar/callback/
```

Ese valor debe coincidir exactamente con:

```env
GOOGLE_CALENDAR_REDIRECT_URI
```

Si la pantalla de consentimiento esta en modo Testing, agregar como test user el email que va a conectar Google Calendar.

## 5. Configurar recordatorios con GitHub Actions

El workflow vive en:

```text
.github/workflows/staging_recordatorios.yml
```

Por seguridad queda desactivado hasta crear esta repository variable:

```text
STAGING_RECORDATORIOS_ACTIVO=true
```

Crear estos repository secrets en GitHub:

```text
STAGING_DJANGO_SECRET_KEY
STAGING_DJANGO_ALLOWED_HOSTS
STAGING_DJANGO_CSRF_TRUSTED_ORIGINS
STAGING_DATABASE_URL
STAGING_EMAIL_BACKEND
STAGING_EMAIL_HOST
STAGING_EMAIL_PORT
STAGING_EMAIL_HOST_USER
STAGING_EMAIL_HOST_PASSWORD
STAGING_EMAIL_USE_TLS
STAGING_EMAIL_USE_SSL
STAGING_EMAIL_API_PROVIDER
STAGING_EMAIL_API_KEY
STAGING_DEFAULT_FROM_EMAIL
STAGING_GOOGLE_CALENDAR_CLIENT_ID
STAGING_GOOGLE_CALENDAR_CLIENT_SECRET
STAGING_GOOGLE_CALENDAR_REDIRECT_URI
```

El workflow se puede correr manualmente con `workflow_dispatch` o automaticamente cada hora.

## 6. Problema pendiente de SMTP

Render Free bloquea salidas SMTP por puertos comunes como `25`, `465` y `587`.

Por eso el staging queda preparado para email por API HTTP. Para enviar emails reales desde staging hay que elegir una de estas opciones:

- Usar Resend con dominio o remitente verificado.
- Usar Brevo con remitente verificado.
- Usar un proveedor o plan que permita SMTP saliente.
- Ejecutar envios programados desde GitHub Actions si el proveedor SMTP lo permite.
- Pasar a un plan pago cuando el consultorio necesite uso real continuo.

## 7. Prueba completa del flujo

Checklist del primer staging:

- Abrir la URL de Render.
- Entrar al admin.
- Crear superusuario si todavia no existe.
- Cargar un odontologo activo.
- Cargar disponibilidad.
- Conectar Google Calendar desde la pantalla interna.
- Solicitar un turno desde la pagina publica.
- Ver que el turno queda pendiente.
- Confirmar el turno.
- Ver que se crea o actualiza el evento en Google Calendar.
- Reprogramar el turno.
- Ver que cambia el evento en Google Calendar.
- Cancelar el turno.
- Ver que el evento se cancela o elimina.
- Revisar emails en logs o en casilla real segun backend configurado.
- Ejecutar el workflow de recordatorios en GitHub Actions.
- Crear un backup logico de prueba con `bash scripts/backup_postgresql.sh`.
- En Windows, crear backup con `.\scripts\backup_postgresql_docker.ps1`.
- Probar restauracion en una base separada antes de usar datos reales.
- En Windows, probar restauracion con `.\scripts\probar_restore_postgresql_docker.ps1`.
- Crear backup de adjuntos clinicos con `.\scripts\backup_storage_historias.ps1`.
- Verificar que el backup de Storage tenga `manifest.json` y archivos descargados.

## Listo para avanzar cuando

- La app responde en Render.
- PostgreSQL de Supabase guarda datos reales de staging.
- Google OAuth conecta correctamente.
- El flujo de turno completo funciona.
- Los emails quedan validados por consola o por un proveedor compatible.
- El workflow de recordatorios corre sin errores.
- Existe una estrategia de backup y restauracion probada para PostgreSQL y Storage.
