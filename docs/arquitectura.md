# Arquitectura del Proyecto

Este documento describe la arquitectura actual de `gestor-turnos-odontologia` según el código del repositorio.

El proyecto sigue una separación simple por apps Django, con reglas de negocio concentradas en modelos, servicios, selectores y permisos. La intención es mantener el sistema entendible, testeable y fácil de extender.

## Objetivo

Administrar turnos de un consultorio odontológico con dos superficies bien separadas:

- Interfaz pública para pacientes.
- Panel interno para odontólogos, recepción y administración.

Además, el sistema incluye historia clínica, odontograma, adjuntos clínicos, Google Calendar y notificaciones por email.

## Apps Principales

### `config`

Responsabilidades:

- Settings de Django.
- Carga de variables de entorno desde `.env`.
- Configuración de base de datos por `DATABASE_URL`.
- Configuración de email, storage, seguridad HTTPS y logs.
- URLs globales.
- Backend propio de email por API HTTP.
- Backend de storage para Supabase Storage.

Archivos relevantes:

- `config/settings.py`
- `config/env.py`
- `config/database.py`
- `config/email_backends.py`
- `config/storage_backends.py`
- `config/urls.py`

### `usuarios`

Responsabilidades:

- Login interno.
- Dashboard interno.
- Perfil del usuario/odontólogo.
- Roles por grupos de Django.
- Mixins de permisos para vistas internas.
- Scope de datos según usuario.

Roles actuales:

- `Recepcionista`
- `Odontologo`
- `Administrador`

Las reglas de acceso principales viven en `usuarios/roles.py`.

### `pacientes`

Responsabilidades:

- Datos personales y administrativos del paciente.
- Ficha odontológica.
- Asociación paciente-odontólogo.
- Derivación/asignación a otro odontólogo.
- Borrado seguro con confirmaciones.
- Perfil clínico del paciente.

Modelos:

- `Paciente`
- `FichaOdontologica`
- `PacienteOdontologo`

Notas:

- Un paciente puede estar asociado a varios odontólogos.
- La ficha odontológica concentra datos personales, cobertura, contacto y alertas clínicas.
- El perfil del paciente muestra resumen clínico, turnos, historia reciente y asociaciones.

### `turnos`

Responsabilidades:

- Odontólogos.
- Disponibilidad por día de semana.
- Turnos.
- Excepciones operativas de agenda.
- Solicitud pública.
- Autogestión pública con OTP por email, sesión temporal y permisos de acción de un solo uso.
- Agenda diaria y semanal.
- Emails transaccionales.
- Recordatorios.
- Google Calendar OAuth y sincronización.

Modelos:

- `Odontologo`
- `DisponibilidadOdontologo`
- `ExcepcionAgenda`
- `Turno`
- `SolicitudTurnoPublica`
- `DesafioAccesoPublicoTurnos`
- `AccionPublicaTurno`
- `GoogleCalendarConexion`

`SolicitudTurnoPublica` separa lo enviado desde la web pública del registro principal del paciente. Guarda documento, nombre, apellido, teléfono, email y motivo enviados como fotografía de auditoría, junto con diferencias detectadas, estado de revisión, usuario revisor y campos aceptados/descartados. Esa fotografía no debe usarse como fuente confiable para datos clínicos ni reemplaza automáticamente a `Paciente`.

Capas internas:

- `models.py`: estructura y validaciones esenciales.
- `forms/`: paquete de formularios por dominio. `__init__.py` reexporta los nombres públicos históricos para conservar `from turnos.forms import TurnoForm`.
  - `fields.py`: campos y conversiones reutilizables.
  - `turnos.py`: formularios internos de turno, confirmación, filtros y búsqueda de horarios.
  - `solicitudes_publicas.py`: solicitud pública inicial y revisión interna de datos enviados.
  - `public_access.py`: formularios OTP, cancelación y reprogramación pública segura.
  - `agenda.py`: filtros de agenda.
  - `excepciones.py`: formulario de bloqueos/excepciones de agenda.
