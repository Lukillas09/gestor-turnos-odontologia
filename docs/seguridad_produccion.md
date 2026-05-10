# Seguridad antes de produccion

Esta guia marca el trabajo de endurecimiento que debe hacerse despues de tener staging funcionando.

No reemplaza una auditoria formal. Sirve como checklist tecnico para no avanzar a produccion con decisiones flojas.

## 1. Tokens OAuth

Estado actual:

- Los tokens OAuth de Google Calendar se guardan en `GoogleCalendarConexion`.
- El admin ya no muestra el valor real de `access_token` ni `refresh_token`.
- Las pantallas internas muestran estado de conexion, no secretos.
- Los errores tecnicos de Google Calendar se normalizan antes de mostrarse al usuario.

Antes de produccion real:

- Evaluar cifrado de tokens en base de datos.
- Rotar credenciales OAuth si alguna vez se compartieron por error.
- Mantener el cliente OAuth en Google Cloud con redirect URIs exactos.
- Revisar accesos del admin y usuarios con permiso `is_staff`.

## 2. Backups

Para staging con Supabase:

- Verificar en el panel de Supabase que la base tenga backups disponibles.
- Mantener backups logicos fuera del proveedor para no depender de un unico punto.
- Guardar backups fuera del repositorio y con acceso restringido.

El repositorio incluye un script base para backups PostgreSQL:

```bash
DATABASE_URL="postgres://usuario:password@host:5432/postgres?sslmode=require" bash scripts/backup_postgresql.sh
```

Ese script usa `pg_dump`, por lo que el equipo o runner donde se ejecute debe tener instalado el cliente de PostgreSQL.

En Windows se puede usar Docker sin instalar PostgreSQL local:

```powershell
.\scripts\backup_postgresql_docker.ps1
```

Ese script toma `DATABASE_URL` desde la variable de entorno o desde `.env`, crea un backup logico del esquema `public` y deja el archivo en `backups/`.

Los archivos quedan en:

```text
backups/
```

Esa carpeta esta ignorada por Git porque puede contener datos sensibles de pacientes.

Antes de produccion:

- Definir frecuencia de backup.
- Probar restauracion, no solo creacion.
- Guardar una copia fuera del proveedor principal.
- Documentar quien puede acceder a esos backups.
- Respaldar tambien Supabase Storage, porque PostgreSQL solo guarda referencias a adjuntos.

Para probar restauracion en una base PostgreSQL temporal con Docker:

```powershell
.\scripts\probar_restore_postgresql_docker.ps1
```

La prueba levanta un contenedor temporal, restaura el ultimo backup y consulta tablas clave. Al terminar elimina el contenedor.

Para adjuntos clinicos en Supabase Storage:

```powershell
.\scripts\backup_storage_historias.ps1 -DryRun
.\scripts\backup_storage_historias.ps1
```

El backup queda en `backups/storage/` e incluye `manifest.json` con ids internos, rutas y `sha256`.

La guia completa esta en `docs/backups.md`.

## 3. Logs

El proyecto ya define logging por consola con:

```env
DJANGO_LOG_LEVEL=INFO
```

Para staging y produccion simple conviene mantener:

```env
DJANGO_LOG_LEVEL=INFO
```

Para investigar errores puntuales se puede subir temporalmente a:

```env
DJANGO_LOG_LEVEL=DEBUG
```

Reglas:

- No registrar tokens OAuth.
- No registrar passwords SMTP.
- No registrar contenido clinico sensible.
- Revisar logs de errores de Google Calendar y email sin exponer secretos.

## 4. Dominio real

Staging puede usar `tu-app.onrender.com`.

Produccion deberia usar un dominio propio, por ejemplo:

```text
turnos.tuconsultorio.com
```

Cuando exista dominio real, actualizar:

```env
DJANGO_ALLOWED_HOSTS=turnos.tuconsultorio.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://turnos.tuconsultorio.com
GOOGLE_CALENDAR_REDIRECT_URI=https://turnos.tuconsultorio.com/turnos/google-calendar/callback/
```

Tambien agregar ese redirect URI en Google Cloud.

## 5. HTTPS final

Para staging:

```env
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
DJANGO_SECURE_HSTS_SECONDS=0
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=False
DJANGO_SECURE_HSTS_PRELOAD=False
DJANGO_SECURE_PROXY_SSL_HEADER=True
```

Para produccion final, despues de confirmar que HTTPS funciona perfecto en el dominio real:

```env
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
DJANGO_SECURE_HSTS_SECONDS=31536000
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=True
DJANGO_SECURE_HSTS_PRELOAD=False
DJANGO_SECURE_PROXY_SSL_HEADER=True
```

No activar `DJANGO_SECURE_HSTS_PRELOAD=True` hasta estar seguro de que todos los subdominios funcionan siempre por HTTPS.

## Checklist de salida a produccion

- Staging probado de punta a punta.
- PostgreSQL funcionando.
- Backups creados y restaurados en prueba.
- Emails reales resueltos con proveedor compatible.
- Dominio real configurado.
- Google OAuth actualizado con redirect URI real.
- HTTPS activo.
- `DEBUG=False`.
- `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` sin comodines inseguros.
- Tokens OAuth no visibles en admin ni templates.
- Logs sin secretos ni datos clinicos sensibles.
