#!/usr/bin/env bash
set -o errexit
set -o nounset

if [ -z "${DATABASE_URL:-}" ]; then
  echo "Falta DATABASE_URL para crear el backup."
  exit 1
fi

mkdir -p backups
timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
output="backups/postgresql-${timestamp}.dump"

pg_dump "$DATABASE_URL" --format=custom --no-owner --no-acl --schema=public --file "$output"

echo "Backup creado en $output"
