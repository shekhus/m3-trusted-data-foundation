FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first so code changes don't invalidate this layer. See docs/decisions.md D-005.
RUN pip install --no-cache-dir uv==0.11.3
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

COPY . .

# data/ is never committed (.gitignore, .dockerignore): the image generates it with the deterministic generator,
# so a deployed instance has the same masters, extracts, knowledge base and answer keys as CI (D-032).
# Locally, docker-compose mounts ./data over it.
RUN python -m synth.generate --out data --clean && mkdir -p data/uploads

EXPOSE 8000
# Migrations first (advisory lock: parallel replicas are safe), then a best-effort index sync, then the API on
# $PORT (Railway) or 8000. The portal runs the same image with scripts/start_portal.sh.
CMD ["sh", "scripts/start.sh"]
