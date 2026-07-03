# Flujo de Turnos

Este documento resume cómo se crean, confirman, reprograman y cancelan turnos en el estado actual del proyecto.

## Estados

El modelo `Turno` permite tres estados:

- `pendiente`
- `confirmado`
- `cancelado`

No hay un cuarto estado para marcar una atención finalizada.

## Reglas de Disponibilidad

Un turno bloquea disponibilidad si está:

- pendiente;
- confirmado.

Un turno no bloquea disponibilidad si está:

- cancelado.

La disponibilidad se calcula con:

- odontólogo activo;
- disponibilidades activas del odontólogo;
- fecha y día de semana;
- duración;
- turnos ocupados del mismo odontólogo.

Funciones relevantes:

- `turnos.selectors.obtener_horarios_disponibles`
- `turnos.selectors.obtener_turno_superpuesto`

## Solicitud Pública

Ruta:

```text
/turnos/solicitar/
```

Flujo:

1. El paciente elige odontólogo y fecha.
2. La pantalla consulta horarios disponibles.
3. El paciente elige un horario.
4. Completa datos mínimos:
   - nombre;
   - apellido;
   - teléfono;
   - DNI obligatorio;
   - email opcional;
   - motivo breve opcional.
5. El DNI se normaliza con una función centralizada para evitar duplicados por formato.
6. Si el DNI no existe, se crea un paciente nuevo con `origen_alta=solicitud_publica` y `estado_validacion_datos=pendiente`.
7. Si el DNI ya existe, el paciente se reutiliza sin modificar nombre, apellido, teléfono ni email.
8. Se crea un turno pendiente con duración inicial de 30 minutos.
9. Se crea una `SolicitudTurnoPublica` con la fotografía inmutable de los datos enviados.
10. Si hay diferencias contra el paciente existente, la solicitud queda pendiente de revisión para recepción.
11. La respuesta pública es neutral y no revela si el paciente existía, si se creó o si hubo diferencias.

Servicio principal:

```python
crear_solicitud_turno_publica(datos)
```

Internamente delega en el caso de uso transaccional `crear_solicitud_publica_de_turno(datos)`, que crea el paciente nuevo cuando corresponde, bloquea pacientes existentes con `select_for_update()`, maneja carreras por DNI duplicado y agenda notificaciones con `transaction.on_commit()`.

Reglas:

- No permite fechas pasadas.
- No permite horarios ocupados.
- No pide datos clínicos ni administrativos extensos.
- Crea asociación paciente-odontólogo.
- No usa el email enviado para notificar a un paciente ya registrado.
- Para pacientes existentes, el aviso se envía al email almacenado previamente, si existe.
- Para pacientes nuevos, se puede enviar confirmación al email enviado, pero ese contacto no queda verificado automáticamente.
- Las diferencias se guardan en `diferencias_detectadas` y solo se muestran a usuarios internos autorizados.

## Revisión Interna de Solicitudes Públicas

Ruta:

```text
/turnos/solicitudes-publicas/
```

Recepción puede revisar solicitudes con estado `pendiente` desde una bandeja interna. La pantalla muestra datos actuales del paciente y datos enviados desde la web lado a lado, resaltando solo los campos diferentes.

Acciones permitidas:

- conservar los datos actuales;
- aplicar únicamente campos seleccionados;
- validar administrativamente un paciente nuevo;
- descartar los datos enviados.

Cada revisión requiere `POST`, CSRF y permisos internos de gestión del consultorio. El servicio bloquea la solicitud y el paciente con `select_for_update()`, registra usuario, fecha, observaciones, campos aceptados y campos descartados, e impide procesar dos veces la misma solicitud.

## Autogestion Publica de Turnos

Rutas:

```text
/turnos/mis-turnos/solicitar-acceso/
/turnos/mis-turnos/verificar/
/turnos/mis-turnos/
/turnos/mis-turnos/<uuid>/cancelar/
/turnos/mis-turnos/<uuid>/reprogramar/
```

