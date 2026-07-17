# Plan técnico: historia clínica versionada e inmutable

## Alcance

Este documento define la implementación previa al cambio. El objetivo es incorporar controles
técnicos alineados con los principios de integridad, cronología, custodia y recuperabilidad de la
Ley 26.529 y su Decreto 1089/2012, además de la confidencialidad exigida por la Ley 25.326.

El diseño no constituye una certificación ni garantiza cumplimiento jurídico completo. Requiere
revisión legal, operativa y de seguridad antes de usar datos clínicos reales en producción. El
sello HMAC descripto aquí aporta evidencia de integridad; no es una firma digital bajo la Ley
25.506.

## Estado relevado

- `HistoriaClinica` es hoy una evolución mutable: cada edición reemplaza el contenido anterior.
- `HistoriaClinicaAdjunto` conserva metadatos básicos, pero no un SHA-256 persistido y usa
  `CASCADE` desde la historia.
- La creación y edición viven en las vistas, sin una capa transaccional de dominio reutilizable.
- `AccesoClinicoAuditoria` registra acceso, creación y edición, pero no versiones, finalizaciones,
  enmiendas, verificaciones o exportaciones.
- El admin permite modificar historias y adjuntos.
- El perfil del paciente, el odontograma, los templates y los tests consumen historias existentes.
- No existe una exportación clínica completa; el backup de Storage es una herramienta operativa,
  no una copia clínica para entrega.
- La política por objeto ya distingue lectura, escritura, acceso compartido y emergencia. Se
  conserva sin otorgar acceso clínico a recepción o administración.

## Modelo de datos

### Asiento clínico

`HistoriaClinica` se mantiene como el asiento principal y suma:

- `fecha_hora_atencion`: instante real de la actuación, con zona horaria de Django.
- `borrador`: estado editable inicial.
- `bloqueada_para_edicion`: bloqueo explícito del asiento finalizado.
- `finalizada_en` y `finalizada_por`: identidad y momento de cierre.
- `numero_asiento`: folio secuencial único dentro de cada paciente.
- `migrada_desde_legacy`: excepción explícita para registros previos sin trazabilidad completa.

El campo `fecha` continúa por compatibilidad y se sincroniza con la fecha local de
`fecha_hora_atencion`. Las búsquedas actuales por fecha seguirán funcionando.

Las restricciones de base deben admitir solo estos estados:

- Borrador: editable, sin folio ni finalización.
- Finalizado nativo: bloqueado, foliado y con finalizador conocido.
- Finalizado legacy: bloqueado y foliado; puede carecer de finalizador cuando el dato histórico
  no existe.

### Versiones

`HistoriaClinicaVersion` será append-only y contendrá número, snapshot JSON canónico, autor,
momento, motivo, hash anterior y sello HMAC. La combinación historia/número será única.

El snapshot incluirá:

- versión del esquema;
- identidad interna del asiento y su estado;
- paciente y profesional identificados al momento de la versión;
- fecha y hora de atención;
- todos los campos clínicos del asiento;
- metadatos y SHA-256 disponibles de adjuntos;
- referencias existentes del odontograma sin activar la funcionalidad experimental;
- marcas explícitas de migración legacy y trazabilidad previa.

### Enmiendas

`HistoriaClinicaEnmienda` será append-only y solo podrá agregarse a asientos finalizados. Guardará
número secuencial, texto, motivo, profesional, usuario, momento y cadena HMAC. La enmienda no
modifica el asiento original ni sus versiones.

### Adjuntos

Cada adjunto sumará `sha256`. El cálculo se hará una vez al crearlo, sin volver a descargar el
archivo en cada lectura. La relación con la historia pasará a `PROTECT`; tanto el modelo como el
QuerySet y el admin rechazarán el borrado físico.

## Estados y transiciones

1. Creación: se crea un borrador y su versión 1 dentro de una única transacción.
2. Edición: se bloquea la fila, se valida que siga en borrador, se exige un motivo concreto y solo
   se crea una versión cuando cambian campos clínicos o se incorporan adjuntos.
3. Finalización: se bloquean paciente y asiento, se asigna el siguiente folio, se crea la versión
   final y se cierra el asiento de manera atómica.
4. Corrección posterior: se agrega una enmienda numerada; nunca se reabre ni sobrescribe el
   original.

No habrá flujo para desfinalizar ni vistas de borrado.

## Migración de registros existentes

La migración de datos será local a PostgreSQL/SQLite y no accederá a Storage ni APIs externas:

1. Agregar campos y modelos con estados temporalmente compatibles.
2. Ordenar registros por paciente, `fecha`, `creado_en` y PK.
3. Asignar folios consecutivos por paciente.
4. Derivar `fecha_hora_atencion` de `fecha` con hora 00:00 local, dejando documentado que la hora
   histórica exacta no estaba disponible.
5. Marcar cada registro como finalizado, bloqueado y legacy.
6. Usar `actualizado_por` o `creado_por` como finalizador solo cuando ya exista ese dato.
7. Usar `actualizado_en` o `creado_en` como aproximación explícita del momento de migración.
8. Conservar sin cambios el contenido clínico y todos los adjuntos.