- `views/`: paquete de vistas por dominio. `__init__.py` reexporta las clases usadas por URLs y tests para mantener compatibilidad.
  - `turnos.py`: listado, detalle, alta, edición, confirmación, cancelación, reprogramación y horarios internos.
  - `public_booking.py`: landing/selección/formulario/confirmación del flujo público de solicitud.
  - `solicitudes_publicas.py`: bandeja, alertas administrativas y revisión de solicitudes.
  - `agenda.py`: agenda diaria y semanal.
  - `excepciones.py`: ABM operativo de excepciones.
  - `google_calendar.py`: OAuth y conexión de Google Calendar.
  - `helpers.py`: helpers de presentación compartidos.
- `services.py`: casos de uso que modifican datos.
- `selectors.py`: consultas reutilizables y cálculo de disponibilidad.
- `excepciones.py`: ventana pública de reserva, bloqueos operativos, detección de turnos afectados y locks técnicos por agenda.
- `excepcion_permissions.py`: permisos de excepciones por rol y por odontólogo.
- `notifications.py`: notificaciones de email.
- `integrations/google_calendar.py`: cliente HTTP de Google Calendar.
- `google_calendar_oauth.py`: guardado/desconexión OAuth.
- `google_calendar_sync.py`: coordinación entre turnos y Google Calendar.
- `solicitudes_publicas/`: caso de uso transaccional de solicitud pública, comparación de datos, selectores y permisos de revisión.
- `public_access/`: OTP público, sesión temporal, rate limiting y acciones públicas de un solo uso.

`turnos/models.py` no se divide todavía para evitar riesgos de imports circulares y cambios accidentales en migraciones. El plan técnico está documentado en `docs/refactor_modelos_turnos.md`.

### `consultorio`

Responsabilidades:

- Perfil singleton del consultorio para una instalación y una base de datos.
- Nombre comercial, nombre corto, logo, contacto, textos públicos, política de cancelación y color principal.
- Ventana de reserva pública, reserva el mismo día y anticipación mínima.
- Context processor global de solo lectura para templates.
- Pantalla interna `/configuracion/consultorio/` para usuarios con permiso de gestión del consultorio.
- Datos explícitos para emails transaccionales, sin cambiar `DEFAULT_FROM_EMAIL`.

Reglas:

- No es multi-tenant y no agrega FK de consultorio a pacientes, turnos, historias ni odontólogos.
- Usa `pk=1` como identificador estable.
- El context processor no crea registros en cada request; si falta la fila, usa defaults seguros en memoria.
- La vista interna puede crear la fila con `get_or_create`.
- El logo usa el storage default de Django, por lo que funciona con filesystem local o Supabase Storage.
- La limpieza del logo anterior se ejecuta después del commit; si falla el borrado remoto, se registra un warning seguro y no se revierte la configuración ya guardada.
- No permite SVG, valida tamaño máximo y evita usar valores arbitrarios como CSS.

### `historias`

Responsabilidades:

- Historia clínica por paciente.
- Adjuntos clínicos.
- Búsqueda y filtros de historias.
- Auditoría básica por logs.
- Integración del odontograma dentro de nuevas entradas clínicas.

Modelos:

- `HistoriaClinica`
- `HistoriaClinicaAdjunto`

Reglas actuales:

- Solo usuarios con perfil de odontólogo pueden acceder a historia clínica.
- Un odontólogo puede ver según las reglas de permisos clínicos.
- La creación exige asociación del odontólogo con el paciente.
- La edición queda limitada al odontólogo responsable de la entrada.

### `odontogramas`

Responsabilidades:

- Odontograma FDI por paciente.
- Estados dentales por diente y cara.
- Historial de cambios por inactivación del estado anterior.
- Editor interactivo con SVG/HTML y JavaScript.
- Asociación opcional de estados dentales a una entrada de historia clínica.

Modelos:

- `Odontograma`
- `EstadoDental`

El odontograma mantiene un estado activo por diente/cara y conserva registros anteriores como historial.

## Flujo de URLs

URLs públicas:

```text
/                                      landing pública
/turnos/solicitar/                     selección pública de turno
/turnos/solicitar/horarios/            endpoint JSON de horarios públicos
/turnos/solicitar/datos/               formulario público de datos mínimos
/turnos/solicitar/gracias/             confirmación pública
/turnos/mis-turnos/solicitar-acceso/  solicitud de acceso publico por OTP
/turnos/mis-turnos/verificar/         verificacion de codigo OTP
/turnos/mis-turnos/                   listado publico de turnos activos con sesion verificada
/turnos/mis-turnos/cerrar/            cierre de sesion publica
/turnos/mis-turnos/<uuid>/cancelar/   cancelacion publica por permiso persistente
/turnos/mis-turnos/<uuid>/reprogramar/ reprogramacion publica por permiso persistente
```

