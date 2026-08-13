FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    DATABASE_PATH=/data/seismograph.db

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY seismograph ./seismograph

RUN pip install . \
 && rm -rf /app/seismograph /app/pyproject.toml \
 && useradd --create-home --uid 10001 seismograph \
 && mkdir -p /data \
 && chown seismograph:seismograph /data

USER seismograph
VOLUME ["/data"]

ENTRYPOINT ["python", "-m", "seismograph"]
CMD ["run"]
