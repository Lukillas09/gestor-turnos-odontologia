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
- `ConfiguracionAgendaInteligente` guarda la grilla, el hueco mínimo útil, los límites de
  resultados, la preservación de bloques largos y el modo de compactación por odontólogo.
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

`hora_inicio` continúa siendo la hora de llegada. El margen no se muestra en emails ni en la
interfaz pública. Google Calendar usa `Turno.duracion_minutos`, por lo que bloquea el intervalo
completo. La descripción interna del evento puede indicar el margen sin incorporar datos
clínicos adicionales.

## Construcción de disponibilidad

Para una fecha se cargan en bloque las disponibilidades activas, los turnos pendientes o
confirmados, las excepciones y las duraciones públicas del odontólogo. Los intervalos ocupados
se fusionan y se restan de la disponibilidad. Los cancelados no ocupan; en una reprogramación
se excluye el turno actual.

La grilla de inicio es independiente de la duración. Por ejemplo, un servicio de 45 minutos
puede comenzar cada 15 minutos. Un candidato solo existe si el bloque completo entra, cumple
la ventana y anticipación públicas, no se superpone y no cae en una excepción.

Un candidato se descarta si deja a cualquiera de sus lados un fragmento mayor que cero y menor
que `hueco_minimo_util_minutos`. Cero es válido. Los restos se valoran también contra las
duraciones bloqueadas de los demás servicios públicos reales del profesional.

## Puntuación v1

La versión persistida es `smart-v1`. La fórmula centralizada es:

| Regla | Puntos |
| --- | ---: |
| Ocupa exactamente todo el intervalo libre | +1000 |
| Queda pegado al inicio | +350 |
| Queda pegado al final | +350 |
| Completa un hueco exacto limitado por turnos | +220 |
| Un resto coincide exactamente con otro servicio | +180 |
| Un resto admite al menos otro servicio | +100 |
| Conserva un bloque largo | +60 |
| Evita dividir el intervalo | +40 |
| Bonificación del modo inicio/final | +80 |
| Divide el intervalo en dos | -150 |
| Reduce el único bloque largo | -220 |
| Fragmenta un bloque largo sin conservar otro largo | -300 |

La puntuación nunca vuelve válido un horario inválido. Los empates se resuelven por hora de
inicio y fin bloqueado, por lo que el resultado es estable.

## Recomendados y alternativas

Los candidatos se ordenan por puntuación. La selección recomendada intenta incluir mañana y
tarde cuando ambas existen y separa opciones al menos 60 minutos antes de completar el cupo.
Los candidatos válidos restantes aparecen en `Ver más horarios`, hasta el límite configurado.
El paciente puede elegir cualquiera de ellos y se guarda la clasificación elegida.

Las razones técnicas y el puntaje sirven para tests y auditoría interna. Los endpoints públicos
no exponen puntajes, razones, turnos ocupados ni datos de otros pacientes.

## Flujo público y seguridad

Con el flag activo:

1. El paciente elige odontólogo.
2. `/turnos/solicitar/tipos/` devuelve solo servicios activos, visibles y habilitados para ese
   profesional.
3. Elige el motivo y la fecha.
4. `/turnos/solicitar/horarios/` devuelve recomendados y alternativas.
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
También excluye el turno actual y revalida bajo bloqueo. Los turnos legacy sin tipo continúan
usando su duración existente.

El alta interna puede seleccionar un tipo y recibir la duración configurada, o no seleccionar
ninguno y cargar una duración manual para casos complejos. El algoritmo público no restringe
la decisión profesional. Una modificación explícita de duración al confirmar vuelve a validar
superposiciones y requiere la confirmación visible del usuario interno.

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

- al odontólogo, administrar sus servicios y su agenda inteligente;
- a administración, gestionar el catálogo y cualquier profesional;
- a recepción, consultar la configuración sin modificarla.

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
