# Gestor de Turnos Odontológico

Aplicación web para administrar turnos de un consultorio odontológico.

El objetivo del proyecto es construir, paso a paso, un sistema que permita cargar pacientes, odontólogos y turnos desde una interfaz administrativa, evitando superposición de horarios y dejando preparada una futura integración con Google Calendar.

## Estado actual

El proyecto se encuentra en una etapa inicial funcional.

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
- Tests iniciales para la lógica de turnos.

Documentación técnica:

- [Arquitectura del proyecto](docs/arquitectura.md)

## Tecnologías

- Python 3.13
- Django 6.0.4
- SQLite para desarrollo local
- Django Admin como primera interfaz de gestión
- Variables de entorno para configuración sensible

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
        ├── google_calendar_sync.py
        ├── integrations/
        │   └── google_calendar.py
        ├── models.py
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

1. Implementar el flujo OAuth para conectar la cuenta de Google del odontólogo desde la web.
2. Probar la sincronización contra una cuenta real de Google Calendar.
3. Agregar notificaciones por email.
4. Preparar variables de entorno para producción.
5. Cambiar SQLite por PostgreSQL antes del despliegue.

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

Cuando hay una conexión OAuth activa para el odontólogo, la aplicación intenta:

- Crear un evento en Google Calendar.
- Actualizar el evento si cambia el horario.
- Cancelar o eliminar el evento si el turno se cancela.

Si Google Calendar falla temporalmente, el turno se mantiene guardado y el error queda registrado en la conexión del odontólogo.

## Licencia

Este proyecto está publicado bajo la licencia incluida en el archivo `LICENSE`.
