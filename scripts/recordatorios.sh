#!/usr/bin/env bash
set -o errexit

cd app
python manage.py enviar_recordatorios_email \
  --horas "${TURNOS_RECORDATORIO_HORAS:-24}" \
  --fallar-si-hay-errores
