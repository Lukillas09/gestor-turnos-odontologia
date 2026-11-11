# Agenda inteligente determinística

## Alcance

La agenda inteligente permite que el paciente elija un motivo de visita antes de la fecha y
el horario. Cada odontólogo configura la duración visible de ese servicio y, opcionalmente,
un margen operativo posterior. No utiliza inteligencia artificial, aprendizaje automático ni
servicios externos: aplica reglas determinísticas, reproducibles y cubiertas por tests.

La primera versión está detrás de `TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED`. El valor por
defecto es `False`; por lo tanto, una instalación no cambia de comportamiento al desplegar el
código y la migración.

## Modelo de datos

- `TipoTurno` es el catálogo global. Tiene slug estable, texto público sin HTML, icono de una
  lista cerrada, orden y estados activo/visible.
- `TipoTurnoOdontologo` vincula el catálogo con un profesional. Define duración de atención,
  margen posterior, estado y si admite reserva pública.
- `ConfiguracionAgendaInteligente` guarda la grilla, el hueco mínimo útil, la cantidad de
  horarios recomendados, la preservación de bloques largos y el modo de compactación por
  odontólogo. `cantidad_horarios_alternativos` quedó **obsoleto desde `smart-v2`**: la columna
  se conserva sin migración, pero el motor la ignora y ni el panel interno ni el admin la
  muestran.
- `ConfiguracionAgendaInteligente.activa` es el interruptor maestro (solo administración): si se
  apaga, el profesional no ofrece **ningún** horario inteligente. `evitar_huecos` (migración
  aditiva `0019`, `default` y `db_default` `True`: la columna conserva su valor por defecto en la
  base, así revertir el código no impide crear configuraciones) es el interruptor del odontólogo, "Organizar turnos para
  evitar huecos": encendido es el comportamiento de `smart-v2`; apagado ofrece la grilla pura,
  sin antifragmentación, sin anclas ni sugeridos (todo se clasifica `alternativo`). Se aplica en
  el motor, así que la oferta y la revalidación bajo bloqueo no pueden divergir, y no requiere
  subir la versión del algoritmo porque el valor por defecto no cambia ningún resultado.
- `Turno` y `SolicitudTurnoPublica` conservan snapshots del tipo, las duraciones y la versión
  del algoritmo. Los cambios posteriores de configuración no alteran reservas existentes.

Los tipos ya usados se protegen con `PROTECT`. La operación normal es desactivarlos, no
borrarlos. El comando opcional `python manage.py crear_tipos_turno_iniciales` crea únicamente
`Control`, `Limpieza` y `Consulta`, sin habilitarlos para ningún profesional. Admite `--dry-run`
y es idempotente.

## Semántica de duración

```text
duracion_atencion_minutos
    tiempo aproximado comunicado al paciente

margen_posterior_minutos
    tiempo operativo interno posterior

duracion_minutos (bloqueada)
    duracion_atencion_minutos + margen_posterior_minutos
```

`hora_inicio` continúa siendo la hora de llegada. `Turno.hora_fin_atencion` suma la duración
visible efectiva y es la propiedad usada en emails y superficies del paciente;
`Turno.hora_fin_bloqueada` suma la duración total. En registros legacy sin snapshot de atención,
la primera conserva compatibilidad usando `duracion_minutos`. Google Calendar y la agenda
interna usan el fin bloqueado, pero el evento no revela el margen ni datos del paciente.

## Construcción de disponibilidad

Para una fecha se cargan en bloque las disponibilidades activas, los turnos pendientes o
confirmados, las excepciones y las duraciones públicas del odontólogo. Los intervalos ocupados
se fusionan y se restan de la disponibilidad. Los cancelados no ocupan; en una reprogramación
se excluye el turno actual.

La grilla de inicio es independiente de la duración. Por ejemplo, un servicio de 45 minutos
puede comenzar cada 15 minutos. Un candidato solo existe si el bloque completo entra, cumple
la ventana y anticipación públicas, no se superpone y no cae en una excepción.

### Candidatos anclados

