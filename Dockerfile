FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PRAXIS_TRANSPORT=streamable-http \
    PRAXIS_HOST=0.0.0.0 \
    PRAXIS_PORT=8000 \
    PRAXIS_DB=/data/praxis.sqlite

WORKDIR /app

# Build metadata and sources for the praxis package (poetry-core build backend).
COPY pyproject.toml README.md ./
COPY src/praxis ./src/praxis

RUN pip install --no-cache-dir . && mkdir -p /data

EXPOSE 8000

CMD ["praxis-server"]
