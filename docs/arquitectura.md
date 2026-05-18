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
- Solicitud pública.
- Consulta/cancelación/reprogramación pública por DNI.
- Agenda diaria y semanal.
- Emails transaccionales.
- Recordatorios.
- Google Calendar OAuth y sincronización.

Modelos:

- `Odontologo`
- `DisponibilidadOdontologo`
- `Turno`
- `GoogleCalendarConexion`

Capas internas:

- `models.py`: estructura y validaciones esenciales.
- `forms.py`: formularios internos y públicos.
- `views.py`: vistas HTTP.
- `services.py`: casos de uso que modifican datos.
- `selectors.py`: consultas reutilizables y cálculo de disponibilidad.
- `notifications.py`: notificaciones de email.
- `integrations/google_calendar.py`: cliente HTTP de Google Calendar.
- `google_calendar_oauth.py`: guardado/desconexión OAuth.
- `google_calendar_sync.py`: coordinación entre turnos y Google Calendar.

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
/turnos/cancelar/                      consulta/cancelación por DNI
/turnos/api/por-dni/                   endpoint JSON por DNI
/turnos/<id>/cancelar-publico/         cancelación pública con validación de DNI
/turnos/<id>/reprogramar-publico/      reprogramación pública si el turno está pendiente
```

URLs internas:

```text
/cuentas/login/
/inicio/
/pacientes/
/turnos/
/turnos/agenda/dia/
/turnos/agenda/semana/
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
- Turnos pendientes y confirmados bloquean disponibilidad.
- Turnos cancelados no bloquean disponibilidad.
- No se puede crear turno en odontólogo inactivo.
- El turno debe estar dentro de una disponibilidad activa.
- El turno debe terminar el mismo día.
- No puede superponerse con otro turno activo del mismo odontólogo.
- La confirmación de un pendiente permite elegir duración real.
- Si la duración real genera conflicto, el turno no cambia de estado.

## Permisos y Alcance de Datos

El scope de pacientes y turnos se centraliza en `usuarios/roles.py`.

Recepción y administración:

- Pueden ver y gestionar el consultorio según permisos.

Odontólogo:

- Ve turnos propios y turnos de pacientes asociados.
- Ve pacientes asociados.
- Puede cargar historia clínica si está asociado al paciente.
- Puede editar entradas clínicas propias.
- Puede conectar su propia cuenta de Google Calendar.

Interfaz pública:

- No requiere login.
- No expone historia clínica.
- Opera por DNI y valida DNI contra el turno antes de cancelar/reprogramar.

## Integraciones

### Google Calendar

Cada odontólogo tiene una conexión independiente en `GoogleCalendarConexion`.

El sistema guarda:

- `access_token`
- `refresh_token`
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

## Criterios de Código Limpio

- No duplicar reglas de negocio entre vistas.
- Mantener cambios de datos en servicios.
- Mantener consultas complejas en selectores.
- Mantener integraciones externas fuera de modelos y vistas.
- Usar nombres de dominio claros.
- Mantener tests para reglas críticas.
- Actualizar documentación cuando cambie un flujo real.
