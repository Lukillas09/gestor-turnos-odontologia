# Email real por API HTTP

Render Free bloquea conexiones salientes por puertos SMTP comunes como `25`, `465` y `587`.

Para que los emails lleguen a pacientes desde staging o produccion, el proyecto incluye un backend propio de Django que envia por API HTTP:

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
```

## Proveedores soportados

Version inicial:

- Resend
- Brevo

La configuracion se elige por variable de entorno:

```env
EMAIL_API_PROVIDER=resend
```

o:

```env
EMAIL_API_PROVIDER=brevo
```

## Resend

Variables:

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=re_...
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
```

El remitente debe estar permitido por Resend. Para enviar a pacientes reales conviene verificar un dominio propio.

## Brevo

Variables:

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=brevo
EMAIL_API_KEY=xkeysib-...
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
```

El remitente debe estar validado en Brevo.

## Render

En Render cargar:

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=clave-real-del-proveedor
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
```

No subir `EMAIL_API_KEY` al repositorio.

## GitHub Actions

Para recordatorios programados, cargar estos secrets:

```text
STAGING_EMAIL_BACKEND
STAGING_EMAIL_API_PROVIDER
STAGING_EMAIL_API_KEY
STAGING_DEFAULT_FROM_EMAIL
```

Valores recomendados:

```text
STAGING_EMAIL_BACKEND=config.email_backends.EmailApiBackend
STAGING_EMAIL_API_PROVIDER=resend
```

## Prueba

Probar un email simple:

```powershell
python manage.py probar_email tu-email@example.com
```

Probar las notificaciones reales del dominio:

```powershell
python manage.py probar_notificaciones_email tu-email@example.com
```

Si el proveedor responde con error, el comando falla y muestra el problema sin imprimir la API key.

## Desarrollo local

Para desarrollo se puede seguir usando consola:

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

O SMTP local/real:

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
```

El backend por API HTTP queda pensado especialmente para staging y produccion en proveedores donde SMTP saliente no esta disponible.
