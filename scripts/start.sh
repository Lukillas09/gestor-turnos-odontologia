#!/usr/bin/env bash
set -o errexit

cd app

# GUNICORN_TIMEOUT tambien lo lee config/settings.py: statement_timeout + lock_timeout deben ser
# menores, asi la base corta una consulta o una espera de lock antes de que gunicorn mate al worker
# a mitad de una transaccion. --max-requests recicla los workers (0 lo desactiva).
python -m gunicorn config.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers ${WEB_CONCURRENCY:-2} \
    --timeout ${GUNICORN_TIMEOUT:-30} \
    --graceful-timeout ${GUNICORN_TIMEOUT:-30} \
    --max-requests ${GUNICORN_MAX_REQUESTS:-1000} \
    --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER:-100}
