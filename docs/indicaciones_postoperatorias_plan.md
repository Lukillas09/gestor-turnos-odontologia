# Plan técnico: indicaciones postoperatorias

## Alcance

El módulo permitirá que un odontólogo autorizado prepare, revise, emita, descargue,
envíe y anule indicaciones postoperatorias de un paciente activo. No implementa recetas,
prescripciones, firma digital certificada ni generación automática de contenido médico.
Las plantillas y los textos clínicos de producción serán ingresados y aprobados por los
profesionales del consultorio.

La funcionalidad vivirá en una app independiente, `indicaciones`, y estará desactivada por
defecto mediante `INDICACIONES_POSTOPERATORIAS_ENABLED=False`.

## Arquitectura

- `models.py`: plantillas, versiones append-only e indicaciones con estados coherentes.
- `selectors.py`: consultas acotadas por paciente, profesional y política clínica.
- `permissions.py`: adaptación de `historias.access_policy` sin acceso administrativo
  implícito.
- `services.py`: creación y edición de borradores, emisión, anulación, reemplazo y
  versionado de plantillas.
- `pdf.py`: documento A4 generado con ReportLab, sin recursos remotos.
- `integrity.py`: sello HMAC reutilizando `CLINICAL_INTEGRITY_HMAC_KEY`.
- `emails.py`: entrega y reintentos separados de la transacción de emisión.
- `views.py`, `forms.py` y templates: flujo interno protegido y responsive.
- `management/commands/reenviar_indicaciones_pendientes.py`: reintentos operativos.
- `tests/` y `tests_e2e/`: dominio, seguridad, PDF, correo y navegación real.

Las URLs se incluirán bajo `/pacientes/<paciente_pk>/indicaciones/`. Con el flag apagado
no habrá controles visibles y cualquier acceso directo responderá como recurso no
disponible, sin borrar ni alterar datos preexistentes.

## Modelos

### PlantillaIndicacion

Contendrá nombre, procedimiento, título y secciones configurables, número de versión,
estado activo, autores y fechas. No habrá migraciones con indicaciones médicas de ejemplo.
La actualización se realizará exclusivamente mediante un servicio que exige motivo,
captura la versión anterior e incrementa el número de versión bajo bloqueo transaccional.
Se bloquearán el borrado físico y las actualizaciones masivas.

### PlantillaIndicacionVersion

Será append-only y conservará el snapshot anterior, número de versión, motivo, autor y
fecha. Tendrá unicidad por plantilla y versión. Modelo, QuerySet, admin y PostgreSQL
impedirán editar o borrar registros existentes.

### IndicacionPaciente

Estados:

- `borrador`: editable únicamente por su odontólogo, sin PDF ni datos de emisión.
- `emitida`: contenido, vínculos, snapshots, PDF, hash y sello inmutables.
- `anulada`: conserva el documento original y agrega autor, fecha y motivo de anulación.

La corrección de una emitida se representará con una nueva indicación vinculada por
`reemplaza_a`; nunca se sobrescribirá el original. Se bloqueará el borrado físico en todos
los estados y las mutaciones masivas. Las restricciones de base comprobarán la coherencia
de cada estado y PostgreSQL agregará triggers para las defensas que SQLite no puede
expresar completamente.

Los snapshots de paciente, profesional, consultorio y documento incluirán solo los datos
necesarios en el momento de emisión. No contendrán URLs firmadas, secretos, tokens,
sesiones ni contenido técnico de infraestructura.

## Permisos

Se reutilizarán `obtener_politica_lectura`, `obtener_politica_escritura` y los filtros de
`historias.access_policy`.

- Lectura: odontólogo con alcance clínico vigente; acceso de emergencia solo donde la
  política existente lo autorice y lo audite.
- Escritura, emisión y anulación: odontólogo activo asociado al paciente y usando su propia
  identidad profesional.
- Recepción: sin acceso al contenido, PDF o acciones clínicas.
- Administración: sin acceso clínico implícito por ser staff o superusuario.
- Objeto fuera de alcance: `404`.
- Objeto visible con acción inválida o no autorizada: `403`.

El odontólogo nunca será un campo editable del formulario.

## Emisión e inmutabilidad

`emitir_indicacion()` usará `transaction.atomic()` y `select_for_update()`:

1. vuelve a cargar y bloquea el borrador;
2. revalida estado, paciente, profesional y permisos;
3. captura snapshots independientes;
4. genera el PDF en memoria;
5. guarda el archivo mediante el storage privado;
6. calcula el SHA-256 final y el sello HMAC;
7. persiste el estado emitido y registra auditoría;
8. agenda el correo con `transaction.on_commit()`.

Un POST repetido sobre un documento ya emitido devolverá el mismo resultado y no volverá
a crear PDF ni a programar el correo inicial. Si la transacción falla después de subir el
archivo, el servicio intentará limpiar únicamente ese archivo incompleto sin ocultar la
excepción original.

## Integridad

El sello definitivo se calculará con la serialización canónica ya usada por `historias` y
la misma `CLINICAL_INTEGRITY_HMAC_KEY`, sobre el snapshot completo y el SHA-256 del PDF.
No se creará una segunda clave.

Un archivo no puede contener de forma verificable el HMAC de su propio hash final: al
insertar ese valor cambiaría el archivo y, por tanto, su hash. Por eso el PDF mostrará una
referencia técnica abreviada calculada sobre el UUID y los snapshots previos al PDF; el
detalle protegido mostrará el SHA-256 y el sello HMAC definitivo almacenado, que además
cubre esa referencia. La documentación no presentará este sello como firma digital.

