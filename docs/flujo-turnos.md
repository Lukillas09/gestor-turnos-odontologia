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
- turnos ocupados del mismo odontólogo;
- excepciones de agenda activas del odontólogo o globales del consultorio.

Funciones relevantes:

- `turnos.selectors.obtener_horarios_disponibles`
- `turnos.selectors.obtener_turno_superpuesto`
- `turnos.excepciones.obtener_excepcion_que_bloquea_intervalo`

## Solicitud Pública

Ruta:

```text
/turnos/solicitar/
```

Flujo:

1. El paciente elige odontólogo. Con la agenda inteligente activa también elige un motivo configurado antes de la fecha.
2. La pantalla consulta horarios disponibles; el modo inteligente separa recomendados y alternativas.
3. El paciente elige cualquier horario válido.
4. Completa datos mínimos:
   - nombre;
   - apellido;
   - teléfono;
   - DNI obligatorio;
   - email condicional: obligatorio para pacientes nuevos y para pacientes existentes activos sin email registrado;
   - motivo breve opcional en legacy o comentario adicional opcional en el modo inteligente.
5. El POST final se cuenta una sola vez para rate limit por IP y DNI hasheados.
6. Si corresponde por umbral, se exige Turnstile antes de procesar la solicitud.
7. Se valida el token de idempotencia del formulario para evitar doble clic, recarga o reenvío del POST.
8. El DNI se normaliza con una función centralizada para evitar duplicados por formato.
9. Si el DNI no existe, el email es obligatorio y se crea un paciente nuevo con `origen_alta=solicitud_publica`, `estado_validacion_datos=pendiente` y `email_verificado_en=None`.
10. Si el DNI ya existe, el paciente se reutiliza sin modificar nombre, apellido, teléfono ni email. Si el paciente activo no tiene email registrado, debe enviar uno como propuesta para revisión.
11. Se valida ventana pública de reserva, anticipación mínima, disponibilidad, excepciones y superposición.
12. Dentro de la transacción se detectan duplicados exactos y se aplica el máximo de pendientes por DNI.
13. Se crea un turno pendiente. El modo legacy usa 30 minutos; el modo inteligente deriva atención+margen, recalcula bajo bloqueo y guarda snapshots.
14. Se crea una `SolicitudTurnoPublica` con la fotografía inmutable de los datos enviados.
15. Si hay diferencias contra el paciente existente, la solicitud queda pendiente de revisión para recepción.
16. La respuesta pública es neutral y no revela si el paciente existía, si se creó, si hubo diferencias ni si se reutilizó una solicitud previa.

Servicio principal:

```python
crear_solicitud_turno_publica(datos)
```

Internamente delega en el caso de uso transaccional `crear_solicitud_publica_de_turno(datos)`, que crea el paciente nuevo cuando corresponde, bloquea pacientes existentes con `select_for_update()`, maneja carreras por DNI duplicado, recalcula pendientes dentro de la transacción, bloquea la agenda técnica del odontólogo y agenda notificaciones con `transaction.on_commit()`.

Reglas:

- No permite fechas fuera de la ventana pública configurada en el perfil del consultorio.
- Respeta la anticipación mínima configurada.
- No permite horarios ocupados.
- No permite horarios dentro de excepciones de agenda.
- No pide datos clínicos ni administrativos extensos.
- No crea registros si se supera el rate limit por IP/DNI o si la protección de cache no está disponible.
- El máximo de pendientes cuenta solo solicitudes públicas futuras con turno pendiente, no turnos internos, confirmados, cancelados, pasados ni solicitudes rechazadas.
- Un duplicado exacto activo del mismo DNI, odontólogo, fecha, hora y servicio cuando aplica se trata como operación ya registrada y redirige a la confirmación genérica sin reenviar emails.
- Las alertas administrativas sin turno para pacientes archivados se reutilizan dentro de la ventana configurada para no generar pendientes ilimitados.
- Crea asociación paciente-odontólogo.
- No usa el email enviado para notificar a un paciente ya registrado si difiere del email persistido.
- Para pacientes existentes, el aviso y cualquier OTP se envían únicamente al email almacenado previamente, si existe.
- Para pacientes existentes sin email, el email enviado queda guardado como propuesta en `SolicitudTurnoPublica.email_enviado` y no se usa para OTP hasta que recepción lo aplique explícitamente.
- Para pacientes nuevos, se puede enviar confirmación al email enviado, pero ese contacto no queda verificado automáticamente.
- El primer OTP validado correctamente marca `Paciente.email_verificado_en`; crear la solicitud, enviar emails o confirmar el turno no verifican el correo.
- Las diferencias se guardan en `diferencias_detectadas` y solo se muestran a usuarios internos autorizados.

### Agenda inteligente por motivo

Con `TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED=True`, el navegador recibe solo servicios públicos
habilitados para el odontólogo. La duración, margen, puntaje y snapshot nunca se aceptan desde
el cliente. El servidor genera candidatos por grilla, descarta fragmentos inferiores al mínimo
útil, puntúa encajes y extremos, y presenta una selección diversa más alternativas.