URLs internas:

```text
/cuentas/login/
/inicio/
/pacientes/
/turnos/
/turnos/agenda/dia/
/turnos/agenda/semana/
/turnos/excepciones/
/turnos/excepciones/nueva/
/turnos/excepciones/<id>/editar/
/turnos/alertas-administrativas/
/turnos/solicitudes-publicas/       # compatibilidad: redirige a Turnos o Alertas
/turnos/solicitudes-publicas/<uuid>/ # compatibilidad: redirige al turno si existe
/turnos/google-calendar/
/historias/pacientes/<paciente_id>/
/odontogramas/pacientes/<paciente_id>/
/admin/
```

La URL independiente de odontograma se conserva para compatibilidad interna, pero el flujo clínico principal lo integra en la creación de una entrada de historia clínica.

## Reglas de Turnos

Estados válidos:

- `pendiente`
- `confirmado`
- `cancelado`

Reglas:

- Turnos internos: se crean como `confirmado`.
- Turnos públicos: se crean como `pendiente` y `duracion_minutos=30`.
- Solicitudes públicas normales: crean un `Turno` y una `SolicitudTurnoPublica`; el turno aparece directamente en Turnos y Agenda.
- `SolicitudTurnoPublica` se conserva como auditoría de los datos enviados, diferencias, revisor, fecha de revisión y campos aceptados/descartados.
- Si el DNI ya existe, los datos enviados no modifican automáticamente el `Paciente`; sólo se aplican campos seleccionados desde la revisión interna.
- Si el DNI corresponde a un paciente archivado, no se crea turno y la solicitud queda como alerta administrativa sin reactivar ni borrar pacientes.
- Turnos pendientes y confirmados bloquean disponibilidad.
- Turnos cancelados no bloquean disponibilidad.
- No se puede crear turno en odontólogo inactivo.
- El turno debe estar dentro de una disponibilidad activa.
- El turno debe terminar el mismo día.
- No puede superponerse con otro turno activo del mismo odontólogo.
- No puede caer dentro de una `ExcepcionAgenda` activa que afecte al odontólogo o a todo el consultorio.
- Los turnos públicos solo pueden reservarse o reprogramarse dentro de la ventana pública configurada en `ConfiguracionConsultorio`.
- La confirmación de un pendiente permite elegir duración real. Si el turno proviene de una solicitud pública pendiente, la revisión de datos y la confirmación se ejecutan juntas en una transacción.
- Si la duración real genera conflicto, el turno no cambia de estado.
- Crear o actualizar una excepción que afecta turnos existentes requiere confirmación explícita en dos pasos; esos turnos no se cancelan automáticamente.
- Las excepciones de agenda no se sincronizan con Google Calendar.

## Permisos y Alcance de Datos

El scope de pacientes y turnos se centraliza en `usuarios/roles.py`.

La autorizacion por objeto se aplica al construir el queryset, antes de resolver `pk`,
`paciente_pk` o cualquier identificador de URL. El patron esperado es limitar primero y
recien despues usar `get_object_or_404()`. Un ID interno no concede acceso por si solo.

Recepción y administración:

- Pueden ver y gestionar el consultorio segun permisos operativos.
- Mantienen alcance administrativo sobre pacientes y turnos.
- Mantienen alcance administrativo sobre excepciones globales y por odontólogo.
- Si no tienen perfil `Odontologo`, no acceden a historias clinicas, adjuntos clinicos ni odontogramas.

Odontólogo:

- Ve turnos propios y turnos de pacientes asociados.
- Ve solo pacientes con asociacion activa en `PacienteOdontologo`.
- Una asociacion con `activo=False` no concede alcance.
- Puede cargar historia clinica solo si esta asociado a un paciente activo.
- Puede editar entradas clinicas propias.
- Puede gestionar excepciones solo de su propia agenda; no puede crear bloqueos globales ni modificar excepciones de otro odontólogo.
- Puede conectar su propia cuenta de Google Calendar.

Lectura clinica compartida y emergencia:

