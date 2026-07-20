# Seguridad antes de produccion

Esta guia marca el trabajo de endurecimiento que debe hacerse despues de tener staging funcionando.

No reemplaza una auditoria formal. Sirve como checklist tecnico para no avanzar a produccion con decisiones flojas.

## 1. Tokens OAuth

Estado actual:

- Los tokens OAuth de Google Calendar se guardan cifrados en `GoogleCalendarConexion`.
- El admin ya no muestra el valor real de `access_token` ni `refresh_token`.
- Las pantallas internas muestran estado de conexion, no secretos.
- Los errores tecnicos de Google Calendar se normalizan antes de mostrarse al usuario.

Antes de produccion real:

- Configurar `OAUTH_TOKEN_ENCRYPTION_KEY` con una clave Fernet estable fuera del repositorio.
- Rotar credenciales OAuth si alguna vez se compartieron por error.
- Mantener el cliente OAuth en Google Cloud con redirect URIs exactos.
- Revisar accesos del admin y usuarios con permiso `is_staff`.

## 2. Acceso publico a turnos

Estado actual:

- El DNI por si solo ya no permite consultar turnos.
- El acceso publico usa OTP por email enviado al contacto ya registrado del paciente.
- Las respuestas al solicitar acceso son genericas para evitar enumeracion.
- Los codigos OTP y tokens de accion se guardan hasheados.
- Cancelar y reprogramar requieren sesion publica verificada, `POST`, CSRF y permisos de un solo uso.
- Los permisos quedan invalidos si cambia la version publica del turno.
- La solicitud publica de turno exige DNI, guarda una fotografia en `SolicitudTurnoPublica` y no sobrescribe datos de un `Paciente` existente.
- La creación pública de turnos aplica rate limit separado por IP y DNI hasheados, idempotencia por formulario, deduplicación exacta y máximo configurable de pendientes por DNI.
- Turnstile puede exigirse de forma progresiva después de varios intentos, pero no reemplaza los límites duros ni los reinicia.
- PostgreSQL conserva ventanas de rate limit e idempotencia con lease; sleeping, reinicios y varios workers no reinician las protecciones.
- Si la base no puede garantizar una protección, la operación falla cerrada con HTTP 503, `Retry-After` y mensaje genérico.
- El email es obligatorio para pacientes nuevos y para pacientes existentes activos sin email registrado, pero un email propuesto publicamente nunca se considera identidad verificada.
- Si el DNI ya existe y el formulario trae telefono/email diferentes, el turno queda marcado como `Datos por revisar` y se notifica solo al contacto almacenado previamente.
- Los codigos OTP se envian exclusivamente a `Paciente.email`; `SolicitudTurnoPublica.email_enviado` no se usa como fallback antes de una revision interna explicita.
- Aplicar un email nuevo desde revision deja `email_verificado_en=None`; el primer OTP correcto verifica la posesion del correo.
- Los pacientes nuevos creados desde la web quedan pendientes de validacion administrativa; esto no verifica automaticamente email ni telefono.
- La agenda inteligente no confía en duración, margen o puntaje enviados por el navegador: deriva la configuración y recalcula el candidato bajo bloqueo transaccional.
- Los endpoints de horarios no exponen puntajes, razones técnicas, turnos ocupados ni datos de pacientes.
- La caché de horarios es solo una optimización de lectura; nunca autoriza la creación definitiva.
- Los logs operativos de agenda usan identificadores técnicos y cantidades, sin DNI, contacto, comentario ni datos clínicos.

Antes de produccion real:

- Mantener PostgreSQL como base obligatoria y aplicar la migración de protecciones públicas.
- Dejar `TURNOS_PUBLIC_REDIS_REQUIRED=False`; configurar Redis sólo si se desea acelerar cálculos no críticos.
- Verificar límites de solicitud, OTP, reenvío, acciones públicas, creación pública por IP/DNI y máximo de pendientes según el tráfico real.
- Evaluar `TURNSTILE_ENABLED=True` y cargar `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY`.
- Programar `python manage.py limpiar_desafios_acceso_publico` para limpiar OTP vencidos y permisos inactivos.
- Ejecutar periódicamente `python manage.py limpiar_protecciones_publicas` con lotes acotados para rate limits e idempotencias vencidas.
- Capacitar a recepcion para actualizar el email del paciente desde el panel interno si no tiene contacto utilizable.
- Revisar periodicamente los turnos filtrados por `Datos por revisar` y aplicar cambios solo campo por campo.
- Revisar `/turnos/alertas-administrativas/` cuando Inicio avise que hay solicitudes sin turno.
- No pedir ni aceptar emails nuevos como prueba de identidad dentro del flujo publico.
- Activar `TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED` solo después de configurar servicios, aplicar migraciones y validar concurrencia en PostgreSQL de staging.

## 3. Autorizacion interna por objeto

Estado actual:

- El acceso interno a pacientes, turnos, historias, adjuntos y odontogramas no depende solo del ID recibido en la URL.
- Las vistas resuelven objetos desde querysets limitados por usuario antes de llamar a `get_object_or_404()`.
- Un odontologo normal ve solo pacientes con asociacion activa en `PacienteOdontologo`.
- Las asociaciones inactivas (`activo=False`) no conceden acceso.
- Historias clinicas, adjuntos y odontogramas heredan el alcance del paciente.
- Un odontologo no asociado recibe `404` al intentar abrir un paciente, historia, adjunto u odontograma fuera de su alcance.
- Recepcion y administracion conservan alcance operativo sobre pacientes y turnos, pero no ven informacion clinica si no tienen perfil odontologico.
- La politica clinica se centraliza en `historias/access_policy.py`.
- `DATOS_CLINICOS_COMPARTIDOS_ENTRE_ODONTOLOGOS=False` deja apagada la lectura compartida entre odontologos.
- El superusuario no tiene lectura clinica global silenciosa: debe abrir un acceso de emergencia por paciente, con motivo obligatorio, vencimiento y auditoria persistente.
- El odontograma sigue desactivado por `ODONTOGRAMA_FEATURE_ENABLED=False`; aun asi, el backend ya valida alcance antes de crear objetos cuando se reactive.
- El acceso a una URL no crea asociaciones, fichas, odontogramas ni estados dentales.

Pacientes archivados:

- El borrado fisico de pacientes esta bloqueado.
- El archivado conserva ficha, historias, adjuntos, turnos y asociaciones.
- Los pacientes archivados salen de listados activos y no pueden recibir nuevos turnos, historias, fichas, odontogramas ni asociaciones activas.
- El flujo publico trata DNIs archivados con respuesta neutral y deriva a revision interna sin crear turno.

Reglas de respuesta:

- Usuario anonimo en rutas internas: redireccion a login.
- Usuario autenticado sin permiso general: `403`.
- Objeto existente pero fuera del alcance: `404`, igual que un ID inexistente.
- Objeto visible con accion especifica no permitida: `403`.

Antes de produccion real:

- Mantener tests de IDOR para pacientes, historias, adjuntos, odontogramas y turnos.
- Revisar cada nueva vista que reciba `pk`, `paciente_pk`, UUID u otro identificador.
- No confiar en botones ocultos como control de seguridad.
- No registrar DNI, telefono, email ni contenido clinico en intentos denegados esperables.

## 4. Backups

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

## 5. Logs

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
- No registrar codigos OTP, tokens de accion, DNI o IP en texto plano.
- No registrar fotografias completas de solicitudes publicas ni diferencias de telefono/email en logs.
- No registrar contenido clinico sensible.
- Revisar logs de errores de Google Calendar y email sin exponer secretos.

## 6. Dominio real

Staging puede usar `tu-app.up.railway.app`.

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