Al confirmar, `crear_solicitud_publica_de_turno()` bloquea odontólogo/fecha, bloquea la
configuración, recalcula sin caché y verifica que el candidato siga vigente. La reprogramación
aplica el mismo proceso usando los snapshots del turno original. Cancelar vuelve a liberar el
intervalo. Ver [`agenda_inteligente.md`](agenda_inteligente.md).

## Revisión Integrada de Solicitudes Públicas

Las solicitudes públicas normales crean dos registros:

- un `Turno` en estado `pendiente`;
- una `SolicitudTurnoPublica` asociada al turno como fotografía auditable de los datos enviados.

El trabajo diario ya no requiere entrar a una bandeja separada. El turno aparece directamente en:

- listado de Turnos;
- Agenda diaria;
- Agenda semanal.

Si la solicitud necesita revisión se muestra la etiqueta `Datos por revisar`. Al abrir el turno se muestra la sección `Solicitud realizada desde la web`, con datos registrados y datos enviados lado a lado. Sólo se resaltan diferencias.

Para confirmar un turno con revisión pendiente se usa la pantalla `Revisar y confirmar turno`. En un solo POST se decide la revisión y se confirma el turno con duración real.

Acciones permitidas:

- conservar los datos actuales;
- aplicar únicamente campos seleccionados;
- validar administrativamente un paciente nuevo;
- rechazar la solicitud y cancelar el turno pendiente.

Reglas de seguridad:

- Los datos enviados desde la web no sobrescriben automáticamente pacientes existentes.
- Teléfono y email enviados se tratan como datos no verificados.
- Si se conservan datos actuales, la confirmación usa el contacto persistido.
- Si se aplican campos seleccionados, sólo esos campos cambian.
- Si se aplica un email propuesto distinto, el paciente queda con `email_verificado_en=None` hasta que complete un OTP exitoso.
- Si el email propuesto coincide con el actual después de normalizar espacios y mayúsculas, no se reescribe ni se borra una verificación existente.
- Pacientes nuevos se validan administrativamente al confirmar.
- La operación integrada usa una transacción: si falla la confirmación, no quedan cambios parciales en paciente ni solicitud.
- Emails y Google Calendar se ejecutan después de guardar correctamente.
- Un segundo POST no vuelve a aplicar cambios porque la solicitud deja de estar pendiente.

## Alertas Administrativas

Las solicitudes que no pueden convertirse en turno permanecen separadas como alertas administrativas. El caso principal es un DNI asociado a un paciente archivado.

Ruta:

```text
/turnos/alertas-administrativas/
```

Estas alertas:

- no se muestran en la agenda;
- aparecen en Inicio sólo cuando hay pendientes;
- se revisan desde una pantalla secundaria;
- mantienen `SolicitudTurnoPublica` como historial;
- no reactivan pacientes archivados automáticamente.

Las rutas antiguas se conservan por compatibilidad:

- `/turnos/solicitudes-publicas/` redirige a Turnos con filtro `Datos por revisar` si no hay alertas, o a Alertas administrativas si existen.
- `/turnos/solicitudes-publicas/<uuid>/` redirige al flujo de confirmar si la solicitud tiene turno, o permite revisar la alerta si no lo tiene.

## Autogestion Publica de Turnos

Rutas:

```text
/turnos/mis-turnos/solicitar-acceso/
/turnos/mis-turnos/verificar/
/turnos/mis-turnos/
/turnos/mis-turnos/<uuid>/cancelar/
/turnos/mis-turnos/<uuid>/reprogramar/
```

El paciente ingresa su DNI para iniciar un desafio de acceso. La respuesta siempre es generica: si el DNI corresponde a un paciente activo con email persistido, se envia un codigo OTP de 6 digitos; si no corresponde, si esta archivado o si solo existe un email propuesto en una solicitud publica, se crea un desafio ficticio y no se revela si el paciente existe.

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
- valida ventana pública, anticipación mínima y disponibilidad del nuevo horario;
- rechaza horarios dentro de excepciones de agenda;
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

## Excepciones de Agenda

Ruta:

```text
/turnos/excepciones/
```

`ExcepcionAgenda` representa bloqueos operativos visibles: vacaciones, feriados, capacitaciones, ausencias, cierres del consultorio o bloqueos parciales. Puede ser global para todo el consultorio o específica de un odontólogo.

Reglas:

- Recepción, administración y superusuarios pueden gestionar excepciones globales y por odontólogo.
- Un odontólogo normal solo puede gestionar excepciones de su propia agenda.
- Las excepciones activas bloquean creación, confirmación y reprogramación de turnos.
- Si una excepción afecta turnos pendientes o confirmados existentes, el formulario exige una confirmación explícita en dos pasos.
- Los turnos afectados no se cancelan, no se reprograman y no se sincronizan automáticamente por crear la excepción.
- Las excepciones no generan eventos de Google Calendar.

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
