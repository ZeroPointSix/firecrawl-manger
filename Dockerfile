FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 appuser

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations
COPY config.yaml /app/config.yaml
COPY app /app/app
COPY scripts /app/scripts

RUN mkdir -p "/app/data" && chown -R appuser:appuser "/app"

USER appuser
EXPOSE 8000

CMD ["/bin/sh", "/app/scripts/entrypoint.sh"]
