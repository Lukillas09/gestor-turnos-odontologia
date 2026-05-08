# Recordatorios automaticos

El proyecto ya tiene el comando:

```powershell
python manage.py enviar_recordatorios_email
```

Este comando busca turnos confirmados proximos, envia el recordatorio por email y termina. Esa forma encaja bien con schedulers externos porque el proceso no queda corriendo.

## Estrategia elegida

Por ahora no se agrega Celery ni un scheduler dentro de Django. Para esta etapa conviene usar un scheduler externo:

- Render Cron Job.
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
cd app && python manage.py enviar_recordatorios_email --horas 24 --fallar-si-hay-errores
```

El sistema marca cada turno con `recordatorio_email_enviado_en`, por eso no envia el mismo recordatorio dos veces aunque el comando corra cada hora.

## Render Cron Job

En Render se crea un Cron Job desde el dashboard.

Valores sugeridos:

```text
Schedule: 0 * * * *
Command: cd app && python manage.py enviar_recordatorios_email --horas 24 --fallar-si-hay-errores
```

Render usa horarios en UTC para la expresion cron. Si se necesita una hora exacta de Argentina, hay que convertirla a UTC.

El Cron Job debe tener las mismas variables de entorno que la app web:

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS`
- `DATABASE_URL`
- Variables SMTP
- Variables de email por API HTTP (`EMAIL_BACKEND`, `EMAIL_API_PROVIDER`, `EMAIL_API_KEY`, `DEFAULT_FROM_EMAIL`)
- Variables de Google Calendar si hicieran falta

## Railway Cron Job

En Railway se puede configurar un servicio como Cron Job desde Settings usando una expresion crontab.

Valores sugeridos:

```text
Cron Schedule: 0 * * * *
Start Command: cd app && python manage.py enviar_recordatorios_email --horas 24 --fallar-si-hay-errores
```

Railway tambien usa UTC para cron. El proceso debe terminar cuando finaliza el comando.

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

hace que el comando termine con error si algun recordatorio no pudo enviarse. Esto ayuda a que Render, Railway o cron muestren el problema en los logs.

## Referencias

- Render Cron Jobs: https://render.com/docs/cronjobs
- Railway Cron Jobs: https://docs.railway.com/cron-jobs
