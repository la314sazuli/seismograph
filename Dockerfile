FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_PATH=/data/seismograph.db

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY seismograph ./seismograph
COPY fixtures ./fixtures

RUN pip install --no-cache-dir . \
 && useradd --create-home --uid 10001 seismograph \
 && mkdir -p /data \
 && chown seismograph:seismograph /data

USER seismograph
VOLUME ["/data"]

ENTRYPOINT ["python", "-m", "seismograph"]
CMD ["run"]
