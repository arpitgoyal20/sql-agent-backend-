FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY prompts ./prompts
COPY scripts ./scripts
COPY data/schema.sql data/schema_docs.yaml data/sample.db ./data/

# Run as an unprivileged user; checkpoints.db is created at runtime in /app/data.
RUN useradd --create-home appuser && chown -R appuser /app/data
USER appuser

EXPOSE 8000
# --proxy-headers so rate limiting sees the real client IP behind Render's proxy.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'
