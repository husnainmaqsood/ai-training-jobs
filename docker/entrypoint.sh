#!/bin/sh
set -e
case "$1" in
  web)
    python manage.py migrate --noinput
    exec gunicorn config.wsgi:application -c docker/gunicorn.conf.py ;;
  worker)
    exec celery -A config worker --loglevel=INFO --concurrency="${CELERY_CONCURRENCY:-2}" ;;
  beat)
    exec celery -A config beat --loglevel=INFO --schedule=/tmp/celerybeat-schedule ;;
  manage)
    shift; exec python manage.py "$@" ;;
  *)
    exec "$@" ;;
esac