Además de la grilla, cada intervalo libre genera dos candidatos anclados a sus bordes: el
inicio del intervalo y `fin del intervalo - duración bloqueada`. Así, si un turno interno con
duración manual termina 10:20, se ofrece 10:20 aunque no caiga en la grilla. Las anclas se
redondean a minutos enteros (el inicio hacia arriba y el fin hacia abajo) porque el horario
viaja como `HH:MM` y se revalida por igualdad exacta al guardar. Se descartan duplicados y se
respetan `hora_inicio_minima`, anticipación, ventana pública y excepciones.

### Antifragmentación y fallback por intervalo

Un candidato deja un fragmento inútil si a cualquiera de sus lados queda un resto mayor que
cero y menor que `hueco_minimo_util_minutos`. Cero es válido. Es una **preferencia, no una
prohibición**: si en un intervalo todos los candidatos que entran dejarían un fragmento inútil,
se rescata el mejor candidato pegado a un borde (con preferencia por quedar pegado a un turno
existente) y lleva la razón técnica `fallback_fragmentacion`. Por ejemplo, un hueco de 60
minutos con un servicio de 45 ofrece 10:00 en lugar de quedar vacío. Los restos se valoran
también contra las duraciones bloqueadas de los demás servicios públicos reales del profesional.

`descartados_por_fragmentacion` cuenta solo los candidatos que no se ofrecieron; el rescatado
no se cuenta. Los candidatos que la anticipación mínima ya excluyó tampoco participan del
fallback.

## Orden de preferencia `smart-v2`

La versión persistida es `smart-v2`. Reemplaza a los pesos numéricos de `smart-v1` por un
**orden lexicográfico explícito**: se compara el primer criterio y solo se pasa al siguiente en
caso de empate. De mayor a menor importancia:

| | Criterio | Valores |
| --- | --- | --- |
| a | Ocupa exactamente todo el intervalo libre | 1 / 0 |
| b | No deja un hueco inútil (0 solo en un candidato de fallback) | 1 / 0 |
| c | Queda pegado a un turno o bloqueo (2) o al borde de la disponibilidad (1) | 2 / 1 / 0 |
| d | No rompe un bloque largo (si `preservar_bloques_largos` está activo) | 1 / 0 |
| e | El resto sirve para otro servicio publicado: exacto (2) o admite (1) | 2 / 1 / 0 |
| f | Coincide con el modo de compactación (inicio o final) | 1 / 0 |
| | Desempate final: hora de inicio más temprana y luego fin bloqueado | |

`puntaje` es esa tupla codificada en seis dígitos (`a·10⁵ + b·10⁴ + c·10³ + d·10² + e·10 + f`,
por ejemplo `112100` para un horario que ocupa un hueco exacto entre turnos). Ordenar por
puntaje descendente equivale a comparar la tupla, y cada dígito se puede explicar en una
auditoría. **El puntaje solo es comparable dentro de la misma `algoritmo_version`**: el de
`smart-v1` se calculaba con otros pesos y no debe compararse con el de `smart-v2`.

Consecuencias del orden elegido:

- Como (b) va antes que (c), un horario de fallback queda por debajo de cualquier horario
  válido del día, aunque llene un hueco entre dos turnos. Casi nunca se recomienda, pero siempre
  se ofrece entre los alternativos.
- Se eliminó el matiz de `smart-v1` que castigaba distinto reducir el *único* bloque largo del
  día; (d) ahora es un booleano que penaliza romper cualquier bloque largo.

La puntuación nunca vuelve válido un horario inválido. Los empates se resuelven por hora de
inicio y fin bloqueado, por lo que el resultado es estable. Los turnos y solicitudes creados
antes conservan su snapshot `smart-v1` con su puntaje original; las reservas y reprogramaciones
nuevas guardan `smart-v2`.

## Horarios sugeridos y lista cronológica

Los candidatos se ordenan por el orden de preferencia. Los **sugeridos son un destacado, no un
filtro**: la selección (`recomendados` en el motor, `cantidad_horarios_recomendados`) intenta
incluir mañana y tarde cuando ambas existen y separa opciones al menos 60 minutos antes de
completar el cupo. Todos los demás candidatos válidos son alternativos, de modo que
`recomendados + alternativos = total_candidatos_validos`. `cantidad_horarios_alternativos` ya no
limita el resultado: el trabajo está acotado por construcción (una grilla más dos anclas por
intervalo libre). `buscar_candidato` acepta cualquier horario ofrecido, incluidos los anclados y
los de fallback, tanto en la reserva como en la reprogramación pública.

