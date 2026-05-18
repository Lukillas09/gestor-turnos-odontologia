# Archivos estaticos

Esta guia deja preparado el manejo de archivos estaticos para produccion.

## Estrategia elegida

El proyecto usa WhiteNoise para servir archivos estaticos desde Django en Railway.

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

## Railway

Build command sugerido:

```bash
bash scripts/build.sh
```

Start command sugerido:

```bash
bash scripts/start.sh
```

Las migraciones pueden ejecutarse con `bash scripts/release.sh` antes de abrir el servicio a usuarios reales.

## Recomendaciones

- Ejecutar `collectstatic` en cada deploy.
- No versionar `staticfiles/`.
- Mantener WhiteNoise para una primera version simple.
- Evaluar CDN o almacenamiento externo si el proyecto crece mucho o maneja muchos archivos subidos.