## 7. HTTPS final

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
- PostgreSQL configurado, migraciones aplicadas y concurrencia de protecciones validada.
- `TURNOS_PUBLIC_REDIS_REQUIRED=False`; cualquier Redis configurado se usa sólo como caché opcional.
- Variables `TURNOS_PUBLIC_BOOKING_*` revisadas para el tráfico esperado.
- Turnstile configurado o decisión documentada para mantenerlo apagado.
- Limpieza de desafios OTP y acciones publicas agendada.
- Limpieza por lotes de rate limits e idempotencias expiradas agendada o incorporada al mantenimiento existente.
- Logs sin secretos ni datos clinicos sensibles.
- `CLINICAL_INTEGRITY_HMAC_KEY` independiente, estable y custodiada fuera de la base.
- Migraciones clínicas `0005` a `0007` aplicadas y triggers verificados en PostgreSQL.
- Ventana de mantenimiento clínico mantenida hasta completar migración, backfill e
  inicialización legacy.
- Hashes de adjuntos legacy completados antes de inicializar sus sellos.
- `verificar_integridad_historias --verificar-adjuntos --fallar-si-hay-errores` sin errores.
- Procedimiento institucional de solicitud, autenticación y entrega de copias aprobado.
- Runbook de incidente de integridad revisado por responsables operativos.

## Controles de historia clínica

La historia versionada agrega bloqueo de registros finalizados, versiones y enmiendas
append-only, folios, SHA-256 de adjuntos, auditoría, exportación interna y triggers
PostgreSQL. El sello HMAC es evidencia técnica de integridad y **no es una firma digital**.

Antes del deploy:

1. Crear una clave clínica con alta entropía, distinta de cualquier otra credencial.
2. Guardarla en las variables secretas del servicio y en custodia de recuperación
   separada de los backups de base.
3. Aplicar migraciones con un backup completo ya probado.
4. Ejecutar backfill e inicialización legacy en ese orden.
5. Revisar registros que no tengan usuario histórico; no asignar autores ficticios.
6. Ejecutar la verificación completa y conservar el resultado operativo.
7. Probar exportación y restauración en un entorno aislado.

La clave no debe aparecer en logs, tickets, manifiestos, base de datos ni archivos del
repositorio. Una persona con control simultáneo de base y secretos aún podría reconstruir
sellos, por lo que se requieren privilegios mínimos, separación de funciones, monitoreo y
backups externos.

Ante pérdida, exposición o error de integridad, no volver a sellar ni modificar el asiento.
Seguir [`docs/runbooks/incidente_integridad_clinica.md`](runbooks/incidente_integridad_clinica.md).
La guía completa está en
[`docs/historia_clinica_inmutable.md`](historia_clinica_inmutable.md).

## Controles de indicaciones postoperatorias

Antes de activar `INDICACIONES_POSTOPERATORIAS_ENABLED=True`:

1. Aplicar `historias.0008` e `indicaciones.0001/0002` sobre PostgreSQL y revisar el SQL de
   triggers.
2. Confirmar que `CLINICAL_INTEGRITY_HMAC_KEY` es independiente, estable y está respaldada
   fuera de los datos.
3. Configurar `PRIVATE_CLINICAL_STORAGE_BACKEND` contra un bucket privado y comprobar que
   la descarga directa no es pública.
4. Probar Resend con un PDF ficticio en staging y confirmar que OTP, turnos y recordatorios
   simples conservan su funcionamiento.
5. Simular un error de email: la indicación debe permanecer emitida y quedar reintentable.
6. Verificar que recepción y odontólogos no asociados reciben denegación sin exposición de
   contenido.
7. Respaldar PostgreSQL y el prefijo privado `indicaciones/`, y ensayar una restauración.
8. Revisar logs: no deben contener contenido clínico, emails completos, PDF, Base64, URLs
   firmadas ni secretos.

La inmutabilidad es defensa en profundidad: servicios, modelo, QuerySet, admin y triggers
PostgreSQL. El HMAC se presenta como sello técnico de integridad y no como firma digital.
