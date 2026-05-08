# Archivos estaticos

Esta guia deja preparado el manejo de archivos estaticos para produccion.

## Estrategia elegida

El proyecto usa WhiteNoise para servir archivos estaticos desde Django en despliegues simples como Render o Railway.

La configuracion principal esta en `app/config/settings.py`:

```python
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    ...
]

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
```

WhiteNoise comprime y versiona los archivos generados por `collectstatic`.

## Ejecutar collectstatic

Desde la carpeta `app/`:

```powershell
python manage.py collectstatic --noinput
```

El comando copia los archivos estaticos a:

```text
app/staticfiles/
```

Esa carpeta esta ignorada por Git porque es salida generada.

## Render

Build command sugerido:

```bash
pip install -r requirements.txt && cd app && python manage.py collectstatic --noinput && python manage.py migrate
```

Start command sugerido:

```bash
cd app && gunicorn config.wsgi:application
```

Todavia falta agregar `gunicorn` cuando se avance con el deploy real.

## Railway

Build command sugerido:

```bash
pip install -r requirements.txt && cd app && python manage.py collectstatic --noinput
```

Start command sugerido:

```bash
cd app && gunicorn config.wsgi:application
```

Tambien falta agregar `gunicorn` cuando se cierre el bloque de deploy.

## Recomendaciones

- Ejecutar `collectstatic` en cada deploy.
- No versionar `staticfiles/`.
- Mantener WhiteNoise para una primera version simple.
- Evaluar CDN o almacenamiento externo si el proyecto crece mucho o maneja muchos archivos subidos.
