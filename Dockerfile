# --- Stage 1: build Tailwind CSS -----------------------------------------------------------
FROM node:22-alpine AS css
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npx tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/app.css --minify

# --- Stage 2: Python runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings PRIVATE_MEDIA_ROOT=/data/private_media
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app && mkdir -p /data/private_media && chown -R app /data
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY --chown=app . .
COPY --from=css --chown=app /build/static/css/app.css ./static/css/app.css
RUN SECRET_KEY=build-only DEBUG=0 python manage.py collectstatic --noinput
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
ENTRYPOINT ["./docker/entrypoint.sh"]
CMD ["web"]
