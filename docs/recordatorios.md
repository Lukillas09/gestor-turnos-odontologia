# Recordatorios automaticos

El proyecto ya tiene el comando:

```powershell
python manage.py enviar_recordatorios_email
```

Este comando busca turnos confirmados proximos, envia el recordatorio por email y termina. Esa forma encaja bien con schedulers externos porque el proceso no queda corriendo.

## Estrategia elegida

Por ahora no se agrega Celery ni un scheduler dentro de Django. Para esta etapa conviene usar un scheduler externo:

- GitHub Actions para staging gratuito.
- Railway Cron Job.
- Cron en Linux.
- Programador de tareas de Windows para pruebas locales.

Esta estrategia mantiene el proyecto simple y evita tener un proceso de fondo permanente.

## Frecuencia recomendada

Recomendacion inicial:

```text
0 * * * *
```

Esto ejecuta el comando una vez por hora.

Comando recomendado:

```bash
bash scripts/recordatorios.sh
```

El sistema marca cada turno con `recordatorio_email_enviado_en`, por eso no envia el mismo recordatorio dos veces aunque el comando corra cada hora.

El script `scripts/recordatorios.sh` lee `TURNOS_RECORDATORIO_HORAS`. Si la variable no existe, usa `24`.

## GitHub Actions para staging

El repositorio incluye el workflow:

```text
.github/workflows/staging_recordatorios.yml
```

Para activarlo en GitHub:

1. Ir a `Settings` -> `Secrets and variables` -> `Actions`.
2. Crear la variable:

```text
STAGING_RECORDATORIOS_ACTIVO=true
```

3. Crear los secrets obligatorios:

```text
STAGING_DJANGO_SECRET_KEY
STAGING_DJANGO_ALLOWED_HOSTS
STAGING_DJANGO_CSRF_TRUSTED_ORIGINS
STAGING_DATABASE_URL
STAGING_OAUTH_TOKEN_ENCRYPTION_KEY
STAGING_EMAIL_BACKEND
STAGING_EMAIL_API_PROVIDER
STAGING_EMAIL_API_KEY
STAGING_DEFAULT_FROM_EMAIL
```

`STAGING_OAUTH_TOKEN_ENCRYPTION_KEY` debe contener la misma clave Fernet estable que usa el entorno conectado a la base de staging. No se debe generar una clave nueva en cada ejecución porque dejaría inaccesibles los tokens OAuth cifrados previamente.

Valores no secretos recomendados para Resend:

```text
STAGING_EMAIL_BACKEND=config.email_backends.EmailApiBackend
STAGING_EMAIL_API_PROVIDER=resend
```

Secrets opcionales según el backend seleccionado:

```text
STAGING_EMAIL_API_URL
STAGING_EMAIL_HOST
STAGING_EMAIL_PORT
STAGING_EMAIL_HOST_USER
STAGING_EMAIL_HOST_PASSWORD
STAGING_EMAIL_USE_TLS
STAGING_EMAIL_USE_SSL
```

Con `config.email_backends.EmailApiBackend`, el workflow exige que `STAGING_EMAIL_API_PROVIDER` y `STAGING_EMAIL_API_KEY` no estén vacíos. También rechaza explícitamente los backends de consola, memoria, dummy y archivos para evitar ejecuciones exitosas que no envíen emails reales.

El job configura `TURNOS_PUBLIC_REDIS_REQUIRED=False` y deja `REDIS_URL` vacío únicamente durante la ejecución programada. Este proceso no atiende tráfico público ni necesita el rate limiting distribuido; la aplicación web en Railway conserva su configuración y política de Redis independientes.

Antes de enviar recordatorios, el workflow valida los Secrets requeridos y ejecuta `python manage.py check` con `DJANGO_DEBUG=False`. No ejecuta migraciones ni `collectstatic`.

El workflow corre automáticamente cada hora. También se puede ejecutar manualmente desde `Actions` -> `Staging recordatorios` -> `Run workflow`.

En ejecucion manual se puede cambiar `horas` para probar una ventana mas amplia, por ejemplo `72`.

## Railway Cron Job

En Railway conviene crear un servicio separado para recordatorios. No configurar el cron sobre el servicio `web`, porque `web` debe quedar levantado continuamente.

Pasos:

1. En Railway, dentro del mismo proyecto, crear un nuevo servicio desde el mismo repo.
2. Nombrarlo `recordatorios-email`.
3. Cargar las mismas variables de entorno que usa el servicio `web`, especialmente:

```text
DJANGO_SECRET_KEY
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS
DJANGO_CSRF_TRUSTED_ORIGINS
OAUTH_TOKEN_ENCRYPTION_KEY
DATABASE_URL
EMAIL_BACKEND
EMAIL_API_PROVIDER
EMAIL_API_KEY
DEFAULT_FROM_EMAIL
TURNOS_RECORDATORIO_HORAS=24
```

4. Configurar el start command del servicio `recordatorios-email`:

```text
bash scripts/recordatorios.sh
```

5. En `Settings`, configurar `Cron Schedule`:

```text
0 * * * *
```

Ese schedule ejecuta recordatorios una vez por hora.

Valores sugeridos:

```text
Cron Schedule: 0 * * * *
Start Command: bash scripts/recordatorios.sh
```

Railway usa UTC para cron. El proceso debe terminar cuando finaliza el comando; por eso se usa un script corto que envia recordatorios y sale.

Para probarlo manualmente antes de dejarlo automatico:

```bash
bash scripts/recordatorios.sh
```

La prueba esperada:

- Si hay turnos confirmados dentro de las proximas 24 horas con email de paciente, envia recordatorios.
- Si no hay turnos candidatos, termina correctamente con `Encontrados: 0`.
- Si un recordatorio ya fue enviado, no lo duplica.

## Cron en Linux

Ejemplo para un servidor Linux:

```cron
0 * * * * cd /ruta/gestor-turnos-odontologia/app && /ruta/venv/bin/python manage.py enviar_recordatorios_email --horas 24 --fallar-si-hay-errores >> /ruta/logs/recordatorios.log 2>&1
```

## Windows Task Scheduler

Para pruebas locales en Windows se puede crear una tarea programada que ejecute:

```powershell
C:\Users\Lucas\Proyectos\.venv\Scripts\python.exe C:\Users\Lucas\Proyectos\gestor-turnos-odontologia\app\manage.py enviar_recordatorios_email --horas 24 --fallar-si-hay-errores
```

Configurar la tarea para ejecutarse cada 1 hora.

## Modo de monitoreo

El flag:

```bash
--fallar-si-hay-errores
```

hace que el comando termine con error si algun recordatorio no pudo enviarse. Esto ayuda a que Railway, GitHub Actions o cron muestren el problema en los logs.

## Referencias

- Railway Cron Jobs: https://docs.railway.com/cron-jobs
