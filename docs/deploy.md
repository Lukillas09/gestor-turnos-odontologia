# Deploy

Esta guia deja preparados los comandos base para desplegar el proyecto en Render o Railway.

Todavia no se hace el deploy real. El objetivo de este bloque es que el repositorio ya tenga los scripts necesarios para construir y arrancar la aplicacion en un entorno Linux.

El primer entorno elegido para staging esta documentado en [staging.md](staging.md).

## Dependencias

El servidor de produccion configurado es Gunicorn:

```text
gunicorn==25.3.0
```

Gunicorn se usa para ejecutar la aplicacion WSGI de Django:

```bash
python -m gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers $WEB_CONCURRENCY
```

En Windows local se puede seguir usando:

```powershell
python manage.py runserver
```

## Scripts

Los scripts viven en:

```text
scripts/
```

### Build

```bash
bash scripts/build.sh
```

Hace:

- Instala dependencias.
- Ejecuta `collectstatic`.

### Release

```bash
bash scripts/release.sh
```

Hace:

- Ejecuta migraciones con `python manage.py migrate --noinput`.

### Start

```bash
bash scripts/start.sh
```

Hace:

- Arranca Gunicorn apuntando a `config.wsgi:application`.
- Usa la variable `PORT` del proveedor.
- Si `PORT` no existe, usa `8000`.
- Usa `WEB_CONCURRENCY` para definir workers. Si no existe, usa `2`.

## Render

Comandos sugeridos para un Web Service de Render:

```text
Build Command: bash scripts/build.sh
Start Command: bash scripts/start.sh
```

Si el plan permite Pre-Deploy Command:

```text
Pre-Deploy Command: bash scripts/release.sh
```

Si no se usa Pre-Deploy Command, ejecutar migraciones manualmente desde la shell de Render antes de abrir el sitio a usuarios reales:

```bash
cd app && python manage.py migrate --noinput
```

Variables minimas:

```env
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=clave-segura
DJANGO_ALLOWED_HOSTS=tu-servicio.onrender.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://tu-servicio.onrender.com
DATABASE_URL=postgres://...
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=...
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
WEB_CONCURRENCY=2
```

El repositorio tambien incluye `render.yaml` para crear el Web Service desde Blueprint. En el plan gratuito, si no hay comando de pre deploy disponible, las migraciones deben correrse manualmente con `bash scripts/release.sh` o `cd app && python manage.py migrate --noinput`.

## Railway

Railway permite configurar el start command desde el dashboard o con config-as-code.

Comandos sugeridos:

```text
Build Command: bash scripts/build.sh
Start Command: bash scripts/start.sh
```

Para migraciones:

```bash
bash scripts/release.sh
```

Ese comando puede ejecutarse manualmente con Railway CLI o como tarea separada antes de publicar cambios importantes.

Variables minimas:

```env
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=clave-segura
DJANGO_ALLOWED_HOSTS=tu-dominio.up.railway.app
DJANGO_CSRF_TRUSTED_ORIGINS=https://tu-dominio.up.railway.app
DATABASE_URL=postgres://...
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=...
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
WEB_CONCURRENCY=2
```

## Procfile

El repositorio incluye un `Procfile` simple:

```text
web: bash scripts/start.sh
release: bash scripts/release.sh
```

Esto sirve como referencia para proveedores que lean Procfile y tambien documenta los procesos principales.

## Checklist antes del primer deploy

- Confirmar que `python manage.py check --deploy` no tenga issues criticos.
- Cargar variables de entorno reales en el proveedor.
- Configurar PostgreSQL.
- Ejecutar migraciones.
- Ejecutar `collectstatic` durante el build.
- Probar login, admin, turnos, emails y Google Calendar en staging.

## Referencias

- Render Django: https://render.com/docs/deploy-django
- Render deploys: https://render.com/docs/deploys
- Railway Django: https://docs.railway.com/guides/django
- Railway config-as-code: https://docs.railway.com/config-as-code/reference
- Gunicorn: https://gunicorn.org/
