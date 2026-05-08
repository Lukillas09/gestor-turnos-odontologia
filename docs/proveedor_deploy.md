# Proveedor de deploy gratuito inicial

Esta guia deja definida la estrategia gratuita inicial para probar el sistema fuera de la maquina local.

## Decision

Para una primera version gratuita se elige:

```text
App Django: Render Free Web Service
Base de datos: Supabase Free Postgres
Recordatorios: GitHub Actions Scheduled Workflow
```

Esta decision prioriza costo cero y simplicidad.

## Por que no usar Render completo

Render es muy comodo para Django, pero en el plan gratuito tiene limites importantes:

- El Web Service gratuito se duerme si no recibe trafico.
- Render Postgres gratuito expira a los 30 dias.
- Render Cron Jobs no son gratis.
- Render Free bloquea trafico saliente por puertos SMTP comunes como `25`, `465` y `587`.

Por eso se usa Render para la app, pero no para la base de datos ni para los recordatorios.

## Por que Supabase para PostgreSQL

Supabase Free incluye PostgreSQL y permite tener una base persistente sin costo inicial.

Limites importantes:

- 500 MB de base de datos.
- Conviene mantener backups logicos propios fuera del proveedor.
- El proyecto puede pausarse por inactividad segun las condiciones del plan gratuito.
- No es ideal para produccion sensible sin plan pago, prueba de restauracion y backups propios.

Para un consultorio chico sirve como staging o primera prueba controlada, pero antes de depender de esto con pacientes reales conviene definir backups.

## Por que GitHub Actions para recordatorios

Ya existe el comando:

```bash
python manage.py enviar_recordatorios_email --horas 24 --fallar-si-hay-errores
```

GitHub Actions puede ejecutarlo con un schedule cron sin tener un proceso permanente.

Esto evita pagar Render Cron Jobs al principio.

## Variables para Render

Ejemplo base para el Web Service:

```env
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=generar-una-clave-segura
DJANGO_ALLOWED_HOSTS=tu-app.onrender.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://tu-app.onrender.com
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
DJANGO_SECURE_HSTS_SECONDS=0
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=False
DJANGO_SECURE_HSTS_PRELOAD=False
DJANGO_SECURE_PROXY_SSL_HEADER=True
DJANGO_LOG_LEVEL=INFO
DATABASE_URL=postgres://usuario:password@host.supabase.co:5432/postgres?sslmode=require
WEB_CONCURRENCY=2
TURNOS_RECORDATORIO_HORAS=24
```

Google Calendar:

```env
GOOGLE_CALENDAR_CLIENT_ID=
GOOGLE_CALENDAR_CLIENT_SECRET=
GOOGLE_CALENDAR_REDIRECT_URI=https://tu-app.onrender.com/turnos/google-calendar/callback/
GOOGLE_CALENDAR_SCOPES=https://www.googleapis.com/auth/calendar.events
```

Email:

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DEFAULT_FROM_EMAIL=turnos@localhost
```

El email queda pendiente de resolver porque Render Free bloquea puertos SMTP comunes. Para produccion real hay que cambiar a un proveedor compatible por API HTTP o mover la app a un plan/proveedor que permita SMTP.

## Variables para GitHub Actions

Guardar como repository secrets:

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
STAGING_DEFAULT_FROM_EMAIL
STAGING_GOOGLE_CALENDAR_CLIENT_ID
STAGING_GOOGLE_CALENDAR_CLIENT_SECRET
STAGING_GOOGLE_CALENDAR_REDIRECT_URI
```

Para los recordatorios, GitHub Actions debe poder conectarse a la base Supabase y al proveedor de email.

El workflow queda apagado hasta crear esta repository variable:

```text
STAGING_RECORDATORIOS_ACTIVO=true
```

## Problema pendiente: SMTP

Actualmente los emails del proyecto usan SMTP.

Render Free bloquea puertos salientes comunes de SMTP:

- `25`
- `465`
- `587`

Opciones para resolverlo mas adelante:

1. Cambiar notificaciones a una API HTTP de email.
2. Usar un proveedor/plan que permita SMTP saliente.
3. Ejecutar recordatorios y envio de emails desde GitHub Actions, si el proveedor SMTP permite conexion desde GitHub.
4. Evaluar un plan pago minimo cuando el consultorio use el sistema con pacientes reales.

## Riesgos de esta arquitectura gratuita

- La app en Render puede tardar cerca de un minuto en despertar.
- Supabase Free requiere una estrategia de backups propios y prueba de restauracion.
- GitHub Actions puede demorarse y no garantiza ejecucion al minuto exacto.
- Los emails requieren una decision adicional por el bloqueo SMTP de Render Free.

## Recomendacion

Usar esta arquitectura para staging, demo y primeras pruebas del consultorio.

La guia operativa para crearlo paso a paso esta en [staging.md](staging.md).

Antes de usarla como produccion diaria, definir:

- Backups de PostgreSQL.
- Estrategia definitiva de email.
- Dominio real.
- Monitoreo basico.
- Plan de salida si se supera el free tier.
- Endurecimiento documentado en [seguridad_produccion.md](seguridad_produccion.md).

## Referencias

- Render Free: https://render.com/docs/free
- Render Pricing: https://render.com/pricing
- Supabase Pricing: https://supabase.com/pricing
- GitHub Actions billing: https://docs.github.com/en/billing/managing-billing-for-your-products/about-billing-for-github-actions
