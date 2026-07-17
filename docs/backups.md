# Backups completos

Esta guia cubre el backup minimo para staging y primeras pruebas controladas:

- PostgreSQL: datos del sistema, pacientes, turnos, historias clinicas y referencias a adjuntos.
- Supabase Storage: archivos reales de historia clinica, por ejemplo radiografias, PDF o DICOM.

Los backups pueden contener datos sensibles. Deben guardarse fuera del repositorio y con acceso restringido.

## PDF de indicaciones postoperatorias

Los documentos emitidos agregan objetos privados bajo `indicaciones/<uuid>/documento.pdf`
y filas en las tablas `indicaciones_*`. Una restauración consistente requiere el dump de
PostgreSQL y esos objetos del mismo punto temporal. La clave
`CLINICAL_INTEGRITY_HMAC_KEY` se custodia por separado y debe corresponder al período de los
sellos restaurados.

El comando `backup_storage_historias` enumera adjuntos de historia clínica y no debe
considerarse un backup automático del prefijo `indicaciones/`. Mientras no exista un
comando unificado, exportar ese prefijo con herramientas privadas del proveedor, registrar
hashes y ensayar la restauración en un bucket aislado. No volver público el bucket ni copiar
PDF a `MEDIA_ROOT` para simplificar el procedimiento.

La historia versionada requiere conservar como un conjunto coherente:

- PostgreSQL, incluidos versiones, enmiendas, auditoría, folios, hashes y migraciones;
- Storage privado con las mismas rutas de los adjuntos;
- logs operativos y de infraestructura según la política de retención;
- la clave `CLINICAL_INTEGRITY_HMAC_KEY` mediante custodia separada de la base.

La clave HMAC no debe incluirse dentro del dump, el backup de Storage ni `manifest.json`.
Sin la clave correspondiente, los datos se pueden recuperar pero los sellos históricos no
se pueden verificar.

## Carpeta local

El repositorio ignora la carpeta:

```text
backups/
```

Usala para pruebas locales. Para uso real, copiar tambien a un lugar externo seguro.

## Backup de PostgreSQL

En Windows, con Docker Desktop iniciado:

```powershell
.\scripts\backup_postgresql_docker.ps1
```

El script lee `DATABASE_URL` desde el entorno o desde `.env` y crea un archivo `.dump` en `backups/`.

Para probar restauracion:

```powershell
.\scripts\probar_restore_postgresql_docker.ps1
```

Esta prueba levanta una base PostgreSQL temporal, restaura el ultimo `.dump` y consulta tablas clave.

## Backup de Supabase Storage

Los adjuntos clinicos se respaldan con un comando de Django:

```powershell
cd app
python manage.py backup_storage_historias
```

Tambien se puede usar el wrapper de Windows:

```powershell
.\scripts\backup_storage_historias.ps1
```

El comando crea una carpeta similar a:

```text
backups/storage/historias-storage-20260510T120000Z/
```

Dentro guarda:

- `archivos/`: copia local de los adjuntos.
- `manifest.json`: indice de verificacion con ids, rutas, tamanos y `sha256`.

El manifest no guarda nombres de pacientes. Guarda ids internos para poder cruzar con la base restaurada.

## Prueba sin descargar archivos

Para verificar configuracion sin descargar adjuntos:

```powershell
cd app
python manage.py backup_storage_historias --dry-run
```

Con wrapper:

```powershell
.\scripts\backup_storage_historias.ps1 -DryRun
```

## Restauracion completa

La restauracion completa requiere dos pasos:

1. Restaurar PostgreSQL desde el `.dump`.
2. Volver a subir a Storage los archivos respaldados manteniendo las rutas de `ruta_storage` indicadas en `manifest.json`.

Todavia no automatizamos la subida de restauracion porque conviene hacerla primero de forma controlada en un proyecto Supabase de prueba.

Después de restaurar en el entorno aislado:

```powershell
python manage.py showmigrations historias
python manage.py verificar_integridad_historias --fallar-si-hay-errores
python manage.py verificar_integridad_historias --verificar-adjuntos --fallar-si-hay-errores
```

La prueba debe usar la clave clínica correspondiente al momento del backup. No sustituirla
por una clave nueva ni regenerar versiones para forzar un resultado válido. Comparar
conteos de pacientes, asientos, versiones, enmiendas y adjuntos y verificar una muestra de
ZIP en un canal autorizado.

La política real debe definir RPO, RTO, frecuencia, retención, cifrado, acceso, copia
externa y responsable de cada restauración. Probar periódicamente la recuperación completa
de base más Storage; un dump que nunca se restauró no es evidencia suficiente.

## Checklist

- Crear backup PostgreSQL.
- Probar restauracion PostgreSQL en base temporal.
- Crear backup Storage.
- Verificar que `manifest.json` existe.
- Abrir un archivo de prueba desde `archivos/`.
- Guardar una copia fuera de Supabase y fuera del hosting de la app.
- Documentar quien puede acceder al backup.
- Conservar la clave HMAC en custodia separada y probar que puede recuperarse sin exponerla.
- Ejecutar el verificador de integridad después de cada simulacro de restauración.
- Registrar fecha, responsable y resultado del simulacro.

Antes de produccion real, la restauracion base + storage debe probarse en un entorno separado.