### Qué ve el paciente

El criterio de "sugerido" optimiza la agenda del consultorio, no la comodidad del paciente, por
eso no se presenta como "recomendado". La pantalla de reserva muestra **todos** los horarios en
una lista cronológica por franja (Mañana antes de las 13:00, Tarde desde las 13:00):

- los sugeridos llevan una estrella (`is-suggested`, `data-slot-suggested="true"`), un borde
  distinto y un `aria-label` que termina en ", sugerido", de modo que el estado no depende solo
  del color;
- una leyenda "Horario sugerido por el consultorio" aparece solo si hay sugeridos;
- `ResultadoHorariosInteligentes.cronologicos` es la única fuente: el render sin JavaScript
  (`public_slot_card.html`) y el JSON (`horarios_manana`, `horarios_tarde`, con `sugerido`,
  `hay_sugeridos`) salen de las mismas opciones, y `public-booking.js` usa una sola función de
  render para ambos flujos. Un test compara las ternas (franja, horario, sugerido) del HTML y del
  JSON;
- el JSON no expone puntaje ni razones; `sugerido` es un booleano derivado de la clasificación.

En la **reprogramación pública** no se destacan horarios: el JSON y las opciones del formulario
devuelven una sola lista cronológica (`horarios`).

### Estado vacío: próximo disponible

Si el día elegido no tiene horarios, la respuesta incluye `proximo_disponible` (fecha, hora y la
URL del formulario de datos de ese horario) y la pantalla muestra "Próximo disponible: <día> ·
<hora>" con un link directo. Si no hay horarios posteriores dentro de la ventana pública se
muestra "No encontramos horarios disponibles después de esta fecha dentro de la ventana de
reserva...". La búsqueda solo mira hacia adelante, por eso el texto no afirma nada sobre los
días anteriores (que pueden tener horarios).

`buscar_proximo_horario_disponible` recorre desde el día siguiente al elegido hasta el último día
de la ventana pública de reserva. Carga la agenda de todo el rango en bloque
(`cargar_contexto_agenda`: disponibilidades, turnos, excepciones y duraciones, con una cantidad de
consultas constante sin importar el largo de la ventana) y evalúa cada día en memoria con
`calcular_horarios_del_dia`, la misma función que usa `calcular_horarios_inteligentes` para un
día. La búsqueda solo se ejecuta cuando el día elegido está vacío, no usa caché y es informativa:
al reservar se vuelve a validar bajo bloqueo. No aplica al flujo legacy ni a la reprogramación.

## Flujo público y seguridad

Con el flag activo:

1. El paciente elige odontólogo.
2. `/turnos/solicitar/tipos/` devuelve solo servicios activos, visibles y habilitados para ese
   profesional.
3. Elige el motivo y la fecha.
4. `/turnos/solicitar/horarios/` devuelve una lista cronológica por franja, con los sugeridos
   marcados, y el próximo disponible cuando el día está vacío.
5. La URL conserva odontólogo, tipo, fecha, hora y clasificación; nunca duración, margen o
   puntaje.
6. El formulario final vuelve a derivar la configuración en el servidor.
7. El servicio abre una transacción, bloquea la agenda y la configuración, recalcula sin caché
   y recién entonces crea el turno y sus snapshots.

La protección contra rate limiting, Turnstile, idempotencia, máximo de pendientes y mensajes
neutrales sigue siendo la misma y usa PostgreSQL como autoridad compartida. Dos procesos no
pueden confirmar el mismo intervalo porque comparten el bloqueo técnico por odontólogo/fecha
y revalidan dentro de la transacción.

## Caché

Cuando su TTL es mayor a cero, la caché corta incluye odontólogo, fecha, configuración de servicio, timestamps del tipo,
servicio y agenda, versión del algoritmo y bucket temporal. Una caída de caché no impide
calcular horarios; se registra únicamente etapa, clase de caché y tipo de error. La caché nunca
es autoridad al guardar: la validación definitiva siempre es transaccional y sin caché.

El valor recomendado sin Redis es `TURNOS_PUBLIC_BOOKING_HORARIOS_CACHE_SECONDS=0`.
`LocMemCache` puede usarse con un TTL mayor sólo como optimización local por worker.

