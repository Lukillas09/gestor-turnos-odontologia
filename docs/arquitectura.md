# Arquitectura del Proyecto

Este documento define las decisiones iniciales de arquitectura para el gestor de turnos odontologico.

La idea es que el proyecto crezca de forma ordenada, con codigo limpio, responsabilidades claras y cambios faciles de mantener.

## Objetivo del sistema

El sistema debe permitir administrar turnos de un consultorio odontologico.

En esta primera etapa se busca resolver:

- Carga de pacientes.
- Carga de odontologos.
- Carga y gestion de turnos.
- Solicitud publica de turnos para pacientes.
- Validacion de horarios disponibles.
- Prevencion de turnos superpuestos.
- Preparacion para integracion con Google Calendar.

## Principios de diseno

El proyecto va a seguir estos criterios:

- Modularidad: cada app debe tener una responsabilidad clara.
- Alta cohesion: cada modulo debe agrupar codigo relacionado con un mismo concepto.
- Bajo acoplamiento: una app no debe conocer detalles internos innecesarios de otra.
- Nombres claros: clases, funciones y variables deben expresar su intencion.
- Codigo simple: primero resolver bien el caso actual, sin sobrearmar estructuras prematuras.
- Reglas testeables: cada regla importante del dominio debe poder probarse con tests.
- Cambios incrementales: cada etapa debe dejar el sistema funcionando.

## Modulos actuales

### config

Responsabilidad:

- Configuracion general de Django.
- Registro de apps instaladas.
- Configuracion de autenticacion y redirecciones de login/logout.
- Registro del context processor de permisos.
- Carga de variables de entorno desde `.env`.
- Configuracion de base de datos.
- Configuracion de idioma, zona horaria y archivos estaticos.
- Rutas principales del proyecto.

Este modulo no debe contener reglas de negocio.

### pacientes

Responsabilidad:

- Representar y administrar los datos de los pacientes.
- Centralizar la informacion personal y de contacto.

Modelo principal:

- `Paciente`

Por ahora, esta app no conoce los detalles internos de los turnos. La relacion con turnos aparece desde la app `turnos`.

### turnos

Responsabilidad:

- Representar odontologos.
- Representar disponibilidad de odontologos.
- Representar turnos.
- Validar reglas basicas de agenda.
- Evitar turnos superpuestos.
- Calcular horarios disponibles.
- Guiar la creacion de turnos con horarios disponibles.
- Resolver solicitudes publicas de turnos.
- Mostrar agenda diaria y semanal simple.
- Mostrar agenda diaria por bloques horarios con estados diferenciados visualmente.
- Enviar notificaciones por email relacionadas con turnos.
- Aislar la integracion con Google Calendar.
- Sincronizar turnos con eventos externos sin acoplar vistas ni formularios.

Modelos principales:

- `Odontologo`
- `DisponibilidadOdontologo`
- `Turno`
- `GoogleCalendarConexion`

Esta app concentra las reglas iniciales del dominio de agenda.

### usuarios

Responsabilidad:

- Centralizar roles y permisos internos.
- Definir permisos de recepcionista, odontologo y administrador.
- Redirigir a cada usuario a su pantalla inicial segun el rol.
- Exponer permisos simples a las plantillas.

Este modulo no representa pacientes ni turnos. Solo decide que puede hacer cada usuario dentro del sistema.

## Reglas de negocio actuales

El modelo `Turno` valida que:

- La duracion del turno sea mayor a cero.
- El odontologo este activo para turnos no cancelados.
- El turno entre dentro de una disponibilidad activa del odontologo.
- Los dias sin disponibilidad activa queden bloqueados como no laborables.
- No exista otro turno activo superpuesto para el mismo odontologo.
- Los turnos cancelados no bloqueen horarios.

El selector `obtener_horarios_disponibles` calcula horarios libres usando:

- Disponibilidad activa del odontologo.
- Duracion configurada del turno.
- Turnos pendientes y confirmados ya existentes.
- Estado activo/inactivo del odontologo.

El formulario de creacion de turnos consume ese selector para que la hora se elija desde una lista de horarios libres, en lugar de cargarla manualmente.

El formulario publico de solicitud de turnos tambien consume ese selector, rechaza fechas pasadas y guarda los turnos nuevos como `pendiente`.

La confirmacion publica muestra los datos principales del turno recien solicitado usando el identificador guardado en la sesion del navegador.

La confirmacion interna de turnos solo cambia el estado de `pendiente` a `confirmado`; no modifica fecha, hora ni duracion.

