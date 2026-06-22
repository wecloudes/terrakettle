# Terrakettle — Terrahawk report store & viewer
#
# Build with the cloud extra matching your object-storage backend:
#   docker build --build-arg CLOUD=aws   -t terrakettle:aws   .
#   docker build --build-arg CLOUD=azure -t terrakettle:azure .
#   docker build --build-arg CLOUD=gcp   -t terrakettle:gcp   .
ARG CLOUD=local

FROM python:3.12-slim AS base
ARG CLOUD
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# Install with the matching storage extra (local => no extra).
RUN pip install --no-cache-dir ".$( [ "$CLOUD" = "local" ] && echo "" || echo "[$CLOUD]" )"

EXPOSE 8000
# Metadata DB + (for local backend) report files persist under /data.
ENV TERRAKETTLE_DB_PATH=/data/terrakettle.db \
    TERRAKETTLE_STORAGE_BUCKET=/data/reports
VOLUME ["/data"]

ENTRYPOINT ["terrakettle"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
