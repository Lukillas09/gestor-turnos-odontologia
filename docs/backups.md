# Backups completos

Esta guia cubre el backup minimo para staging y primeras pruebas controladas:

- PostgreSQL: datos del sistema, pacientes, turnos, historias clinicas y referencias a adjuntos.
- Supabase Storage: archivos reales de historia clinica, por ejemplo radiografias, PDF o DICOM.

Los backups pueden contener datos sensibles. Deben guardarse fuera del repositorio y con acceso restringido.

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

## Checklist

- Crear backup PostgreSQL.
- Probar restauracion PostgreSQL en base temporal.
- Crear backup Storage.
- Verificar que `manifest.json` existe.
- Abrir un archivo de prueba desde `archivos/`.
- Guardar una copia fuera de Supabase y fuera del hosting de la app.
- Documentar quien puede acceder al backup.

Antes de produccion real, la restauracion base + storage debe probarse en un entorno separado.