Los selectores `obtener_turnos_del_dia` y `obtener_turnos_de_la_semana` concentran las consultas de agenda para que las vistas solo preparen contexto de presentacion.

El selector `obtener_bloques_agenda_del_dia` arma bloques horarios para la agenda diaria. La vista usa esos bloques para mostrar espacios libres y turnos cargados sin agregar librerias externas.

Estados actuales de un turno:

- `pendiente`
- `confirmado`
- `cancelado`
- `realizado`

## Roles actuales

El sistema usa grupos de Django para separar responsabilidades:

- `Recepcionista`: puede gestionar pacientes y turnos desde las vistas internas.
- `Odontologo`: puede ver turnos propios, detalle y agenda filtrada a su perfil.
- `Administrador`: puede configurar odontologos y disponibilidad desde Django Admin.

Los permisos de acceso viven en `usuarios/roles.py` y los mixins de vistas en `usuarios/mixins.py`.
Para acceder a Django Admin, el usuario tambien debe tener `is_staff` activo.

## Decisiones tomadas

### Django Admin como primera interfaz

Se usa Django Admin para validar el dominio rapidamente y poder cargar datos desde el inicio.

Esta decision permite avanzar sin invertir todavia en vistas propias, plantillas o frontend.

Mas adelante se agregaran pantallas especificas para usuarios del consultorio.

### Login interno

Las vistas internas de pacientes, turnos y agenda requieren sesion iniciada.

Por ahora se utiliza la autenticacion nativa de Django con grupos para roles internos.

El formulario publico de solicitud de turnos no requiere sesion iniciada.

### SQLite para desarrollo local

Se usa SQLite porque simplifica el arranque del proyecto.

Antes de produccion, la base deberia cambiarse a PostgreSQL.

### Validaciones iniciales en los modelos

Las primeras reglas de agenda viven en el modelo `Turno`.

Esto es aceptable en esta etapa porque:

- Las reglas son pocas.
- Estan cerca de los datos que validan.
- Se ejecutan tanto desde admin como desde codigo.

Cuando crezca la logica, se moveran los casos de uso a servicios especificos.

## Evolucion prevista

La arquitectura puede evolucionar asi:

```text
turnos/
|-- models.py
|-- admin.py
|-- forms.py
|-- views.py
|-- services.py
|-- selectors.py
|-- tests.py
`-- integrations/
    `-- google_calendar.py
```

La separacion esperada seria:

- `models.py`: estructura de datos y validaciones esenciales.
- `forms.py`: validaciones propias de formularios.
- `views.py`: manejo de requests y responses.
- `services.py`: casos de uso que modifican datos.
- `selectors.py`: consultas de lectura reutilizables.
- `integrations/`: comunicacion con servicios externos.
- `tests.py`: pruebas del comportamiento del dominio.

Esta estructura se va a crear solo cuando haga falta, no antes.

El modulo `turnos/integrations/google_calendar.py` prepara eventos de Google Calendar, lee configuracion OAuth, renueva access tokens y ejecuta llamadas HTTP a la API externa.

El modulo `turnos/google_calendar_sync.py` coordina la sincronizacion desde el dominio: decide si corresponde crear, actualizar o cancelar un evento, y registra errores sin romper el guardado del turno.

El modulo `turnos/google_calendar_oauth.py` guarda y desconecta tokens OAuth asociados al odontologo.

El modelo `GoogleCalendarConexion` guarda la relacion entre un `Odontologo` y su token OAuth. Es una relacion uno a uno porque cada odontologo debe conectar su propia agenda.

El modulo `turnos/notifications.py` concentra los emails del dominio de turnos. Los servicios lo llaman cuando una solicitud publica queda pendiente, cuando un turno se confirma y cuando un turno se cancela.

## Seguridad y secretos

La configuracion sensible vive fuera del codigo fuente.

El archivo `.env.example` documenta las variables necesarias, pero los valores reales deben quedar en `.env`, que esta ignorado por Git.

No deben versionarse:

- Secret keys de Django.
- Credenciales OAuth de Google Cloud.
- Tokens OAuth de odontologos.
- Archivos JSON de credenciales o tokens.

Los tokens OAuth se guardan en la base de datos mediante `GoogleCalendarConexion`. Para desarrollo alcanza con SQLite; antes de produccion se deberia evaluar cifrado de tokens, PostgreSQL y backups.

Las variables actuales para Google Calendar son:

