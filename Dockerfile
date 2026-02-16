FROM node:22-alpine@sha256:e4bf2a82ad0a4037d28035ae71529873c069b13eb0455466ae0bc13363826e34 AS webui-builder

WORKDIR /src/webui

COPY webui/package.json webui/package-lock.json /src/webui/
RUN npm ci

RUN mkdir -p /src/app
COPY webui/ /src/webui/
RUN npm run build


FROM python:3.11-slim@sha256:0b23cfb7425d065008b778022a17b1551c82f8b4866ee5a7a200084b7e2eafbf

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
COPY --from=webui-builder /src/app/ui2 /app/app/ui2
COPY scripts /app/scripts
RUN sed -i 's/\r$//' /app/scripts/entrypoint.sh && chmod +x /app/scripts/entrypoint.sh

RUN mkdir -p "/app/data" && chown -R appuser:appuser "/app"

USER appuser
EXPOSE 8000

CMD ["/bin/sh", "/app/scripts/entrypoint.sh"]