El paciente ingresa su DNI para iniciar un desafio de acceso. La respuesta siempre es generica: si el DNI corresponde a un paciente registrado con email, se envia un codigo OTP de 6 digitos; si no corresponde, se crea un desafio ficticio y no se revela si el paciente existe.

Luego de validar el codigo, se crea una sesion publica temporal. Desde esa sesion se muestran solo turnos activos del paciente:

- pendientes;
- confirmados.

No se muestran turnos cancelados, datos clinicos, notas internas ni motivo del turno.

Acciones:

- Cancelar turno pendiente o confirmado.
- Reprogramar solo turno pendiente.

Cada accion se protege con un registro persistente `AccionPublicaTurno` y un token de un solo uso guardado hasheado en base de datos. El permiso queda atado al paciente, turno, tipo de accion, version publica del turno y vencimiento. Si el turno cambia, se cancela o se reprograma, la version publica rota y los permisos anteriores dejan de ser validos.

La cancelacion publica:

- requiere sesion OTP verificada;
- requiere permiso de cancelacion activo y token correcto;
- permite motivo opcional;
- cambia estado a `cancelado`;
- no borra el turno;
- dispara sincronizacion con Google Calendar si corresponde;
- envia email de cancelacion si corresponde.

La reprogramacion publica:

- requiere sesion OTP verificada;
- requiere permiso de reprogramacion activo y token correcto;
- solo se permite para turnos pendientes;
- valida fecha futura y disponibilidad del nuevo horario;
- conserva el turno en estado `pendiente`.

El flujo aplica rate limiting por IP y DNI hasheados. En produccion debe usar Redis mediante `REDIS_URL`; Turnstile puede activarse como desafio adicional despues de varios intentos.
## Creación Interna

Ruta:

```text
/turnos/nuevo/
```

Regla:

- Todo turno creado desde el panel interno se guarda como `confirmado`.

Servicio:

```python
crear_turno_desde_formulario(form, usuario=None)
```

También crea o asegura la asociación paciente-odontólogo.

## Confirmación Interna de Pendientes

Ruta:

```text
/turnos/<id>/confirmar/
```

El odontólogo elige duración real:

- 30 minutos;
- 45 minutos;
- 60 minutos;
- 90 minutos;
- 120 minutos;
- duración personalizada válida.

Servicio:

```python
confirmar_turno_con_duracion(turno, duracion_minutos)
```

Si no hay conflicto:

- cambia estado a `confirmado`;
- actualiza duración;
- sincroniza Google Calendar;
- envía email de confirmación.

Si hay conflicto:

- mantiene el turno pendiente;
- no envía email;
- no sincroniza Google Calendar;
- muestra el turno conflictivo.

## Reprogramación Interna

Ruta:

```text
/turnos/<id>/reprogramar/
```

Disponible para turnos pendientes o confirmados según permisos.

Servicio:

```python
reprogramar_turno(turno, datos)
```

Efectos:

- actualiza fecha, hora y duración;
- valida reglas del modelo;
- sincroniza Google Calendar;
- envía email de reprogramación.

## Cancelación Interna

Ruta:

```text
/turnos/<id>/cancelar/
```

Servicio:

```python
cancelar_turno(turno)
```

Efectos:

- cambia estado a `cancelado`;
- sincroniza cancelación con Google Calendar;
- envía email de cancelación.

## Google Calendar

La sincronización ocurre desde servicios de turnos, no desde templates:

- `sincronizar_turno_creado`
- `sincronizar_turno_actualizado`
- `sincronizar_turno_cancelado`

Si el odontólogo no tiene conexión activa o Google falla, el turno se conserva y el error queda registrado.

## Emails

Notificaciones:

- `notificar_solicitud_turno_recibida`
- `notificar_turno_confirmado`
- `notificar_turno_cancelado`
- `notificar_turno_reprogramado`
- `notificar_recordatorio_turno`

Si el paciente no tiene email, no se intenta enviar.

## Agenda

Rutas:

```text
/turnos/agenda/dia/
/turnos/agenda/semana/
```

Los odontólogos ven su agenda limitada por las reglas de scope. Recepción y administración pueden ver el consultorio según permisos.
