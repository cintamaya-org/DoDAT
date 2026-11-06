# Base Python 3.12 (Debian Bookworm)
FROM python:3.12-bookworm

# Empêche Python de bufferiser et d’écrire des .pyc
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:/root/.local/bin:${PATH}"

# Paquets système utiles (postgres client, build, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl netcat-traditional \
 && rm -rf /var/lib/apt/lists/*

# Installer uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Dossier de l'app
WORKDIR /app

# Installer deps Python
COPY pyproject.toml /app/
RUN uv lock && uv sync --frozen --no-dev

# Copier le code; .dockerignore exclut secrets, caches et données locales.
COPY . /app

# Entrypoint (migrations, collectstatic, etc.)
COPY cintafactory/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Switch to a non-root user for runtime safety
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /logs \
    && chown -R appuser:appuser /app /logs /entrypoint.sh \
    && chmod -R ug+rwX /app /logs \
    && chmod +x /entrypoint.sh
USER appuser

# Gunicorn par défaut ; override possible via docker-compose
ENV PORT=8000
EXPOSE 8000

CMD ["/entrypoint.sh"]
