# Indicaciones postoperatorias

El módulo `indicaciones` permite que un odontólogo prepare, revise, emita y entregue un
documento de cuidados postoperatorios a un paciente dentro de su alcance clínico. Está
desactivado por defecto y no altera historias clínicas existentes.

Este módulo no es una receta electrónica, una prescripción ni una firma digital
certificada. El sistema no genera contenido médico, diagnósticos ni medicamentos. Las
plantillas y cada personalización deben ser redactadas y aprobadas por profesionales del
consultorio.

## Activación

Aplicar primero las migraciones y validar el entorno. Después configurar:

```env
INDICACIONES_POSTOPERATORIAS_ENABLED=True
INDICACIONES_PDF_MAX_BYTES=5242880
PRIVATE_CLINICAL_STORAGE_BACKEND=config.storage_backends.SupabaseStorage
```

En local, `PRIVATE_CLINICAL_STORAGE_BACKEND` usa por defecto
`config.storage_backends.PrivateClinicalFileSystemStorage` y guarda fuera de `MEDIA_ROOT`,
en `app/private_media/`. En Railway con Supabase debe apuntar al backend privado y el
bucket configurado no debe ser público.

La emisión reutiliza `CLINICAL_INTEGRITY_HMAC_KEY`. La clave debe ser estable,
independiente de otros secretos y estar disponible antes de iniciar Django.

## Permisos

- Solo un usuario autenticado con perfil de odontólogo activo puede escribir.
- El paciente debe estar activo y tener una asociación clínica vigente con ese profesional.
- El odontólogo se deriva del usuario autenticado; nunca se acepta desde el formulario.
- Recepción no puede listar, leer, descargar, emitir, reenviar ni anular indicaciones.
- Un objeto fuera del alcance responde `404`; una transición no permitida responde `403`.
- La lectura compartida o de emergencia sigue la política clínica existente y no concede
  silenciosamente permisos de escritura.

## Plantillas versionadas

`PlantillaIndicacion` contiene un punto de partida aprobado. Al modificarla, el servicio
crea una fila append-only en `PlantillaIndicacionVersion`, exige un motivo e incrementa la
versión. Un borrador copia el contenido y conserva `plantilla_version`; cambios posteriores
de la plantilla no actualizan borradores ni documentos emitidos.

No hay datos médicos precargados por migración. El admin solo está disponible para
odontólogos activos, no permite borrado y exige un motivo para cada modificación.

## Estados e inmutabilidad

El flujo usa tres estados:

1. `BORRADOR`: editable únicamente por el odontólogo responsable, sin PDF ni snapshots.
2. `EMITIDA`: contiene snapshots, PDF, SHA-256 y sello HMAC; su contenido es inmutable.
3. `ANULADA`: conserva íntegros el PDF y los snapshots, agrega autor, fecha y motivo de
   anulación, y no puede reenviarse como vigente.

Una corrección requiere anular el original y crear un nuevo borrador con `reemplaza_a`.
No se sobrescribe ni se elimina el documento anterior. El modelo, los servicios, el
QuerySet, el admin y triggers PostgreSQL aplican estas reglas.

## Emisión y concurrencia

`emitir_indicacion()` ejecuta `transaction.atomic()` y `select_for_update()` sobre el
borrador. Dentro de la transacción vuelve a validar identidad, permisos y estado; luego:

1. captura snapshots del paciente, profesional, consultorio y documento;
2. genera un PDF A4 con ReportLab;
3. calcula el SHA-256 de los bytes finales;
4. calcula un HMAC canónico sobre snapshots y hash;
5. guarda el PDF en `indicaciones/<uuid>/documento.pdf`;
6. cambia el estado a `EMITIDA` y registra auditoría;
7. programa el email con `transaction.on_commit()`.

Un segundo POST sobre el mismo documento devuelve la emisión ya existente y no genera
otro archivo ni programa otro correo inicial.

El PDF contiene identidad del consultorio, paciente, profesional, contenido revisado,
contacto, referencia técnica, aviso de urgencia y la aclaración obligatoria de que no es
una receta electrónica. No usa recursos remotos, QR público ni firma escaneada.

