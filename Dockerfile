FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# system deps for ffmpeg and runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        build-essential \
        libgl1 \
        libglib2.0-0 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# copy whole repo into an image-only path so we can initialize volumes from it
COPY . /usr/src/app

# install Python deps from the image copy
RUN pip install --no-cache-dir -r /usr/src/app/requirements.txt

# copy runtime files into /app (this will be the container runtime working dir)
COPY . /app

# entrypoint
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# basic healthcheck: ensures the manager process is running
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD pgrep -f run_manager.py || exit 1

CMD ["/app/docker-entrypoint.sh"]
