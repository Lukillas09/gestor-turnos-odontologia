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
   - DNI opcional;
   - email opcional;
   - motivo breve opcional.
5. Se crea o actualiza el paciente.
6. Se crea un turno pendiente con duración inicial de 30 minutos.
7. Se envía email de solicitud recibida si el paciente cargó email.
8. Se muestra pantalla de confirmación.

Servicio principal:

```python
crear_solicitud_turno_publica(datos)
```

Reglas:

- No permite fechas pasadas.
- No permite horarios ocupados.
- No pide datos clínicos ni administrativos extensos.
- Crea asociación paciente-odontólogo.

## Consulta y Cancelación Pública

Ruta:

```text
/turnos/cancelar/
```

El paciente ingresa su DNI y ve solo turnos:

- pendientes;
- confirmados.

No se muestran turnos cancelados.

Acciones:

- Cancelar turno pendiente o confirmado.
- Reprogramar solo turno pendiente.

La cancelación pública:

- valida que el DNI coincida con el paciente del turno;
- permite motivo opcional;
- cambia estado a `cancelado`;
- no borra el turno;
- dispara sincronización con Google Calendar si corresponde;
- envía email de cancelación si corresponde.

Campo usado para motivo:

```text
Turno.motivo_cancelacion_paciente
```

## Reprogramación Pública

Ruta:

```text
/turnos/<id>/reprogramar-publico/
```

Solo se permite si:

- el DNI coincide;
- el turno está pendiente;
- el nuevo horario está disponible;
- la fecha no es pasada;
- no hay superposición.

Los turnos confirmados no pueden reprogramarse desde la interfaz pública.

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