- `GOOGLE_CALENDAR_CLIENT_ID`
- `GOOGLE_CALENDAR_CLIENT_SECRET`
- `GOOGLE_CALENDAR_CLIENT_SECRETS_FILE`
- `GOOGLE_CALENDAR_REDIRECT_URI`
- `GOOGLE_CALENDAR_SCOPES`
- `EMAIL_BACKEND`
- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS`
- `EMAIL_USE_SSL`
- `EMAIL_TIMEOUT`
- `DEFAULT_FROM_EMAIL`

En desarrollo se usa `django.core.mail.backends.console.EmailBackend`, que imprime los mensajes en consola y evita depender de un proveedor externo. Para produccion se debe configurar `django.core.mail.backends.smtp.EmailBackend` con credenciales de un proveedor SMTP y guardar esas credenciales solo en variables de entorno.

Los mensajes al paciente estan separados en plantillas de texto:

- `turnos/templates/turnos/emails/solicitud_recibida.txt`
- `turnos/templates/turnos/emails/turno_confirmado.txt`
- `turnos/templates/turnos/emails/turno_cancelado.txt`

Las notificaciones se ejecutan desde los casos de uso de turnos:

- `crear_solicitud_turno_publica`
- `confirmar_turno`
- `cancelar_turno`

Si el paciente no tiene email, la notificacion se omite. Si SMTP falla durante una accion real del consultorio, el turno mantiene su cambio de estado y el error queda registrado para diagnostico.

La configuracion SMTP activa se puede validar con:

```powershell
python manage.py probar_email tu-email@example.com
```

Las tres plantillas de notificaciones se pueden probar con:

```powershell
python manage.py probar_notificaciones_email tu-email@example.com
```

Los comandos usan las variables `EMAIL_*` vigentes. Si el backend sigue siendo `console.EmailBackend`, el email aparece en consola; si se cambia a `smtp.EmailBackend`, se intenta enviar por el proveedor real.

## Casos de uso futuros

Los siguientes casos de uso deberian vivir fuera del modelo cuando la logica crezca:

- Crear turno.
- Crear solicitud publica de turno.
- Confirmar turno.
- Cancelar turno.
- Reprogramar turno.
- Buscar horarios disponibles.
- Sincronizar turno con Google Calendar.
- Enviar notificaciones de turno.

Ejemplo de nombres esperados:

```python
crear_turno(...)
confirmar_turno(...)
cancelar_turno(...)
reprogramar_turno(...)
obtener_horarios_disponibles(...)
crear_solicitud_turno_publica(...)
```

## Criterios de codigo limpio

Para mantener el proyecto entendible:

- Una funcion debe hacer una sola cosa.
- Si una funcion necesita demasiadas condiciones, probablemente haya una abstraccion pendiente.
- Evitar nombres genericos como `data`, `obj`, `item` cuando haya un nombre de dominio mejor.
- Evitar duplicar reglas de negocio en varios lugares.
- No mezclar logica de negocio con detalles de interfaz.
- No mezclar logica de negocio con integraciones externas.
- Los tests deben describir comportamiento, no implementacion interna.

## Criterio para agregar nuevas apps

No se debe crear una app nueva por cada modelo.

Una app nueva se justifica cuando aparece un area del dominio con responsabilidad propia.

Ejemplos posibles a futuro:

- `historia_clinica`
- `pagos`
- `notificaciones`
- `integraciones`

Por ahora, `pacientes`, `turnos` y `usuarios` son suficientes.

## Integracion con Google Calendar

La integracion con Google Calendar no debe quedar mezclada directamente dentro del modelo `Turno`.

Las responsabilidades se separan asi:

```text
turnos/integrations/google_calendar.py
turnos/google_calendar_oauth.py
turnos/google_calendar_sync.py
```

La conexion OAuth queda asociada al modelo `GoogleCalendarConexion`, mientras que el `Turno` solo conserva el `google_calendar_event_id` del evento creado.

La pantalla `/turnos/google-calendar/` permite al odontologo iniciar el flujo OAuth. El callback `/turnos/google-calendar/callback/` valida el `state`, intercambia el `code` por tokens y guarda la conexion.

La app puede crear, editar o cancelar turnos aunque Google Calendar falle temporalmente. En ese caso, el error se registra en `GoogleCalendarConexion.ultimo_error`.

Esto ayuda a mantener bajo acoplamiento entre el dominio del sistema y un servicio externo.

## Regla de trabajo por etapa

Cada etapa del proyecto deberia cerrar con:

1. Codigo implementado.
2. Tests actualizados cuando corresponda.
3. `python manage.py check`.
4. `python manage.py test`.
5. README o documentacion actualizada si cambia la forma de usar el sistema.
6. Commit con mensaje claro en espanol.
