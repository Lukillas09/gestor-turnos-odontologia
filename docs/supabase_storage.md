# Supabase Storage para adjuntos clinicos

Esta guia deja preparado el almacenamiento externo de radiografias, imagenes, PDF y archivos DICOM de historia clinica.

## Objetivo

En desarrollo local se pueden guardar adjuntos en `app/media/`. En staging o produccion no conviene depender del disco del hosting porque los archivos pueden perderse en reinicios o redeploys. Para evitarlo, los adjuntos clinicos deben guardarse en Supabase Storage.

## Crear bucket privado

1. Entrar al proyecto en Supabase.
2. Abrir `Storage`.
3. Crear un bucket llamado:

```text
historias-clinicas
```

4. Dejar el bucket como privado.
5. Configurar, si Supabase lo permite desde el panel, limite de archivo cercano a `10 MB`.
6. Permitir tipos de archivo usados por el consultorio:

```text
image/png
image/jpeg
image/webp
image/tiff
application/pdf
application/dicom
```

El sistema tambien valida extensiones seguras desde Django: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.tif`, `.tiff`, `.bmp`, `.dcm`.

## Variables de entorno

En Railway o el entorno donde se use Supabase Storage:

```env
MEDIA_STORAGE_BACKEND=config.storage_backends.SupabaseStorage
SUPABASE_STORAGE_URL=https://tu-proyecto.supabase.co
SUPABASE_STORAGE_BUCKET=historias-clinicas
SUPABASE_STORAGE_SERVICE_ROLE_KEY=service-role-key-del-proyecto
SUPABASE_STORAGE_TIMEOUT=30
SUPABASE_STORAGE_CACHE_CONTROL=3600
SUPABASE_STORAGE_SIGNED_URL_SECONDS=300
```

`SUPABASE_STORAGE_SERVICE_ROLE_KEY` es sensible. No se debe exponer en frontend, commits, screenshots ni logs.

Para desarrollo local sin Supabase:

```env
MEDIA_STORAGE_BACKEND=django.core.files.storage.FileSystemStorage
```

## Probar storage

Despues de configurar variables:

```bash
cd app
python manage.py probar_storage_historias
```

El comando guarda un archivo de prueba, lo lee y lo borra. Si se quiere dejar el archivo para revisar el bucket:

```bash
python manage.py probar_storage_historias --conservar
```

## Probar desde la web

1. Entrar con un usuario odontologo.
2. Abrir un paciente.
3. Entrar a `Historia clinica`.
4. Crear o editar una entrada.
5. Adjuntar una radiografia o PDF de prueba.
6. Guardar.
7. Entrar al detalle de la entrada.
8. Abrir el adjunto desde el boton `Abrir`.

La descarga pasa por Django, por eso el bucket puede mantenerse privado.

## Límites y costo

Los límites del plan gratuito de Supabase pueden cambiar. Antes de usar datos reales de pacientes, revisar el panel de Supabase y la documentación oficial del plan vigente.

Recomendación práctica para un consultorio chico:

- Usar Supabase Storage primero en staging o pruebas controladas.
- Subir archivos comprimidos cuando sea posible.
- Evitar radiografías muy pesadas si no son necesarias.
- Revisar uso de Storage periódicamente durante pruebas con el cliente.
- Definir una política de backup antes de cargar documentación clínica real.

## Backups

Los backups de PostgreSQL no incluyen automaticamente los archivos de Storage. Hay que considerar dos cosas:

- Base de datos: guarda la referencia del adjunto.
- Storage: guarda el archivo real.

Estrategia minima:

1. Mantener backup logico de PostgreSQL con los scripts del repo.
2. Descargar periodicamente los archivos del bucket privado con `python manage.py backup_storage_historias`.
3. Probar restauracion completa: base + archivos.
4. Documentar quien puede acceder a esos backups.

Comandos utiles:

```powershell
cd app
python manage.py backup_storage_historias --dry-run
python manage.py backup_storage_historias
```

En Windows tambien se puede ejecutar:

```powershell
.\scripts\backup_storage_historias.ps1
```

El backup genera una carpeta en `backups/storage/` con los archivos y un `manifest.json` con `sha256`, rutas e ids internos.

La guia completa esta en [backups.md](backups.md).

Antes de usar datos reales de pacientes, esta estrategia debe quedar probada.
