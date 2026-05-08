#!/usr/bin/env bash
set -o errexit

cd app
python manage.py migrate --noinput
