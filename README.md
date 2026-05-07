# Gestor de Turnos Odontológico

Aplicación web para administrar turnos de un consultorio odontológico.

El objetivo del proyecto es construir, paso a paso, un sistema que permita cargar pacientes, odontólogos y turnos desde una interfaz administrativa, evitando superposición de horarios y dejando preparada una futura integración con Google Calendar.

## Estado actual

El proyecto se encuentra en una etapa funcional de panel interno, reglas de agenda e integraciones iniciales.

Actualmente incluye:

- Proyecto Django configurado.
- App `pacientes` para gestionar datos de pacientes.
- App `turnos` para gestionar odontólogos y turnos.
- Panel de administración de Django mejorado.
- Login interno con autenticación de Django.
- Roles internos basados en grupos de Django.
- Vistas internas protegidas para usuarios autenticados.
- Listado y creación de pacientes desde vistas propias.
- Listado y creación de turnos desde vistas propias.
- Validación para evitar turnos superpuestos.
- Disponibilidad de odontólogos por día de semana.
- Bloqueo de días no laborables.
- Validación para evitar turnos en odontólogos inactivos.
- Cálculo de horarios disponibles.
- Agenda diaria y semanal simple.
- Agenda diaria por bloques horarios y colores por estado.
- Creación de turnos guiada por horarios disponibles.
- Formulario público para solicitar turnos.
- Campo preparado para guardar el ID del evento de Google Calendar.
- Modelo para guardar la conexión OAuth de Google Calendar por odontólogo.
- Sincronización preparada para crear, actualizar y cancelar eventos de Google Calendar.
- Flujo OAuth visual para conectar Google Calendar desde la web.
- Integración real con Google Calendar probada de punta a punta.
- Emails de confirmación para solicitud, confirmación y cancelación de turnos.
- Configuración SMTP real por variables de entorno, probada con envío real.
- Comando para probar las tres notificaciones de email con plantillas reales.
- Tests automatizados para la lógica de turnos, permisos, agenda, Google Calendar y emails.

Documentación técnica:

- [Arquitectura del proyecto](docs/arquitectura.md)

## Tecnologías

- Python 3.13
- Django 6.0.4
- SQLite para desarrollo local
- Django Admin como primera interfaz de gestión
- Variables de entorno para configuración sensible
- SMTP para emails transaccionales

## Estructura del proyecto

```text
gestor-turnos-odontologia/
├── README.md
├── LICENSE
├── .gitignore
├── requirements.txt
├── docs/
└── app/
    ├── manage.py
    ├── config/
    │   ├── settings.py
    │   ├── urls.py
    │   ├── asgi.py
    │   └── wsgi.py
    ├── pacientes/
    │   ├── admin.py
    │   ├── apps.py
    │   ├── models.py
    │   ├── tests.py
    │   └── migrations/
    ├── usuarios/
    │   ├── apps.py
    │   ├── roles.py
    │   ├── mixins.py
    │   ├── views.py
    │   ├── tests.py
    │   └── migrations/
    └── turnos/
        ├── admin.py
        ├── apps.py
        ├── google_calendar_oauth.py
        ├── google_calendar_sync.py
        ├── integrations/
        │   └── google_calendar.py
        ├── management/
        │   └── commands/
        │       ├── probar_email.py
        │       └── probar_notificaciones_email.py
        ├── models.py
        ├── notifications.py
        ├── templates/
        │   └── turnos/
        │       └── emails/
        ├── tests.py
        └── migrations/
```

## Instalación local

Desde la carpeta del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Crear el archivo local de variables de entorno:

```powershell
Copy-Item .env.example .env
```

El archivo `.env` es local y no se sube a Git. Ahi se configuran secretos, credenciales OAuth y valores propios del entorno.

Entrar a la carpeta de la aplicación Django:

```powershell
cd app
```

Aplicar migraciones:

```powershell
python manage.py migrate
```

Crear un superusuario para entrar al admin:

```powershell
python manage.py createsuperuser
```

Levantar el servidor local:

```powershell
python manage.py runserver
```

Después abrir:

```text
http://127.0.0.1:8000/admin/
```

## Uso del admin

Desde el panel de administración se pueden cargar y administrar:

- Pacientes
- Odontólogos
- Conexiones de odontólogos con Google Calendar
- Turnos

## Configuración segura

El proyecto lee configuración desde variables de entorno.

