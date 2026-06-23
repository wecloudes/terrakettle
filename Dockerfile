# Terrakettle — Terrahawk report store & viewer
#
# Build with the cloud extra matching your object-storage backend:
#   docker build --build-arg CLOUD=aws   -t terrakettle:aws   .
#   docker build --build-arg CLOUD=azure -t terrakettle:azure .
#   docker build --build-arg CLOUD=gcp   -t terrakettle:gcp   .
ARG CLOUD=local

# ---- build stage: produce a self-contained venv ----
# Alpine base keeps the runtime free of perl/apt baggage that carries
# unfixed Debian CVEs; all runtime deps ship musllinux wheels (no compiler).
FROM python:3.12-alpine AS build
ARG CLOUD
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml README.md ./
COPY src ./src
# Install with the matching storage extra (local => no extra).
RUN pip install --no-cache-dir ".$( [ "$CLOUD" = "local" ] && echo "" || echo "[$CLOUD]" )"

# ---- runtime stage: minimal, non-root ----
FROM python:3.12-alpine AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    TERRAKETTLE_DB_PATH=/data/terrakettle.db \
    TERRAKETTLE_STORAGE_BUCKET=/data/reports
COPY --from=build /opt/venv /opt/venv
# Run as an unprivileged user; /data is chowned so the volume stays writable.
RUN addgroup -S app && adduser -S app -G app \
    && mkdir -p /data && chown app:app /data
USER app
WORKDIR /app
EXPOSE 8000
# Metadata DB + (for local backend) report files persist under /data.
VOLUME ["/data"]

ENTRYPOINT ["terrakettle"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
