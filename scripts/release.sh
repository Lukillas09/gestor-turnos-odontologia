#!/usr/bin/env bash
set -o errexit

cd app

# Las migraciones pueden incluir DDL o backfills largos: sin statement_timeout y con un
# lock_timeout mas holgado que el de la aplicacion (0 desactiva el limite).
export DATABASE_STATEMENT_TIMEOUT_SECONDS="${DATABASE_MIGRATION_STATEMENT_TIMEOUT_SECONDS:-0}"
export DATABASE_LOCK_TIMEOUT_SECONDS="${DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS:-30}"
# Gunicorn no atiende migraciones: sin esto la validacion de coherencia con GUNICORN_TIMEOUT
# (pensada para los workers web) rechazaria cualquier statement_timeout de migracion mayor a 0.
export GUNICORN_TIMEOUT=0

python manage.py migrate --noinput
