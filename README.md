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
- Listado y creación de pacientes desde vistas propias.
- Validación para evitar turnos superpuestos.
- Validación de horarios de atención del odontólogo.
- Campo preparado para guardar el ID del evento de Google Calendar.
- Tests iniciales para la lógica de turnos.

Documentación técnica:

- [Arquitectura del proyecto](docs/arquitectura.md)

## Tecnologías

- Python 3.13
- Django 6.0.4
- SQLite para desarrollo local
- Django Admin como primera interfaz de gestión

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
    └── turnos/
        ├── admin.py
        ├── apps.py
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
- Turnos

## Interfaz web inicial

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
- El turno empiece dentro del horario de atención del odontólogo.
- El turno termine dentro del horario de atención del odontólogo.
- No existan turnos activos superpuestos para el mismo odontólogo.
- Los turnos cancelados no bloqueen ese horario.

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

1. Crear vistas propias para listar, crear y editar turnos fuera del admin.
2. Agregar formularios para pacientes y turnos.
3. Crear una vista de agenda diaria o semanal.
4. Definir disponibilidad por odontólogo.
5. Agregar autenticación para usuarios internos.
6. Integrar Google Calendar para crear, actualizar y cancelar eventos.
7. Preparar variables de entorno para producción.
8. Cambiar SQLite por PostgreSQL antes del despliegue.

## Integración futura con Google Calendar

El modelo de turnos ya incluye un campo para guardar el identificador del evento de Google Calendar.

Más adelante, al crear o modificar un turno, la aplicación podrá:

- Crear un evento en Google Calendar.
- Actualizar el evento si cambia el horario.
- Cancelar o eliminar el evento si el turno se cancela.

## Licencia

Este proyecto está publicado bajo la licencia incluida en el archivo `LICENSE`.