Variables principales:

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `EMAIL_BACKEND`
- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS`
- `EMAIL_USE_SSL`
- `EMAIL_TIMEOUT`
- `DEFAULT_FROM_EMAIL`
- `GOOGLE_CALENDAR_CLIENT_ID`
- `GOOGLE_CALENDAR_CLIENT_SECRET`
- `GOOGLE_CALENDAR_CLIENT_SECRETS_FILE`
- `GOOGLE_CALENDAR_REDIRECT_URI`
- `GOOGLE_CALENDAR_SCOPES`

No se deben versionar:

- `.env`
- Credenciales OAuth descargadas desde Google Cloud.
- Tokens OAuth generados por usuarios.
- Archivos `client_secret*.json`, `credentials*.json` o `token*.json`.

## Interfaz web inicial

Para usar las vistas internas hay que iniciar sesión:

```text
http://127.0.0.1:8000/cuentas/login/
```

Roles actuales:

- `Recepcionista`: puede gestionar pacientes y turnos.
- `Odontologo`: puede ver sus propios turnos y agenda.
- `Administrador`: puede configurar odontólogos y disponibilidad desde Django Admin.

Los roles se crean como grupos de Django al ejecutar migraciones.
Para entrar al admin, el usuario administrador tambien debe tener `is_staff` activo.

Formulario público para pacientes:

```text
http://127.0.0.1:8000/turnos/solicitar/
```

Desde esa pantalla se puede:

- Elegir odontologo y fecha.
- Ver horarios disponibles.
- Completar datos básicos del paciente.
- Guardar la solicitud como turno pendiente.
- Evitar solicitudes con fechas anteriores al dia actual.
- Ver una confirmacion con los datos del turno solicitado.

El proyecto ya incluye una primera interfaz propia para pacientes:

```text
http://127.0.0.1:8000/pacientes/
```

Desde esa sección se puede:

- Ver el listado de pacientes.
- Buscar pacientes por nombre, apellido o DNI.
- Crear un nuevo paciente.
- Ver el detalle de un paciente.
- Editar los datos de un paciente.

Tambien incluye una interfaz inicial para turnos:

```text
http://127.0.0.1:8000/turnos/
```

Desde esa seccion se puede:

- Ver el listado de turnos.
- Filtrar turnos por fecha, estado u odontologo.
- Crear un nuevo turno.
- Buscar horarios disponibles por odontologo y fecha antes de elegir la hora.
- Ver el detalle de un turno.
- Editar los datos de un turno.
- Confirmar un turno pendiente sin modificar fecha ni horario.
- Cancelar un turno sin borrarlo.

Vistas de agenda:

```text
http://127.0.0.1:8000/turnos/agenda/dia/
http://127.0.0.1:8000/turnos/agenda/semana/
```

Desde esas vistas se puede:

- Ver una tabla diaria de turnos.
- Ver la agenda diaria por bloques horarios.
- Ver una tabla semanal agrupada por dia.
- Identificar estados por color.
- Filtrar por fecha y odontologo.
- Navegar al dia o semana anterior/siguiente.

Cuando ingresa un odontologo, la agenda queda limitada automaticamente a sus propios turnos.

Conexion de Google Calendar para odontologos:

```text
http://127.0.0.1:8000/turnos/google-calendar/
```

Desde esa pantalla el odontologo puede iniciar OAuth, conectar su cuenta de Google y desconectarla si lo necesita.

Emails al paciente:

- Al solicitar un turno público, se envía un email informando que quedó pendiente.
- Al confirmar un turno pendiente, se envía un email de confirmación.
- Al cancelar un turno, se envía un email de cancelación.

En desarrollo, el backend por defecto imprime los emails en consola. Para enviar emails reales hay que configurar SMTP desde `.env`.

El envio se dispara desde la capa de servicios de turnos:

- `crear_solicitud_turno_publica`: envia solicitud recibida al email del paciente.
- `confirmar_turno`: envia turno confirmado al email del paciente.
- `cancelar_turno`: envia turno cancelado al email del paciente.

Si el paciente no tiene email cargado, no se intenta enviar. Si SMTP falla, el turno no se pierde y el error queda registrado para poder revisarlo.

Configuración local de desarrollo:

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DEFAULT_FROM_EMAIL=turnos@localhost
```

