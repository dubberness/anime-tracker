FROM python:3.12-slim

ARG BUILD_SHA=dev
ARG BUILD_DATE=""

LABEL org.opencontainers.image.title="Anime Collection Tracker" \
      org.opencontainers.image.description="AniList/Shoko/Sonarr collection tracker with a web dashboard" \
      org.opencontainers.image.source="https://github.com/dubberness/anime-tracker" \
      org.opencontainers.image.revision="${BUILD_SHA}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# tini for signal handling, gosu to drop to PUID/PGID the way Unraid expects.
RUN apt-get update && \
    apt-get install -y --no-install-recommends tini gosu tzdata && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN mkdir -p /config /output

# Deliberately NOT setting CRON_SCHEDULE or RUN_ON_START here: an env var
# always wins over the config file, so baking them in would permanently grey
# out the schedule fields in the settings page.
ENV BUILD_SHA=${BUILD_SHA} \
    BUILD_DATE=${BUILD_DATE} \
    PUID=99 \
    PGID=100 \
    TZ=Australia/Hobart \
    CONFIG_DIR=/config \
    OUTPUT_DIR=/output \
    SERVE_WEB=true \
    WEB_PORT=8080 \
    LOG_LEVEL=INFO

EXPOSE 8080

VOLUME ["/config"]

# Hits the app's own health endpoint rather than assuming the dashboard renders.
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import os,sys,urllib.request; \
sys.exit(0) if os.environ.get('SERVE_WEB','true').lower() not in ('1','true','yes','on') \
else urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('WEB_PORT','8080')+'/api/health', timeout=4)"

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["python3", "/app/main.py"]
