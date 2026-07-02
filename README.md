# Gestor de Turnos Odontológico

Aplicación web en Django para gestionar la agenda de un consultorio odontológico, con panel interno para el equipo del consultorio e interfaz pública para pacientes.

El proyecto busca resolver un flujo real de trabajo: cargar pacientes, administrar turnos, validar disponibilidad, confirmar solicitudes públicas, mantener historia clínica, adjuntar archivos clínicos, sincronizar con Google Calendar y enviar notificaciones por email.

> No hay capturas versionadas en el repositorio por ahora. La documentación describe el estado real del código actual.

## Estado Actual

El sistema ya cuenta con una base funcional para uso controlado en staging:

- Landing pública para pacientes en `/`.
- Solicitud pública de turnos en `/turnos/solicitar/`.
- Consulta, cancelación y reprogramación pública de turnos por DNI en `/turnos/cancelar/`.
- Login interno separado en `/cuentas/login/`.
- Dashboard interno en `/inicio/`.
- Gestión visual de pacientes, turnos, agenda diaria/semanal e historia clínica.
- Roles internos con grupos de Django: `Recepcionista`, `Odontologo` y `Administrador`.
- Asociación paciente-odontólogo y derivación de pacientes.
- Ficha odontológica combinada con datos personales, administrativos y clínicos.
- Historia clínica con adjuntos clínicos. El odontograma queda desactivado como implementación futura.
- Turnos con estados `Pendiente`, `Confirmado` y `Cancelado`.
- Turnos internos confirmados automáticamente.
- Solicitudes públicas guardadas como pendientes con duración inicial de 30 minutos.
- Confirmación de turnos pendientes con duración real y validación de superposición.
- Emails transaccionales para solicitud, confirmación, cancelación, reprogramación y recordatorios.
- Google Calendar OAuth por odontólogo y sincronización de eventos.
- Supabase PostgreSQL como base de datos para deploy.
- Supabase Storage privado para adjuntos clínicos.
- Deploy preparado para Railway con Gunicorn, WhiteNoise y scripts de build/start/release.
- Tests automatizados para dominio, permisos, turnos, agenda, emails, Google Calendar, historia clínica e interfaz pública. El módulo de odontograma conserva pruebas propias para una reactivación controlada.

### Estado Del Odontograma

El código del odontograma se conserva en el repositorio como una implementación experimental y futura. La app `odontogramas`, sus modelos, migraciones, templates, assets y tests no fueron eliminados, pero la funcionalidad está desactivada por defecto mediante `ODONTOGRAMA_FEATURE_ENABLED=False`.

Mientras ese flag permanezca apagado, el odontograma no se muestra en las pantallas clínicas, no se integra al alta de entradas de historia clínica y las URLs directas del módulo responden como no disponibles. Para retomarlo más adelante se debe completar la validación funcional/UX y activarlo explícitamente por configuración.

## Stack Tecnológico

| Área | Tecnología |
| --- | --- |
| Backend | Python 3.13, Django 6.0.4 |
| Base local | SQLite si `DATABASE_URL` está vacío |
| Base deploy | Supabase PostgreSQL mediante `DATABASE_URL` |
| Archivos clínicos | Supabase Storage privado o filesystem local |
| Email | Consola, SMTP o backend HTTP propio para Resend/Brevo |
| Calendario | Google Calendar API con OAuth por odontólogo |
| Deploy | Railway |
| Servidor WSGI | Gunicorn |
| Estáticos | WhiteNoise + `collectstatic` |
| Tests | Django TestCase |

## Arquitectura General

El proyecto está organizado por apps Django con responsabilidades separadas:

| App | Responsabilidad |
| --- | --- |
| `config` | Settings, URLs globales, base de datos, email, storage y configuración de entorno. |
| `usuarios` | Login interno, dashboard, perfil de usuario, roles, permisos y mixins. |
| `pacientes` | Datos personales, ficha odontológica, asociación con odontólogos, derivación y borrado seguro. |
| `turnos` | Odontólogos, disponibilidad, turnos, agenda, solicitud pública, emails, recordatorios y Google Calendar. |
| `historias` | Historia clínica, adjuntos clínicos, auditoría básica y permisos clínicos. |
| `odontogramas` | Implementación experimental del odontograma FDI, conservada detrás del feature flag `ODONTOGRAMA_FEATURE_ENABLED` para retomarla en una etapa futura. |

Documentación técnica principal:

- [Arquitectura](docs/arquitectura.md)
- [Configuración](docs/configuracion.md)
- [Flujo de turnos](docs/flujo-turnos.md)
- [Deploy en Railway usando Supabase](docs/deploy.md)
- [Recordatorios automáticos](docs/recordatorios.md)
- [Supabase Storage](docs/supabase_storage.md)
- [Backups](docs/backups.md)
- [Email por API HTTP](docs/email_api.md)
- [Seguridad antes de producción](docs/seguridad_produccion.md)
- [Rendimiento y fluidez](docs/rendimiento_y_fluidez.md)

