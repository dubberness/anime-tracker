FROM python:3.12-slim

LABEL org.opencontainers.image.title="Anime Collection Tracker" \
      org.opencontainers.image.description="AniList/Shoko/Sonarr collection tracker with HTML dashboard"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

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

ENV PUID=99 \
    PGID=100 \
    TZ=Australia/Hobart \
    CONFIG_DIR=/config \
    OUTPUT_DIR=/output \
    CRON_SCHEDULE="0 4 * * *" \
    RUN_ON_START=true \
    SERVE_WEB=true \
    WEB_PORT=8080

EXPOSE 8080

VOLUME ["/config", "/output"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request,os,sys; \
sys.exit(0) if os.environ.get('SERVE_WEB','true').lower() not in ('1','true','yes') \
else urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('WEB_PORT','8080')+'/', timeout=4)"

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["python3", "/app/main.py"]