La migración no creará versiones ni descargará adjuntos porque no debe depender de una clave o de
Storage durante el despliegue. Un comando idempotente obligatorio inicializará la versión legacy
y los sellos una vez configurada la clave. El snapshot indicará
`trazabilidad_previa_disponible=false`; su sello solo prueba el estado observado desde esa
inicialización, no la integridad histórica anterior.

## Servicios de dominio

La escritura saldrá de las vistas y quedará centralizada en servicios atómicos:

- `crear_historia_borrador`;
- `actualizar_historia_borrador`;
- `crear_version_historia`;
- `finalizar_historia_clinica`;
- `crear_enmienda_historia`;
- `verificar_integridad_historia`;
- `exportar_historia_completa`.

Los servicios críticos usarán `select_for_update()`. El bloqueo del paciente serializará la
asignación de folios y el bloqueo de la historia serializará versiones, finalización y enmiendas.
La auditoría usará mensajes neutros y nunca incluirá diagnóstico, tratamiento, observaciones,
texto de enmiendas, DNI, email o teléfono.

## Sello de integridad

Los snapshots se serializarán como JSON UTF-8 canónico: claves ordenadas y separadores estables.
Cada sello será `HMAC-SHA256(clave, hash_anterior + contenido_canónico)`. La primera versión no
tendrá hash anterior; las siguientes enlazarán el sello previo. Las enmiendas enlazarán el último
sello clínico disponible y luego el de la enmienda anterior.

La clave se leerá exclusivamente de `CLINICAL_INTEGRITY_HMAC_KEY`. No se guardará en la base, en
logs, exports, fixtures ni documentación. Producción fallará al iniciar si el sistema está
habilitado y falta la clave. Rotar la clave requiere un procedimiento explícito; cambiarla sin
registrar la transición hará fallar verificaciones históricas. Perderla impide volver a validar los
sellos existentes, aunque no elimina los registros.

El verificador comprobará numeración, encadenamiento, contenido canónico, estado del asiento y
SHA-256 de adjuntos solo cuando se solicite lectura de archivos. Una discrepancia se informará sin
alterar datos.

## Inmutabilidad

Se aplicarán controles complementarios:

- validación de estado y bloqueo en modelos;
- `save()` de versiones/enmiendas limitado a inserción;
- `delete()` protegido en historias, versiones, enmiendas y adjuntos;
- QuerySets que rechazan actualización/borrado en entidades append-only;
- admin sin borrado y completamente readonly para registros finalizados;
- vistas que devuelven 403 para acciones no permitidas y 404 para objetos fuera de alcance;
- triggers reversibles en PostgreSQL para impedir UPDATE/DELETE de versiones y enmiendas, DELETE
  de historias/adjuntos y cambios clínicos de asientos ya bloqueados.

Los triggers se instalarán solo cuando `connection.vendor == "postgresql"`; SQLite seguirá usando
los controles de Django durante tests. No protegen frente a un superusuario de base capaz de
eliminar los propios triggers.

## Exportación

El personal clínico autorizado podrá generar un ZIP auditado, con motivo obligatorio, que incluya:

- `manifest.json` con esquema, generación, asientos, autores, sellos y hashes de archivos;
- `historia_clinica.html` autosuficiente e imprimible;
- JSON de cada versión y enmienda;
- archivos adjuntos bajo nombres internos seguros.

No contendrá URLs firmadas, tokens, claves ni recursos remotos. El archivo se presentará como copia
de trabajo para un procedimiento institucional; no como copia autenticada automática.

## Permisos y auditoría

Se reutilizará la política clínica existente:

- lectura solo para odontólogos con alcance o emergencia vigente;
- escritura del borrador y enmiendas solo para el odontólogo autorizado por la política actual;
- recepción y administración sin lectura clínica silenciosa;
- 404 para IDs fuera de alcance y 403 para acciones conocidas pero no autorizadas.

Se agregarán eventos para creación/edición de borrador, versiones, finalización, consulta de
versiones/enmiendas, enmiendas, exportación, verificación e intentos de modificación/borrado.

## Riesgos y controles operativos pendientes

- La integridad depende de proteger y respaldar por separado la clave HMAC.
- PostgreSQL y Storage deben respaldarse coordinadamente y probarse mediante restauraciones.
- Un archivo legacy sin SHA-256 seguirá marcado como pendiente hasta ejecutar el backfill.
- Los sellos detectan cambios, pero no identifican por sí solos a quien obtuvo la clave.
- La autenticación de copias, retención, custodia, atención de solicitudes del paciente y respuesta
  a incidentes requieren procedimientos institucionales.
- La concurrencia se probará en PostgreSQL; SQLite no reproduce completamente sus bloqueos.
- El odontograma permanece deshabilitado y solo se capturan referencias ya existentes.

## Validación prevista

Se agregarán tests de modelos, servicios, permisos, vistas, admin, comandos, migración legacy,
exportación, sellos, adjuntos y regresiones. También se ampliará el recorrido Playwright real en
desktop y móvil. La validación final incluirá checks de Django, migraciones, suites relacionadas y
completa, estáticos, formato, codificación, SQL generado y revisión exhaustiva del diff.
