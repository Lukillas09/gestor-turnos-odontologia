#!/usr/bin/env bash
set -o errexit

cd app
python -m gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}
