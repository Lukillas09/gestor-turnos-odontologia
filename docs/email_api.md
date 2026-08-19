# Email real por API HTTP

Para que los emails lleguen a pacientes desde staging o produccion sin depender de SMTP, el proyecto incluye un backend propio de Django que envia por API HTTP:

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

Para una prueba inicial sin dominio propio, Resend permite usar su remitente de sandbox:

```env
DEFAULT_FROM_EMAIL=Consultorio <onboarding@resend.dev>
```

Este modo sirve para validar la integracion, pero no reemplaza un dominio verificado para uso real del consultorio.

## Brevo

Variables:

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=brevo
EMAIL_API_KEY=xkeysib-...
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
```

El remitente debe estar validado en Brevo.

## Railway y deploy

En Railway cargar:

```env
EMAIL_BACKEND=config.email_backends.EmailApiBackend
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=clave-real-del-proveedor
DEFAULT_FROM_EMAIL=Consultorio <turnos@tu-dominio.com>
```

No subir `EMAIL_API_KEY` al repositorio.

## Adjuntos de indicaciones

Los clientes de Resend y Brevo soportan adjuntos de `EmailMessage` sin cambiar el payload de
correos simples. Para cada adjunto validan nombre, MIME, contenido binario y tamaño. Resend usa
`attachments` con `filename`, `content` y `content_type`; Brevo usa `attachment` con `name` y
`content`, según su contrato HTTP. El Base64 se genera sólo en memoria y no se guarda ni se
registra en logs.

Las indicaciones usan `Idempotency-Key` validado cuando el proveedor lo admite,
`application/pdf` y el límite `INDICACIONES_PDF_MAX_BYTES`.

Un error del proveedor se transforma en `EmailApiError`. La app `indicaciones` registra
solo el tipo de excepción y mantiene el documento emitido para reintento; no registra API
keys, cuerpo del PDF, Base64 ni la respuesta completa en los mensajes clínicos.

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

El backend por API HTTP queda pensado especialmente para staging y produccion, porque evita problemas habituales con SMTP saliente y funciona bien con proveedores como Resend o Brevo.
