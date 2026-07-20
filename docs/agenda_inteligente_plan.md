# Plan técnico: agenda inteligente para reservas públicas

## Estado actual

El flujo público selecciona odontólogo, fecha y hora, y crea un `Turno` pendiente con una
duración fija de 30 minutos. La disponibilidad se calcula con `DisponibilidadOdontologo`,
turnos pendientes/confirmados y `ExcepcionAgenda`. Los resultados públicos se cachean por
odontólogo, fecha, duración y bucket temporal; Redis tiene fallback tolerante a fallos.

La confirmación definitiva ya usa transacciones y un bloqueo estable por
`(odontologo_id, fecha)` mediante `bloquear_agendas_de_turnos()`. La reserva pública vuelve a
consultar disponibilidad después de adquirir ese bloqueo. OTP, rate limits, Turnstile e
idempotencia son capas independientes que deben conservarse.

La reprogramación pública conserva actualmente `Turno.duracion_minutos`, Google Calendar usa
`fecha_hora_fin` y las agendas interna diaria/semanal también derivan el final desde esa
duración.

## Compatibilidad y feature flag

`TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED` tendrá valor predeterminado `False`.

- Desactivado: se conserva el contrato actual, las URLs existentes y la duración pública fija
  de 30 minutos.
- Activado: se exige un tipo de turno configurado para el odontólogo, se usa su duración real y
  se muestran horarios recomendados y alternativos.
- El flag no se activará mediante migraciones ni se modificará en Railway desde el código.
- Los tests del flujo nuevo usarán `override_settings`.

## Modelos propuestos

### `TipoTurno`

Catálogo global de motivos operativos. Contendrá nombre, slug estable, descripción pública,
clave de icono permitida, orden, visibilidad, estado y auditoría de usuario/fecha. No admitirá
HTML ni iconos arbitrarios. Un tipo utilizado quedará protegido contra borrado y podrá
desactivarse.

### `TipoTurnoOdontologo`

Configuración única por odontólogo y tipo. Define duración de atención, margen posterior,
habilitación pública y estado. Validará múltiplos de cinco, límites y coherencia de publicación.

### `ConfiguracionAgendaInteligente`

Configuración uno a uno por odontólogo: grilla, hueco mínimo útil, cantidades, preservación de
bloques largos y modo de compactación. Se creará de forma explícita al consultar o guardar la
configuración, sin signals.

### Snapshots

`Turno` y `SolicitudTurnoPublica` conservarán el tipo, su nombre, duración visible, margen,
duración bloqueada, versión del algoritmo, clasificación y puntaje técnico. Los campos serán
opcionales para datos legacy.

## Semántica de duración

- `duracion_atencion_minutos`: aproximación mostrada al paciente.
- `margen_posterior_minutos_snapshot`: tiempo operativo que no se muestra públicamente.
- `duracion_minutos`: total bloqueado en agenda, igual a atención más margen.
- `hora_inicio`: hora de llegada del paciente.

Modificar una configuración solo afectará reservas futuras. Reprogramar un turno público usará
sus snapshots originales. La creación interna seguirá permitiendo una duración manual sin tipo.

## Migración

Una migración aditiva creará modelos y campos. Una operación de datos asignará únicamente a
turnos existentes:

- `duracion_atencion_minutos = duracion_minutos`;
- `margen_posterior_minutos_snapshot = 0`;
- `clasificacion_horario = "legacy"`.

No se inferirá un tipo desde `motivo`, no se crearán tratamientos clínicos y no se cambiarán
fechas, horas, estados ni duraciones existentes.

## Motor determinístico

`turnos.smart_scheduling` contendrá dataclasses inmutables y funciones puras. El adaptador de
base de datos obtendrá en consultas agrupadas disponibilidades, turnos activos, excepciones y
duraciones públicas. El cálculo se hará en memoria.

### Intervalos libres

1. Convertir disponibilidades del día en intervalos.
2. Convertir turnos pendientes/confirmados y excepciones aplicables en intervalos ocupados.
3. Fusionar ocupaciones superpuestas.
4. Restarlas de cada disponibilidad.
5. Excluir el turno actual durante una reprogramación.

Los cancelados no ocupan agenda. La anticipación y ventana pública se aplicarán antes de ofrecer
un candidato.

### Generación

Cada intervalo comienza en el primer minuto alineado con la grilla configurada y avanza por esa
grilla. La duración del servicio no modifica el paso. Solo se generan candidatos donde entra el
bloque completo.

Se descarta un candidato cuando cualquiera de sus restos es mayor que cero y menor que
`hueco_minimo_util_minutos`. Un resto cero es válido. Las duraciones bloqueadas de los demás
servicios públicos determinan si un resto admite otro turno real.