Una cancelación deja de ocupar inmediatamente. El bucket corto limita la antigüedad visual y
el POST recalcula de todos modos.

## Reprogramación y turnos internos

La reprogramación pública usa los snapshots del turno original, no la configuración vigente.
También excluye el turno actual y revalida bajo bloqueo. Como el horario nuevo se revalida con
el algoritmo vigente, un turno `smart-v1` reprogramado por el paciente pasa a guardar
`smart-v2` junto con el puntaje del nuevo horario; los turnos que no se reprograman conservan
su snapshot. Los turnos legacy sin tipo continúan usando su duración existente.

El alta interna puede seleccionar un tipo y recibir la duración configurada, o no seleccionar
ninguno y cargar una duración manual para casos complejos. El algoritmo público no restringe
la decisión profesional. Una modificación explícita de duración al confirmar vuelve a validar
superposiciones y requiere la confirmación visible del usuario interno.

## Concurrencia y orden de bloqueos

Las mutaciones que pueden competir adquieren primero los bloqueos técnicos de agenda, ordenados
por `(odontologo_id, fecha)`. Luego bloquean los turnos por clave primaria y, cuando corresponde,
la solicitud y la acción pública por clave primaria. La disponibilidad se recalcula dentro de
esa misma transacción; una caché nunca decide si el horario puede guardarse.

Las pruebas concurrentes reales usan `TransactionTestCase`, conexiones independientes y
timeouts de PostgreSQL. En SQLite se omiten explícitamente porque ese motor no ofrece una
semántica equivalente para `select_for_update()`. Google Calendar y email se ejecutan después
del commit y no mantienen los locks mientras esperan a un proveedor.

## Migración legacy

La migración `0017_agenda_inteligente` es aditiva. Para turnos anteriores copia:

```text
duracion_atencion_minutos = duracion_minutos
margen_posterior_minutos_snapshot = 0
clasificacion_horario = legacy
```

No infiere tipos desde el motivo, no cambia fechas o duración total y no llama servicios
externos.

## Configuración interna

La sección `/turnos/configuracion/servicios/` permite:

- al odontólogo, administrar **solo lo suyo** con una vista simple: la duración aproximada y si
  cada servicio se reserva online, y el interruptor "Organizar turnos para evitar huecos". Un
  servicio nuevo del odontólogo queda sin margen operativo;
- a administración, gestionar el catálogo y cualquier profesional, con todos los campos técnicos
  (margen, estado, grilla, hueco mínimo, bloque largo, modo de compactación, cantidad de
  recomendados y el interruptor maestro `activa`);
- a recepción, consultar la configuración sin modificarla.

El formulario se elige en el servidor según el rol (`get_form_class`), no se oculta en la
plantilla: un POST manual del odontólogo con campos de administración se ignora (el formulario
solo declara sus campos) y no puede apagar `activa` ni cambiar el margen por omisión.

Sin servicios públicos configurados, el profesional no ofrece horarios bajo el nuevo flujo. No
existe fallback silencioso a 30 minutos.

## Observabilidad

Los logs del cálculo incluyen solamente `odontologo_id`, `tipo_turno_id` cuando corresponde,
fecha, cantidades de candidatos/recomendados/alternativos/descartados, `cache_hit`, duración de
cálculo y versión. No registran DNI, nombre, teléfono, email, comentario, razones por candidato
ni información clínica.

## Activación y prueba manual

1. Aplicar migraciones y ejecutar la suite en SQLite y PostgreSQL.
2. Crear tipos globales neutros y configurar duraciones por odontólogo.
3. Mantener `TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED=False` en producción.
4. Activarlo primero en staging y probar mañana/tarde, excepciones, cancelación,
   reprogramación, emails, Calendar, caché deshabilitada y Redis opcional si se configura.
5. Revisar logs y tiempos habituales, con objetivo inferior a 200 ms sin cold start.
6. Activar el flag en producción solo después de validar todos los odontólogos publicados.

Para rollback funcional, volver el flag a `False` y redesplegar. El flujo legacy recupera la
duración pública de 30 minutos; los turnos ya creados conservan sus snapshots y duración total.
No revertir la migración ni borrar tipos usados. Si queda una configuración incorrecta,
desactivar el servicio o su reserva pública desde el panel.