## Estructura Del Repositorio

```text
gestor-turnos-odontologia/
├── README.md
├── LICENSE
├── requirements.txt
├── Procfile
├── railway.json
├── .env.example
├── .env.railway-supabase.example
├── .github/
│   └── workflows/
│       └── staging_recordatorios.yml
├── docs/
├── scripts/
│   ├── build.sh
│   ├── release.sh
│   ├── start.sh
│   ├── recordatorios.sh
│   ├── backup_postgresql.sh
│   ├── backup_postgresql_docker.ps1
│   ├── probar_restore_postgresql_docker.ps1
│   └── backup_storage_historias.ps1
└── app/
    ├── manage.py
    ├── config/
    ├── usuarios/
    ├── pacientes/
    ├── turnos/
    ├── historias/
    ├── odontogramas/
    └── templates/
```

## Instalación Local

Desde la carpeta del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
cd app
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

URLs locales principales:

```text
http://127.0.0.1:8000/                       # landing pública para pacientes
http://127.0.0.1:8000/turnos/solicitar/       # solicitud pública
http://127.0.0.1:8000/turnos/cancelar/        # consulta/cancelación pública por DNI
http://127.0.0.1:8000/cuentas/login/          # login interno
http://127.0.0.1:8000/inicio/                 # dashboard interno
http://127.0.0.1:8000/admin/                  # Django Admin
```

## Variables de Entorno

El proyecto carga `.env` desde la raíz del repo y desde `app/.env` si existe. `.env` no se versiona.

Plantillas:

- `.env.example`: desarrollo local.
- `.env.railway-supabase.example`: Railway + Supabase.

Variables principales:

| Grupo | Variables |
| --- | --- |
| Django | `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_LOG_LEVEL` |
| Feature flags | `ODONTOGRAMA_FEATURE_ENABLED` |
| Seguridad pública de turnos | `TURNOS_PUBLIC_ACTION_TOKEN_SECONDS`, `TURNOS_PUBLIC_DNI_RATE_LIMIT_ATTEMPTS`, `TURNOS_PUBLIC_DNI_RATE_LIMIT_SECONDS` |
| Cifrado OAuth | `OAUTH_TOKEN_ENCRYPTION_KEY` |
| Seguridad HTTPS | `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SESSION_COOKIE_SECURE`, `DJANGO_CSRF_COOKIE_SECURE`, `DJANGO_SECURE_PROXY_SSL_HEADER`, `DJANGO_SECURE_HSTS_SECONDS`, `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS`, `DJANGO_SECURE_HSTS_PRELOAD` |
| Base de datos | `DATABASE_URL` |
| Email | `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_TIMEOUT`, `DEFAULT_FROM_EMAIL`, `EMAIL_API_PROVIDER`, `EMAIL_API_KEY`, `EMAIL_API_URL` |
| Recordatorios | `TURNOS_RECORDATORIO_HORAS` |
| Google Calendar | `GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET`, `GOOGLE_CALENDAR_CLIENT_SECRETS_FILE`, `GOOGLE_CALENDAR_REDIRECT_URI`, `GOOGLE_CALENDAR_SCOPES` |
| Storage clínico | `MEDIA_STORAGE_BACKEND`, `SUPABASE_STORAGE_URL`, `SUPABASE_STORAGE_BUCKET`, `SUPABASE_STORAGE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_TIMEOUT`, `SUPABASE_STORAGE_CACHE_CONTROL`, `SUPABASE_STORAGE_SIGNED_URL_SECONDS` |
| Deploy | `WEB_CONCURRENCY` |

Detalle completo: [docs/configuracion.md](docs/configuracion.md).

## Flujos Principales

### Paciente Público

1. Ingresa a `/`.
2. Solicita turno desde `/turnos/solicitar/`.
3. Elige odontólogo, fecha y horario disponible.
4. Completa nombre, apellido, teléfono y datos opcionales.
5. El turno queda `Pendiente` con duración inicial de 30 minutos.
6. Puede consultar sus turnos por DNI en `/turnos/cancelar/`.
7. Puede cancelar turnos pendientes/confirmados.
8. Puede reprogramar solo turnos pendientes.

### Equipo Interno

1. Ingresa por `/cuentas/login/`.
2. Ve un dashboard simple en `/inicio/`.
3. Gestiona pacientes, turnos y agenda según rol.
4. Confirma turnos pendientes eligiendo duración real.
5. Reprograma o cancela turnos.
6. Gestiona ficha odontológica, historia clínica y adjuntos clínicos.
7. Cada odontólogo puede conectar su propia cuenta de Google Calendar.

Más detalle: [docs/flujo-turnos.md](docs/flujo-turnos.md).

## Reglas de Negocio de Turnos

