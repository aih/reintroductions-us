FROM python:3.13-slim

# git: usc-run installs from github.com/unitedstates/congress.
# Pango and DejaVu: WeasyPrint's PDF rendering (reintro/pdf.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    REINTRO_DB=/data/reintro.db \
    REINTRO_CACHE=/data/cache \
    REINTRO_USC_ROOT=/data/usc

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra ingest --extra aws --no-install-project

COPY . .
RUN uv sync --frozen --no-dev --extra ingest --extra aws

# The commit the footer and /healthz name; deploy/deploy-on-box.sh passes it.
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

EXPOSE 8020

CMD ["uvicorn", "reintro.web.app:app", "--host", "0.0.0.0", "--port", "8020", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--limit-concurrency", "32"]
