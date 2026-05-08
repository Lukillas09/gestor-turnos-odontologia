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

## Recomendaciones

- No subir backups de base de datos al repositorio.
- No subir `.env`.
- Usar PostgreSQL para produccion.
- Mantener SQLite solo para aprendizaje o pruebas locales simples.
- Hacer un backup antes de cada migracion real.