- Estados válidos: `Pendiente`, `Confirmado`, `Cancelado`.
- Los turnos cancelados no bloquean disponibilidad.
- Los turnos pendientes y confirmados sí bloquean disponibilidad.
- Los turnos internos se crean confirmados automáticamente.
- Las solicitudes públicas se crean pendientes y duran 30 minutos inicialmente.
- La confirmación interna permite elegir duración real.
- Si la duración elegida se superpone con otro turno activo del mismo odontólogo, no confirma y muestra el conflicto.
- La reprogramación valida disponibilidad y superposiciones.
- La disponibilidad se define por odontólogo y día de semana.
- No se pueden crear turnos para odontólogos inactivos.

## Google Calendar

Cada odontólogo puede conectar su propia cuenta de Google Calendar desde:

```text
/turnos/google-calendar/
```

El sistema guarda tokens OAuth cifrados en `GoogleCalendarConexion` y conserva el `google_calendar_event_id` en cada turno sincronizado.

Cuando hay conexión activa:

- al crear o confirmar un turno, intenta crear/actualizar evento;
- al reprogramar, actualiza el evento;
- al cancelar, cancela o elimina el evento según la lógica de integración;
- si Google falla, el turno se conserva y el error queda registrado para revisión.

Redirect URI local:

```text
http://127.0.0.1:8000/turnos/google-calendar/callback/
```

Redirect URI en Railway:

```text
https://TU-DOMINIO-RAILWAY/turnos/google-calendar/callback/
```

## Email y Recordatorios

El proyecto usa plantillas en `app/turnos/templates/turnos/emails/`.

Notificaciones implementadas:

- solicitud recibida;
- turno confirmado;
- turno cancelado;
- turno reprogramado;
- recordatorio de turno confirmado próximo.

Comandos útiles:

```powershell
cd app
python manage.py probar_email tu-email@example.com
python manage.py probar_notificaciones_email tu-email@example.com
python manage.py enviar_recordatorios_email --horas 24
```

El script de scheduler es:

```bash
bash scripts/recordatorios.sh
```

## Archivos Clínicos y Backups

Los adjuntos de historia clínica aceptan PDF, imágenes y DICOM hasta 10 MB por archivo. En local pueden guardarse en `app/media/`; en deploy se recomienda Supabase Storage privado.

Prueba de storage:

```powershell
cd app
python manage.py probar_storage_historias
```

Backups:

```powershell
.\scripts\backup_postgresql_docker.ps1
.\scripts\probar_restore_postgresql_docker.ps1
.\scripts\backup_storage_historias.ps1
```

Guía completa: [docs/backups.md](docs/backups.md).

## Deploy en Railway

El repositorio está preparado con `railway.json`:

```json
{
  "build": {
    "buildCommand": "bash scripts/build.sh"
  },
  "deploy": {
    "startCommand": "bash scripts/start.sh",
    "preDeployCommand": "bash scripts/release.sh"
  }
}
```

Comandos:

```bash
bash scripts/build.sh      # instala dependencias y collectstatic
bash scripts/release.sh    # migraciones
bash scripts/start.sh      # gunicorn
```

Railway hostea la aplicación. Supabase mantiene PostgreSQL y Storage. No hace falta crear una base PostgreSQL en Railway para este proyecto.

Guía completa: [docs/deploy.md](docs/deploy.md).

## Tests y Validación

Desde `app/`:

```powershell
python manage.py check
python manage.py test
python manage.py collectstatic --noinput
```

## Seguridad y Privacidad

- `.env`, tokens, credenciales OAuth y claves reales no se versionan.
- La interfaz pública no muestra historia clínica ni datos sensibles.
- Las acciones públicas de cancelación/reprogramación validan DNI contra el turno.
- Las vistas internas requieren login.
- La historia clínica tiene permisos clínicos propios.
- El odontograma conserva permisos internos, pero queda desactivado por defecto y fuera del flujo activo hasta una implementación futura.
- Los adjuntos clínicos se sirven a través de Django y pueden mantenerse en bucket privado.
- Antes de producción real se recomienda rotar secretos expuestos durante configuración, validar backups, dominio definitivo, logs y permisos.

## Roadmap

Prioridades sugeridas:

1. Verificar deploy del último commit en Railway cuando el incidente de builds esté resuelto.
2. Probar flujo público completo en Railway: solicitud, consulta por DNI, cancelación y reprogramación.
3. Ejecutar migraciones después de cada deploy con cambios de modelo.
4. Completar prueba de backups base + Storage en entorno separado.
5. Rotar secretos expuestos durante configuración inicial.
6. Profundizar auditoría clínica y logs operativos.
7. Mejorar reportes: turnos por período, ausencias, pendientes y métricas del consultorio.
8. Evaluar dominio propio y endurecimiento final de HTTPS/HSTS.

## Licencia

Este proyecto está publicado bajo la licencia incluida en [LICENSE](LICENSE).
