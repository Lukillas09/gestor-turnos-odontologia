# Historia clínica versionada e inmutable

## Alcance

El módulo `historias` implementa controles técnicos para conservar la secuencia de los
asientos clínicos, identificar sus autores, bloquear el contenido finalizado y detectar
alteraciones. Estos controles están alineados con principios de la Ley 26.529, pero no
constituyen por sí solos una certificación ni garantizan cumplimiento jurídico completo.
Antes de producción se requiere revisión legal, operativa y de seguridad.

El sello HMAC usado por el sistema es un **sello de integridad**. No es una firma digital,
no identifica al firmante mediante un certificado y no sustituye los requisitos de la
Ley 25.506.

## Referencias normativas

- La [Ley 26.529](https://www.argentina.gob.ar/normativa/nacional/ley-26529-160432/actualizacion)
  define la historia como cronológica, foliada y completa (art. 12), exige medidas de
  integridad, inalterabilidad, perdurabilidad, recuperabilidad y acceso restringido para
  la historia informatizada (art. 13), reconoce la titularidad y entrega de copia
  (art. 14), identifica el contenido de los asientos (art. 15) y regula integridad,
  unicidad, custodia y legitimación (arts. 16 a 19).
- El [Decreto 1089/2012](https://www.argentina.gob.ar/normativa/nacional/decreto-1089-2012-199296/texto)
  reglamenta la seguridad, conservación y recuperación (art. 12), la historia
  informatizada (art. 13), la solicitud de copia (art. 14) y dispone que las actuaciones
  contengan fecha y hora y no se borre ni escriba sobre lo ya escrito (art. 15).
- La [Ley 25.326](https://www.argentina.gob.ar/normativa/nacional/ley-25326-64790/texto)
  considera sensible la información de salud (art. 2), regula su tratamiento por
  establecimientos y profesionales de salud (art. 8) y exige medidas técnicas y
  organizativas de seguridad y confidencialidad (arts. 9 y 10).
- La [Ley 25.506](https://www.argentina.gob.ar/normativa/nacional/ley-25506-70749/actualizacion)
  diferencia firma digital y firma electrónica y establece requisitos específicos para
  la primera (arts. 2, 5 y 9). El HMAC de esta aplicación no reúne ni pretende reunir
  esos requisitos.

## Modelo de registro

`HistoriaClinica` representa un asiento dentro de la historia general de un paciente.
Cada asiento conserva `fecha` por compatibilidad y usa `fecha_hora_atencion` como momento
clínico real con zona horaria.

Los registros legacy solo tenían fecha. La migración conserva esa fecha y utiliza
medianoche como representación técnica necesaria para el campo `DateTime`; no la presenta
como una hora clínica conocida. La interfaz, el snapshot y la exportación indican
`Hora histórica no disponible` mediante un marcador explícito.

Estados admitidos:

| Estado | Propiedades |
| --- | --- |
| Borrador | Editable mediante servicios, sin folio ni datos de finalización. |
| Finalizada | Bloqueada, con folio por paciente, fecha y usuario finalizador. |
| Finalizada legacy | Bloqueada y foliada; puede carecer de finalizador si no existe evidencia histórica. |

Una restricción de base valida la coherencia entre `borrador`,
`bloqueada_para_edicion`, `finalizada_en`, `finalizada_por`, `numero_asiento` y
`migrada_desde_legacy`. El folio es único por paciente cuando está asignado.

Modelos de trazabilidad:

- `HistoriaClinicaVersion`: snapshot completo append-only de cada guardado efectivo.
- `HistoriaClinicaEnmienda`: actuación posterior append-only sobre un asiento finalizado.
- `HistoriaClinicaAdjunto`: archivo privado con SHA-256 persistido.
- `AccesoClinicoAuditoria`: quién realizó o intentó cada actuación, sin copiar contenido
  clínico al motivo de auditoría.

## Flujo clínico

### Borrador

La creación genera la versión 1 con el motivo técnico `Creación inicial del borrador`.
Una edición exige un motivo concreto de al menos diez caracteres. Los motivos genéricos
sin contexto se rechazan. Si no cambió ningún campo, adjunto o referencia odontológica,
no se crea una versión vacía.

Las escrituras pasan por servicios transaccionales y bloquean el asiento con
`select_for_update()`. Los formularios y vistas no escriben directamente la historia.

### Finalización

La finalización requiere confirmación explícita. Dentro de una transacción:

1. Se bloquean el asiento y el paciente.
2. Se vuelve a comprobar permiso y estado.
3. Se asigna `max(numero_asiento) + 1` para ese paciente.
4. Se establece el finalizador y el momento de cierre.
5. Se crea una versión final encadenada.
6. Se registra la auditoría.

No existe acción de desbloqueo ni desfinalización. Un POST a la antigua edición de un
asiento finalizado responde de forma segura y queda auditado.

### Enmienda

Una corrección posterior crea una `HistoriaClinicaEnmienda`; nunca modifica el asiento
original. El profesional se obtiene del usuario autenticado y no de un campo manipulable.
La numeración se asigna bajo bloqueo del asiento y el sello enlaza con la última versión
o enmienda existente.

## Snapshot

El snapshot tiene `schema_version=1` y conserva:

- identificadores y datos básicos históricos del paciente;
- identificador, nombre, matrícula y especialidad del profesional;
- fecha y hora de atención;
- indicador de disponibilidad de la hora histórica para registros legacy;
- campos clínicos del asiento;
- estado, folio y finalización;
- autor de creación y actualización y sus timestamps;
- metadatos y SHA-256 de adjuntos;
- referencias odontológicas ya existentes, sin activar el odontograma experimental;
- indicador legacy y ausencia de trazabilidad previa cuando corresponde.

No guarda binarios, URLs firmadas, tokens, claves, IP, cookies ni datos de sesión. Se
serializa como JSON UTF-8 con claves ordenadas, separadores estables y fechas ISO 8601 en
UTC.

## Sello de integridad

Cada versión usa HMAC-SHA-256 sobre una representación canónica que incluye el snapshot,
el número de versión, el identificador del asiento, autor, timestamp, motivo y sello
anterior. Cada enmienda usa el mismo principio con su texto, motivo, profesional, autor,
timestamp y enlace anterior.

La cadena permite detectar cambios posteriores mientras la clave permanezca secreta y
estable. No evita que una persona con control simultáneo de base, aplicación, backups y
clave reescriba toda la evidencia. Para ese riesgo hacen falta separación de funciones,
custodia independiente, monitoreo y copias externas.

### Configuración y custodia

Variables:

```env
CLINICAL_INTEGRITY_ENABLED=True
CLINICAL_INTEGRITY_HMAC_KEY=<clave aleatoria independiente y estable>
```

Generación sugerida:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

La clave:

- no debe reutilizar `DJANGO_SECRET_KEY`, claves OAuth ni credenciales de proveedores;
- debe configurarse también en desarrollo cuando la función está habilitada;
- debe residir en el gestor de secretos del entorno, nunca en la base, el repositorio,
  los ZIP exportados o sus manifiestos;
- debe respaldarse mediante un procedimiento de custodia separado y con acceso mínimo;
- debe ser idéntica en todos los procesos que escriben o verifican la misma base.

### Pérdida, compromiso y rotación

Si se pierde la clave, el contenido sigue disponible, pero los sellos históricos ya no
pueden comprobarse con esa instalación. No se debe generar otra clave y volver a sellar
silenciosamente: hay que preservar base, logs y backups y aplicar el runbook de incidente.

La versión actual admite una sola clave activa y no guarda un identificador de clave por
sello. Por eso una rotación planificada requiere conservar la clave anterior y desplegar
primero una evolución de esquema con keyring/versionado. Cambiar la variable directamente
invalida la comprobación de todos los sellos previos. Ante compromiso, congelar nuevas
escrituras, preservar evidencia y coordinar una migración auditada con asesoramiento de
seguridad y legal.

## Adjuntos

Los archivos mantienen las validaciones de tamaño y extensión y reciben SHA-256 al
crearse. Los uploads nuevos también se inspeccionan de forma acotada: se rechazan firmas
comunes de ejecutables, scripts y archivos comprimidos, además de formatos reconocibles
cuya extensión no coincide. La lectura restaura el cursor y no descarga objetos ya
almacenados. Esta defensa no reemplaza un servicio antivirus, cuarentena ni análisis de
contenido especializado cuando el riesgo institucional los requiera.

Una historia finalizada no admite nuevos adjuntos directos, reemplazos ni borrado físico.
La verificación de metadatos no descarga archivos; la opción `--verificar-adjuntos` sí lee
cada objeto de Storage y debe ejecutarse en una ventana operativa acorde al volumen.

Para adjuntos legacy:

```powershell
python manage.py completar_hashes_adjuntos_legacy --dry-run
python manage.py completar_hashes_adjuntos_legacy --fallar-si-hay-errores
```

## Migración legacy

Las migraciones `0005`, `0006` y `0007` agregan el esquema, convierten registros previos
y agregan protección PostgreSQL. La migración de datos:

- no altera contenido clínico ni descarga Storage;
- ordena por paciente, fecha, creación y PK;
- asigna folios correlativos;
- bloquea y marca cada asiento como legacy;
- usa `actualizado_por` o `creado_por` solo si ese dato ya existía;
- permite finalizador nulo cuando no hay autor verificable;
- conserva fechas y adjuntos.

Después de migrar, el procedimiento obligatorio es:

```powershell
python manage.py completar_hashes_adjuntos_legacy --dry-run
python manage.py completar_hashes_adjuntos_legacy --fallar-si-hay-errores
python manage.py inicializar_integridad_historias_legacy --dry-run
python manage.py inicializar_integridad_historias_legacy --fallar-si-hay-errores
python manage.py verificar_integridad_historias --verificar-adjuntos --fallar-si-hay-errores
```

El despliegue inicial debe realizarse en una ventana de mantenimiento, sin nuevas
escrituras clínicas entre `0005`, `0006`, el backfill y la verificación final. No se debe
habilitar nuevamente el flujo hasta revisar cualquier registro pendiente o error.

Una historia sin usuario histórico verificable queda pendiente y requiere revisión
institucional; el comando no inventa autores. La primera versión legacy declara que no
existe trazabilidad previa y su sello solo aporta evidencia desde la inicialización.

## Protecciones por capa

- Modelos y querysets rechazan actualización o borrado de objetos inmutables.
- Relaciones críticas usan `PROTECT`.
- Admin es de consulta y no ofrece alta, cambio, borrado ni acciones masivas clínicas.
- Vistas aplican alcance por objeto; conocer un ID no concede acceso.
- Servicios usan `transaction.atomic()` y `select_for_update()`.
- Constraints evitan estados incoherentes y numeraciones duplicadas.
- PostgreSQL instala triggers reversibles que bloquean UPDATE/DELETE de versiones y
  enmiendas, DELETE de historias y adjuntos y UPDATE de asientos ya bloqueados.
- SQLite conserva validaciones de aplicación y constraints, pero los triggers
  PostgreSQL son un no-op deliberado. Se usa para desarrollo y tests, no como sustituto
  de PostgreSQL en producción.

Un superusuario de base puede desactivar triggers. Los controles técnicos deben
acompañarse con cuentas separadas, privilegios mínimos y auditoría de infraestructura.

## Permisos y auditoría

Se conserva la política clínica existente:

- odontólogos autorizados acceden según asociación y responsabilidad;
- recepción y administración no reciben acceso clínico por su rol operativo;
- objetos fuera de alcance responden 404 y acciones conocidas no permitidas, 403;
- el acceso de emergencia sigue limitado a superusuarios, por paciente, con motivo,
  vencimiento y auditoría;
- la lectura compartida continúa detrás de su configuración explícita.

Se auditan borradores, versiones, finalización, visualización de historias,
versiones/enmiendas, consultas autorizadas desde Django Admin, enmiendas, verificación,
exportación e intentos de editar registros finalizados. Los mensajes son neutros y no
contienen diagnósticos, tratamientos, observaciones, DNI, email, teléfono, snapshots ni
secretos.

## Exportación y entrega de copia

Un odontólogo autorizado puede generar un ZIP interno con motivo obligatorio. Incluye:

```text
manifest.json
historia_clinica.html
versiones/
enmiendas/
adjuntos/
```

El manifiesto usa `schema_version=2` y contiene asientos ordenados, autores, timestamps,
folios, sellos y SHA-256. Cada adjunto informa nombre visible, nombre exportado, ruta
relativa, tipo, tamaño, disponibilidad de vista previa inline y, cuando corresponde, el
motivo `tipo_no_compatible`, `tipo_no_coincidente` o `supera_limite`. El Base64 nunca se
incluye en `manifest.json`.

JPEG, PNG y WebP pueden mostrarse dentro del HTML offline mediante Data URI. Para hacerlo
deben coincidir el MIME registrado, la extensión y la firma binaria. GIF no se admite en
las validaciones actuales; SVG, PDF, DICOM, formatos desconocidos y cualquier discordancia
se presentan únicamente como archivo enlazado. La vista previa usa exactamente los bytes
del original y su mismo SHA-256; no redimensiona, recomprime ni modifica el documento.

El límite `CLINICAL_EXPORT_INLINE_IMAGE_MAX_BYTES` es una constante interna de 5 MB por
imagen. Si se supera, el original permanece en `adjuntos/` y el HTML muestra el fallback
de tamaño. Cada objeto de Storage se abre una sola vez: durante esa lectura se escribe en
el ZIP, se calcula SHA-256 y, solo para una candidata inline, se conserva como máximo el
límite necesario para Base64.

Las rutas usan nombres ASCII acotados y únicos por ID, sin confiar en el nombre aportado
por el usuario. El HTML escapa textos, no contiene JavaScript ni recursos remotos y aplica
una CSP offline con `default-src 'none'`, `img-src data:`, estilos inline y scripts,
objetos, frames y conexiones deshabilitados.

El ZIP usa un archivo temporal con rollover a disco y se entrega como respuesta streaming.
Se conserva la política estricta existente: un adjunto ausente, ilegible o cuyo SHA-256 no
coincide cancela la exportación completa y registra un resultado de error neutro. No se
entrega una copia clínica silenciosamente incompleta ni se exponen rutas privadas, URLs
firmadas o secretos.

El ZIP no constituye por sí solo una copia autenticada. La institución debe verificar la
legitimación del solicitante, registrar solicitud, urgencia, responsable y entrega,
aplicar el procedimiento de autenticación que corresponda y respetar los plazos legales.
No existe acceso público directo a la historia completa.

## Verificación

```powershell
python manage.py verificar_integridad_historias
python manage.py verificar_integridad_historias --paciente 10
python manage.py verificar_integridad_historias --historia 25 --verificar-adjuntos
python manage.py verificar_integridad_historias --fallar-si-hay-errores
```

La salida muestra solo IDs internos, cantidades y códigos de inconsistencia. Verifica
estado, folios, secuencias, encadenamiento, sellos, snapshot vigente, enmiendas y hashes
registrados. No imprime contenido clínico.

Ante cualquier inconsistencia, seguir
[`docs/runbooks/incidente_integridad_clinica.md`](runbooks/incidente_integridad_clinica.md).

## Límites pendientes

- La solución no implementa firma digital ni certificados de la Ley 25.506.
- La autenticación institucional de copias sigue siendo un procedimiento humano.
- La rotación transparente de múltiples claves requiere una evolución futura.
- La retención y disposición final deben definirse con asesoramiento aplicable a la
  jurisdicción y al consultorio.
- Backups, restauraciones, monitoreo, capacitación, respuesta a incidentes y separación
  de funciones siguen siendo controles organizativos indispensables.