## Storage y descarga

La ruta física no contiene DNI, nombre, email ni diagnóstico. Las plantillas nunca usan
`pdf.url`; la descarga pasa por una vista autenticada que vuelve a comprobar el alcance,
registra auditoría y responde con `FileResponse`, `Cache-Control: private, no-store` y un
nombre sin datos personales.

El backend local rechaza `.url()`. Supabase puede crear URLs firmadas para otros módulos,
pero las indicaciones se entregan exclusivamente a través de la vista protegida.

## Email y reintentos

Solo se captura como destino un `Paciente.email` persistido cuando el paciente está activo
y `email_verificado_en` no es nulo. Un email propuesto en una solicitud pública no se usa.
El cuerpo es neutro y el contenido clínico completo queda únicamente en el PDF adjunto.

El backend HTTP de Resend acepta adjuntos binarios de Django, valida nombre, MIME y tamaño,
y convierte el PDF a Base64 solo en memoria para construir el request. No guarda Base64 ni
lo registra. Los correos simples existentes conservan el mismo payload.

Si el proveedor falla, la transacción clínica no se revierte: el documento queda emitido,
el estado de email pasa a `ERROR`, se incrementan intentos y se conserva un mensaje neutral.
El odontólogo puede reenviar al destino capturado. Usar un email actual diferente requiere
una elección explícita y ese email también debe estar verificado.
Si el documento se emitió sin destinatario y el paciente verifica un email posteriormente,
el reenvío se habilita, pero exige confirmar expresamente el uso de ese email actual.

Reintento operativo:

```powershell
python manage.py reenviar_indicaciones_pendientes --dry-run
python manage.py reenviar_indicaciones_pendientes --limite 50 --max-intentos 5
python manage.py reenviar_indicaciones_pendientes --fallar-si-hay-errores
```

En PostgreSQL, el selector usa `select_for_update(skip_locked=True)`. El comando no imprime
destinatarios, contenido clínico, PDF ni errores del proveedor.

## Auditoría e integridad

La app reutiliza `AccesoClinicoAuditoria` para creación, edición, lectura, emisión, PDF,
email, reenvío, anulación, reemplazo e intentos denegados. Los eventos contienen IDs y
mensajes neutros, nunca contenido, pautas, observaciones, Base64 o secretos.

El sello HMAC es un control técnico de integridad, no una firma digital. Verifica que los
snapshots y el SHA-256 no cambiaron usando la clave clínica configurada.

## Backup y restauración

Un backup recuperable debe incluir en el mismo punto temporal:

- PostgreSQL, incluidas tablas `indicaciones_*` y auditoría;
- objetos privados bajo `indicaciones/`;
- la clave HMAC clínica custodiada fuera del backup de datos.

`backup_storage_historias` respalda adjuntos referenciados por historias; no debe suponerse
que incluye automáticamente los PDF de indicaciones. Hasta disponer de un comando unificado,
respaldar el prefijo `indicaciones/` con las herramientas privadas del proveedor y ensayar
la restauración en un entorno aislado. Nunca publicar el bucket para facilitar el backup.

## Prueba manual

1. Crear una plantilla ficticia desde el admin con un odontólogo activo.
2. Abrir un paciente activo asociado y crear un borrador.
3. Personalizar, guardar, editar y revisar el contenido.
4. Emitir y comprobar estado, snapshots, hash, sello y PDF.
5. Confirmar el correo con backend de consola o memoria; probar Resend solo en staging.
6. Simular un fallo del proveedor y comprobar que la emisión permanece.
7. Reenviar, anular y crear un reemplazo.
8. Verificar `404` fuera de alcance y para recepción.
9. Revisar móvil y escritorio sin desborde horizontal.
10. Confirmar que el bucket sigue privado y que el original anulado permanece intacto.

## Limitaciones

- No existe keyring para rotar automáticamente la clave HMAC histórica.
- No se envía email de anulación en esta versión.
- No hay QR público ni portal público de indicaciones.
- La programación periódica del comando de reintentos debe revisarse antes de habilitarse.
- El backup del prefijo `indicaciones/` requiere hoy una operación del proveedor separada.