### Puntuación `smart-v1`

La fórmula central será la suma de:

- `+1000`: ocupa exactamente el intervalo libre;
- `+350`: pegado al inicio;
- `+350`: pegado al final;
- `+220`: completa un hueco delimitado por ocupaciones;
- `+180`: un resto coincide exactamente con otra duración pública;
- `+100`: un resto admite al menos otro servicio público;
- `+60`: conserva un bloque largo continuo;
- `+40`: no divide el intervalo;
- `+80`: extremo favorecido por modo `inicio` o `final`;
- `-150`: divide el intervalo en dos restos;
- `-220`: reduce el único bloque largo disponible;
- `-300`: divide un bloque largo y ninguno de los restos continúa siendo largo.

La puntuación nunca habilita un candidato inválido. Los empates se resuelven de forma estable por
hora de inicio y fin.

### Recomendados y alternativas

Los recomendados se eligen por puntaje con diversidad: se intenta incluir mañana y tarde, y se
prioriza una separación mínima de 60 minutos. Luego se completan con los mejores candidatos
restantes. Los candidatos válidos no elegidos forman las alternativas hasta el límite configurado.

Las razones técnicas existen solo en memoria para tests y diagnóstico; no se exponen ni se
registran con datos personales.

## Caché

La clave `turnos:public_booking:smart:v1` incluirá odontólogo, fecha, configuración de tipo,
timestamps de tipo/configuración/agenda, versión y bucket. Se serializarán solamente datos
públicos mínimos. Una caída de Redis registrará un warning neutro y calculará el resultado sin
caché.

El resultado cacheado nunca se usará como validación final.

## Concurrencia e idempotencia

La creación inteligente ejecutará dentro de `transaction.atomic()`:

1. normalización y validación del paciente;
2. bloqueo de configuración de servicio;
3. bloqueo de agenda por odontólogo y fecha;
4. recálculo sin caché;
5. validación del candidato;
6. creación de turno y solicitud con snapshots;
7. notificación mediante `transaction.on_commit()`.

La detección de duplicado exacto incorporará el tipo cuando el flag esté activo. Un horario ya
ocupado por otro tipo no creará un segundo turno ni revelará información del existente.

## Flujo e interfaz

El flujo progresivo será odontólogo, motivo, fecha, horario y datos. Se reutilizará la pantalla
actual para evitar nuevas sesiones intermedias. Se agregará un endpoint público de tipos y se
adaptará el endpoint de horarios.

La UI será mobile-first, con botones reales de al menos 44 px, estado seleccionado, carga,
errores con `aria-live`, `aria-expanded` para alternativas y navegación hacia atrás que conserve
odontólogo/tipo/fecha. Puntajes, márgenes y razones no serán públicos.

## Flujos relacionados

- Reprogramación pública: usa snapshots y excluye el turno actual.
- Creación interna: tipo opcional; duración manual sigue disponible.
- Confirmación: muestra duración visible/bloqueada y exige confirmación explícita si se altera.
- Google Calendar: conserva `Turno.duracion_minutos` como final y agrega el tipo snapshot a la
  descripción interna.
- Emails: muestran tipo y duración aproximada, nunca margen, puntaje o clasificación.
- Gestión interna: odontólogos configuran solo sus servicios; administradores pueden configurar
  cualquiera y el catálogo; recepción tiene lectura sin edición.

## Pruebas

Se cubrirán funciones puras, modelos, snapshots, endpoints, flujo público con flag encendido y
apagado, manipulación de parámetros, caché, idempotencia, concurrencia PostgreSQL,
reprogramación, permisos internos, Calendar, emails y Playwright en móvil/escritorio.

## Riesgos y mitigaciones

- **Regresión del flujo actual:** ramas explícitas por flag y suite legacy sin cambios de
  configuración.
- **Candidatos obsoletos:** recálculo bajo bloqueo antes de crear o reprogramar.
- **Explosión de consultas:** carga por día en consultas agrupadas y cálculo en memoria.
- **Caché obsoleta:** timestamps de configuración, bucket corto y validación final sin caché.
- **Snapshots inconsistentes:** un único servicio transaccional deriva todos los valores.
- **Configuración incompleta:** sin servicios públicos no hay fallback silencioso a 30 minutos.
- **Migración de producción:** cambios aditivos, backfill local y sin llamadas externas.

## Rollback

Desactivar `TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED` restaura inmediatamente el flujo legacy de
30 minutos. Los modelos y snapshots pueden permanecer sin afectar reservas antiguas. No se deben
revertir migraciones con datos en producción; cualquier limpieza posterior será una migración
separada y explícita.
