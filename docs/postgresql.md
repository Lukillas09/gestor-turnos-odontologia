# PostgreSQL

Esta guia explica como pasar el proyecto de SQLite a PostgreSQL sin cambiar el flujo de desarrollo local.

## Estado del proyecto

El proyecto usa SQLite por defecto cuando `DATABASE_URL` queda vacio. Esto permite seguir aprendiendo y probando rapido en local.

Para usar PostgreSQL hay que configurar `DATABASE_URL` en `.env`:

```env
DATABASE_URL=postgres://usuario:password@host:5432/gestor_turnos?sslmode=require
```

Tambien se acepta el esquema `postgresql://`.

## Preparar el entorno

Instalar dependencias:

```powershell
pip install -r requirements.txt
```

El proyecto incluye:

```text
psycopg[binary]==3.3.4
```

Ese paquete permite que Django se conecte a PostgreSQL.

## Migrar datos desde SQLite

Antes de cambiar `DATABASE_URL`, exportar los datos actuales desde SQLite:

```powershell
cd app
New-Item -ItemType Directory -Force ..\backups
python manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.Permission --indent 2 > ..\backups\sqlite-datos.json
```

La carpeta `backups/` esta ignorada por Git porque puede contener datos sensibles de pacientes.

Despues configurar PostgreSQL en `.env`:

```env
DATABASE_URL=postgres://usuario:password@host:5432/gestor_turnos?sslmode=require
```

Aplicar migraciones en PostgreSQL:

```powershell
python manage.py migrate
```

Cargar los datos exportados:

```powershell
python manage.py loaddata ..\backups\sqlite-datos.json
```

Verificar el proyecto:

```powershell
python manage.py check
python manage.py test
```

## Desarrollo local

Para volver a SQLite en desarrollo, dejar `DATABASE_URL` vacio:

```env
DATABASE_URL=
```

Con eso Django vuelve a usar:

```text
app/db.sqlite3
```

## Conexion a Supabase y timeouts

### Que `DATABASE_URL` usar

| Opcion | Host y puerto | Cuando |
| --- | --- | --- |
| Pooler de Supavisor en **modo sesion** (recomendado en Railway) | `aws-0-<region>.pooler.supabase.com:5432`, usuario `postgres.<ref>` | Es IPv4 y se comporta como una conexion directa (sesion, `SET`, transacciones). |
| Conexion directa | `db.<ref>.supabase.co:5432` | Resuelve solo a IPv6 salvo que el proyecto tenga el complemento IPv4, y Railway no sale por IPv6. |
| Pooler en modo transaccion | `aws-0-<region>.pooler.supabase.com:6543` | Solo si hay mas clientes que conexiones. Pierde el estado de sesion (ver abajo). |

Copiar el host exacto desde el panel de Supabase (Connect, Session pooler); la region y el prefijo
(`aws-0`, `aws-1`) cambian por proyecto. Siempre con `sslmode=require`:

```env
DATABASE_URL=postgres://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

### Variables de conexion

Todas en segundos y con `0` para desactivar el limite. Solo aplican a PostgreSQL; con SQLite no
tienen efecto.

| Variable | Default | Efecto |
| --- | --- | --- |
| `DATABASE_CONN_MAX_AGE` | `60` | Reutiliza la conexion entre requests (`CONN_MAX_AGE`) y la verifica antes de reutilizarla (`CONN_HEALTH_CHECKS`). Evita un handshake TLS por request. |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | `5` | `connect_timeout` si la URL no lo trae. |
| `DATABASE_LOCK_TIMEOUT_SECONDS` | `5` | `lock_timeout` de sesion: una espera de lock falla en vez de colgar al worker. |
| `DATABASE_STATEMENT_TIMEOUT_SECONDS` | `20` | `statement_timeout` de sesion. |
| `DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS` | `60` | Corta sesiones que dejan una transaccion abierta. |

Lo que ya traiga la URL (`connect_timeout`, o `options=-c lock_timeout=...`) tiene prioridad y no
se pisa. Los timeouts se envian como parametros de arranque solo desde la aplicacion: `pg_dump` y
los scripts de backup, que usan la URL directa, no se ven afectados.

`DATABASE_STATEMENT_TIMEOUT_SECONDS + DATABASE_LOCK_TIMEOUT_SECONDS` debe ser menor que
`GUNICORN_TIMEOUT` (30 por defecto); si no, Django no arranca. Asi la base corta una consulta o
una espera antes de que gunicorn mate al worker a mitad de una transaccion.

Cuando una espera agota su tiempo (`lock_timeout`, `statement_timeout` o deadlock), la aplicacion
responde un 503 neutral ("El sistema esta ocupado...") en lugar de un 500, y la reserva publica
libera su idempotencia para poder reenviar. No hay reintentos automaticos de mutaciones.

### Migraciones

`scripts/release.sh` corre `migrate` sin `statement_timeout` y con `lock_timeout` de 30 s
(`DATABASE_MIGRATION_STATEMENT_TIMEOUT_SECONDS` y `DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS`), para
no cortar un indice o un backfill legitimo. Para migraciones manuales largas:

```bash
DATABASE_STATEMENT_TIMEOUT_SECONDS=0 DATABASE_LOCK_TIMEOUT_SECONDS=30 python manage.py migrate
```

### Comandos de mantenimiento

Los `manage.py` (verificadores de integridad, backfills legacy, limpiezas, recordatorios) usan
los mismos timeouts de sesion que la aplicacion: `statement_timeout` de 20 s por **sentencia**
(antes la base permitia 120 s). Ningun comando mantiene una transaccion abierta alrededor de
Storage, PDF o email, y las corridas largas son muchas sentencias cortas, asi que no deberian
afectarse. Si una corrida puntual necesita sentencias mas largas, se puede relajar solo para ese
comando:

```bash
DATABASE_STATEMENT_TIMEOUT_SECONDS=0 python manage.py verificar_integridad_historias --verificar-adjuntos
```

### Si el pooler rechaza los parametros de arranque

Si Supavisor en modo sesion rechazara la conexion por los timeouts de arranque, poner las tres
variables `DATABASE_LOCK_TIMEOUT_SECONDS`, `DATABASE_STATEMENT_TIMEOUT_SECONDS` y
`DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS` en `0`: dejan de enviarse y el despliegue se
destraba, mientras los timeouts se fijan a nivel de rol (`ALTER ROLE ... SET ...`).

### Pooler en modo transaccion (puerto 6543)

Si la URL usa el puerto `6543`, la configuracion cambia sola: se desactivan los cursores del
servidor (`DISABLE_SERVER_SIDE_CURSORS`) y las sentencias preparadas de psycopg
(`prepare_threshold=None`), y **no se envian los timeouts de sesion** porque ese modo no conserva
el estado de sesion. En ese caso hay que fijarlos a nivel de rol desde el SQL Editor de Supabase
(por ejemplo `ALTER ROLE postgres SET lock_timeout = '5s'`). `DATABASE_CONN_MAX_AGE` se mantiene:
persistir la conexion cliente-pooler evita el handshake TLS y el pooler la multiplexa. La opcion
recomendada sigue siendo el modo sesion (5432).

### Verificar los timeouts efectivos

Despues de cambiar la URL o desplegar, confirmar desde la aplicacion (no desde el SQL Editor, que
usa otra conexion) que el pooler respeto los parametros de arranque:

```bash
cd app
python manage.py shell -c "
from django.db import connection
c = connection.cursor()
for n in ('lock_timeout', 'statement_timeout', 'idle_in_transaction_session_timeout'):
    c.execute('SHOW ' + n); print(n, c.fetchone()[0])
"
```

Debe mostrar `5s`, `20s` y `1min` (o los valores configurados). Si el pooler rechaza la conexion
por los parametros de arranque, `scripts/release.sh` falla antes de publicar la version nueva.
Si los acepta pero los ignora, usar la URL de conexion directa (con complemento IPv4) o fijar los
timeouts a nivel de rol.

### Tests y triggers clinicos

Bajo `manage.py test` las conexiones no persisten (`CONN_MAX_AGE=0`): las pruebas de concurrencia
usan hilos propios y PostgreSQL no podria borrar la base de pruebas con sesiones abiertas.

Las funciones de los triggers clinicos (`historias_*` e `indicaciones_*`) tienen el
`search_path` fijado en `pg_catalog, pg_temp` (migraciones `historias.0009` e `indicaciones.0003`).
Sus cuerpos no usan ningun objeto de esquema. Cualquier `CREATE OR REPLACE FUNCTION` futuro de
esas funciones debe repetir `SET search_path`; una prueba de la suite PostgreSQL lo verifica.

## Recomendaciones

- No subir backups de base de datos al repositorio.
- No subir `.env`.
- Usar PostgreSQL para produccion.
- Mantener SQLite solo para aprendizaje o pruebas locales simples.
- Hacer un backup antes de cada migracion real.