Configuración SMTP real:

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=usuario@example.com
EMAIL_HOST_PASSWORD=clave-o-token-de-aplicacion
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_TIMEOUT=10
DEFAULT_FROM_EMAIL=Consultorio <turnos@example.com>
```

Ejemplos habituales por proveedor:

| Proveedor | `EMAIL_HOST` | `EMAIL_PORT` | `EMAIL_HOST_USER` | `EMAIL_HOST_PASSWORD` | Seguridad |
| --- | --- | ---: | --- | --- | --- |
| Gmail / Google Workspace | `smtp.gmail.com` | `587` | Email completo | App password | `EMAIL_USE_TLS=True` |
| SendGrid | `smtp.sendgrid.net` | `587` | `apikey` | API key de SendGrid | `EMAIL_USE_TLS=True` |
| Brevo | `smtp-relay.brevo.com` | `587` | Login SMTP | Clave SMTP | `EMAIL_USE_TLS=True` |
| Mailgun | `smtp.mailgun.org` | `587` | Usuario SMTP del dominio | Password SMTP | `EMAIL_USE_TLS=True` |

Referencias oficiales: [Google Workspace SMTP](https://support.google.com/a/answer/176600), [Google App Passwords](https://support.google.com/accounts/answer/185833), [SendGrid SMTP](https://www.twilio.com/docs/sendgrid/for-developers/sending-email/integrating-with-the-smtp-api), [Brevo SMTP](https://help.brevo.com/hc/en-us/articles/7924908994450-Send-transactional-emails-using-Brevo-SMTP), [Mailgun SMTP](https://documentation.mailgun.com/docs/mailgun/user-manual/smtp-protocol/).

Si se usa el puerto `465`, hay que configurar `EMAIL_USE_SSL=True` y `EMAIL_USE_TLS=False`.

Para probar la configuracion activa de email:

```powershell
python manage.py probar_email tu-email@example.com
```

Para probar las tres notificaciones de turnos con las plantillas reales:

```powershell
python manage.py probar_notificaciones_email tu-email@example.com
```

Este comando fue validado con SMTP real en desarrollo. Para que funcione en otra máquina hay que cargar las credenciales SMTP propias en `.env`.

Para producción conviene usar una clave o token de aplicación del proveedor elegido y nunca subir esos valores al repositorio.

Las plantillas de email viven en:

- `turnos/templates/turnos/emails/solicitud_recibida.txt`
- `turnos/templates/turnos/emails/turno_confirmado.txt`
- `turnos/templates/turnos/emails/turno_cancelado.txt`

### Pacientes

El admin muestra columnas útiles para:

- Nombre
- Apellido
- DNI
- Teléfono
- Email

También permite buscar por nombre, apellido, DNI, teléfono o email.

### Odontólogos

El admin muestra:

- Nombre
- Apellido
- Matrícula
- Email
- Especialidad
- Horario de atención
- Estado activo/inactivo

Los odontólogos están asociados a usuarios de Django.

### Turnos

El admin muestra:

- Paciente
- Odontólogo
- Fecha
- Hora de inicio
- Hora de fin
- Estado

Además incluye filtros por estado, fecha y odontólogo.

## Reglas iniciales de negocio

La lógica actual valida que:

- Un turno tenga duración mayor a cero.
- El odontólogo esté activo para cargar turnos no cancelados.
- El turno entre dentro de una disponibilidad activa del odontólogo.
- Los días sin disponibilidad activa queden bloqueados como no laborables.
- No existan turnos activos superpuestos para el mismo odontólogo.
- Los turnos cancelados no bloqueen ese horario.
- Los horarios disponibles se calculen a partir de disponibilidad y turnos activos.
- Los odontologos solo puedan ver turnos asociados a su perfil.
- Las solicitudes públicas de turno se guarden como pendientes.
- Las solicitudes públicas no permitan fechas pasadas.
- Los turnos pendientes puedan confirmarse desde el detalle manteniendo fecha y horario.

Estados disponibles para un turno:

- Pendiente
- Confirmado
- Cancelado
- Realizado

## Comandos útiles

Ejecutar comprobaciones de Django:

```powershell
python manage.py check
```

Aplicar migraciones:

```powershell
python manage.py migrate
```

Crear nuevas migraciones:

```powershell
python manage.py makemigrations
```

Ejecutar tests:

```powershell
python manage.py test
```

Levantar el servidor de desarrollo:

```powershell
python manage.py runserver
```

## Archivos no versionados

El archivo `db.sqlite3` se usa solamente para desarrollo local y está ignorado por Git.

También se ignoran archivos generados como:

- `.venv/`
- `__pycache__/`
- `*.pyc`
- Logs
- Archivos locales de entorno

## Próximas etapas

Próximos pasos sugeridos:

1. Agregar recordatorios antes del turno.
2. Definir si los recordatorios se enviarán por email con un comando manual o con una tarea programada.
3. Preparar variables de entorno para producción.
4. Cambiar SQLite por PostgreSQL antes del despliegue.
5. Evaluar cifrado de tokens OAuth antes de producción.

## Integración con Google Calendar

El modelo de turnos ya incluye un campo para guardar el identificador del evento de Google Calendar.

Además, existe el modelo `GoogleCalendarConexion`, asociado uno a uno con `Odontologo`, para guardar:

- `calendar_id`
- `access_token`
- `refresh_token`
- `scopes`
- vencimiento del access token
- estado de conexión y último error de sincronización

La configuración base de Google Calendar ya está preparada desde variables de entorno y existe el módulo aislado:

```text
app/turnos/integrations/google_calendar.py
```

La sincronización de turnos vive en:

```text
app/turnos/google_calendar_sync.py
```

El guardado de tokens OAuth vive en:

```text
app/turnos/google_calendar_oauth.py
```

La pantalla interna para conectar Google Calendar es:

```text
http://127.0.0.1:8000/turnos/google-calendar/
```

El redirect URI que debe configurarse en Google Cloud para desarrollo local es:

```text
http://127.0.0.1:8000/turnos/google-calendar/callback/
```

Cuando hay una conexión OAuth activa para el odontólogo, la aplicación intenta:

- Crear un evento en Google Calendar.
- Actualizar el evento si cambia el horario.
- Cancelar o eliminar el evento si el turno se cancela.

Si Google Calendar falla temporalmente, el turno se mantiene guardado y el error queda registrado en la conexión del odontólogo.

La integración fue probada contra Google Calendar real: creación, actualización y cancelación de un evento de prueba.

## Licencia

Este proyecto está publicado bajo la licencia incluida en el archivo `LICENSE`.