## PDF

Se agregará ReportLab con versión fijada. El PDF será A4, paginado, legible en color y en
blanco y negro, con identidad del consultorio, paciente, profesional, secciones clínicas,
próximo control, contacto, estado, fecha, identificador y referencia de integridad.

Incluirá obligatoriamente:

> Este documento contiene indicaciones de cuidado brindadas por el profesional y no
> constituye una receta electrónica de medicamentos.

También incluirá el aviso de contacto ante síntomas inesperados, empeoramiento o urgencia.
No incorporará textos médicos generados, QR, JavaScript, recursos remotos ni imágenes de
firma como mecanismo de autenticidad.

## Almacenamiento

El archivo se guardará como `indicaciones/<uuid>/documento.pdf`, sin nombre, DNI, email ni
diagnóstico en la ruta. Se usará un alias de storage clínico privado. En desarrollo local,
los archivos quedarán fuera de `MEDIA_ROOT`; en producción se reutilizará el backend de
Supabase configurado para el bucket privado.

La descarga será exclusivamente por una vista autorizada con `FileResponse`, nombre
seguro, cabecera `nosniff` y evento de auditoría. Ningún template ni admin usará `pdf.url`.

## Email y Resend

El envío solo se programará si el paciente continúa activo y el email persistido tiene
`email_verificado_en`. El cuerpo será breve y no incluirá DNI, diagnóstico, historia ni
texto clínico extenso; el PDF será el adjunto.

El backend HTTP conservará el comportamiento actual para mensajes sin adjuntos. Para
Resend validará nombre, MIME, bytes y tamaño; codificará Base64 y enviará el arreglo
`attachments`. No registrará API keys, contenido ni Base64. Los mensajes con adjuntos en
proveedores no implementados seguirán fallando de forma explícita.

La emisión y el correo serán operaciones distintas. Un fallo del proveedor dejará la
indicación emitida, registrará un error neutral, aumentará intentos y permitirá reintento.
Una clave de idempotencia estable reducirá duplicados ante respuestas ambiguas del
proveedor. El destinatario capturado no cambiará silenciosamente; usar un nuevo email
verificado requerirá una acción explícita.

## Auditoría

Se ampliará `AccesoClinicoAuditoria.Accion` y se reutilizará
`registrar_evento_acceso_clinico`. La referencia del documento se guardará en
`identificador_solicitado`; no se persistirán contenido, pautas, observaciones, PDF,
Base64 ni email completo en el evento. Las acciones cubrirán creación, edición, lectura,
emisión, PDF, descarga, email, error, reenvío, anulación, reemplazo e intentos denegados.

## Plantillas y administración

La administración de plantillas estará limitada a personal staff con identidad de
odontólogo activa. Cada modificación exigirá un motivo y pasará por el servicio de
versionado. Las versiones serán totalmente readonly.

Las indicaciones en admin estarán limitadas al alcance del profesional, sin alta, borrado
ni edición de emitidas o anuladas, y sin enlaces directos al storage.

## Interfaz

La ficha del paciente incorporará la sección “Indicaciones postoperatorias” solo con el
flag activo y alcance clínico. Mostrará resumen, últimas indicaciones, estados expresados
con texto, acceso al historial y acción de creación cuando corresponda.

Habrá pantallas de lista, alta, edición de borrador, revisión, detalle, reenvío, anulación
y reemplazo. Los formularios y acciones mantendrán el sistema visual existente y se
adaptarán entre 320 y 1440 píxeles sin desplazamiento horizontal ni superposición con la
navegación móvil.

## Pruebas

- Modelos: estados, constraints, inmutabilidad, borrado y versionado.
- Servicios: plantilla, borrador, emisión idempotente, PDF, hashes, sello, storage,
  anulación, reemplazo y permisos.
- Email: destinatario verificado, separación transaccional, errores y reintentos.
- Backend Resend: compatibilidad sin adjunto, PDF Base64, MIME, nombre, tamaño y errores.
- Vistas: feature flag, IDOR, roles, todos los estados y descarga protegida.
- Comando: filtros, límites, dry-run, errores y no duplicación.
- E2E: flujo completo en 390 x 844 y 1440 x 900, descarga y ausencia de overflow.
- Regresión: historias, pacientes, turnos, OTP, recordatorios y suite completa.

## Riesgos y mitigaciones

- Storage no transaccional: limpieza compensatoria del archivo creado si falla la base.
- Respuesta ambigua de email: clave idempotente persistida y estados separados.
- Doble emisión: lock de fila, transición monotónica y callback único.
- Fuga por URL: storage local fuera de media, bucket privado y descarga autenticada.
- Cambios de plantilla: copia al borrador y snapshot de versión, nunca vínculo dinámico.
- Pérdida de clave HMAC: documentación de custodia y backup separado; no hay rotación
  automática en esta entrega.
- Triggers no ejecutados en SQLite: defensas equivalentes de modelo/servicio y pruebas de
  SQL PostgreSQL.

## Compatibilidad

No se modificarán historias finalizadas, versiones, enmiendas ni adjuntos existentes. La
app solamente reutilizará su política de acceso, serialización HMAC y auditoría. Los
correos sin adjuntos, el flujo público, OTP, Google Calendar, turnos y almacenamiento de
archivos clínicos conservarán sus contratos actuales.