- `DATOS_CLINICOS_COMPARTIDOS_ENTRE_ODONTOLOGOS=False` es el comportamiento recomendado: sin asociacion activa no hay lectura clinica.
- Si se activa lectura compartida, es solo lectura, requiere perfil `Odontologo` y queda auditada.
- Un usuario administrativo sin perfil odontologico no recibe acceso clinico por ser administrador.
- El superusuario debe iniciar un acceso de emergencia por paciente para leer datos clinicos fuera de las reglas normales. Ese acceso exige motivo, vence y queda auditado.

Historias, adjuntos y odontogramas:

- Heredan el alcance del paciente.
- El detalle y la edicion de historias usan `limitar_historias_clinicas_por_usuario()`.
- La descarga de adjuntos se resuelve desde historias visibles y no abre el archivo antes de autorizar.
- El odontograma permanece detras de `ODONTOGRAMA_FEATURE_ENABLED`; cuando se active, resuelve el paciente visible antes de crear `Odontograma` o `EstadoDental`.
- Acceder a una URL no crea asociaciones, fichas, odontogramas ni estados dentales.

Respuestas HTTP internas:

- Usuario anonimo: redireccion a login.
- Usuario autenticado sin permiso general del modulo: `403`.
- Usuario con permiso general pero objeto fuera de alcance: `404`.
- Objeto visible pero accion no permitida, por ejemplo editar una historia ajena visible: `403`.

Archivado de pacientes:

- El modelo `Paciente` bloquea el borrado fisico y usa archivado reversible.
- El archivado conserva historia clinica, adjuntos, fichas, turnos y asociaciones para auditoria.
- Las consultas operativas usan pacientes activos por defecto.
- No se crean nuevos turnos, historias, fichas, odontogramas ni asociaciones activas para pacientes archivados.

Interfaz pública:

- No requiere login.
- No expone historia clínica.
- Opera con desafio OTP por email, respuesta generica para evitar enumeracion y permisos de accion atados a paciente, turno, tipo de accion, version del turno y vencimiento.
- La solicitud pública de turno usa respuesta neutral: no revela si el DNI existe, si hubo diferencias ni a qué contacto se notificó.
- La revisión de diferencias queda restringida a usuarios con permiso de gestión del consultorio, principalmente recepción.

## Integraciones

### Google Calendar

Cada odontólogo tiene una conexión independiente en `GoogleCalendarConexion`.

El sistema guarda:

- `access_token` cifrado en reposo
- `refresh_token` cifrado en reposo
- `token_expira_en`
- `calendar_id`
- `scopes`
- `ultimo_error`

El turno guarda `google_calendar_event_id`.

Si Google Calendar falla, la operación del dominio se mantiene y el error queda registrado.

### Email

El envío se concentra en `turnos/notifications.py`.

Backends soportados:

- Consola de Django para desarrollo.
- SMTP estándar.
- `config.email_backends.EmailApiBackend` para Resend o Brevo.

### Storage

Los adjuntos clínicos usan `FileField`.

Backends soportados:

- `django.core.files.storage.FileSystemStorage` para desarrollo local.
- `config.storage_backends.SupabaseStorage` para Supabase Storage privado.

## Deploy

Railway ejecuta:

- `scripts/build.sh`: instala dependencias y corre `collectstatic`.
- `scripts/release.sh`: aplica migraciones.
- `scripts/start.sh`: levanta Gunicorn.

Supabase mantiene:

- PostgreSQL.
- Storage privado para adjuntos clínicos.

## Frontend base

`base.html` mantiene solo la estructura HTML general, los bloques Django y las variables CSS dinámicas del color del consultorio. El CSS estático vive en `app/static/css/` con `app.css` como punto de entrada y se divide en tokens, base, formularios, vistas internas, vistas públicas y responsive.

La navegación interna, la navegación pública, mensajes y banner de emergencia clínica viven en includes reutilizables bajo `app/templates/includes/`.

## Calidad automatizada

La configuración de Black, Ruff, Coverage, Mypy y Bandit vive en `pyproject.toml`. `requirements-dev.txt` contiene herramientas de desarrollo y no se usa en runtime de Railway.

## Criterios de Código Limpio

- No duplicar reglas de negocio entre vistas.
- Mantener cambios de datos en servicios.
- Mantener consultas complejas en selectores.
- Mantener integraciones externas fuera de modelos y vistas.
- Usar nombres de dominio claros.
- Mantener tests para reglas críticas.
- Actualizar documentación cuando cambie un flujo real.
