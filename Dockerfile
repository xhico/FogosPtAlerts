FROM python:3.13-slim

LABEL org.opencontainers.image.title="FogosPT Alerts" \
      org.opencontainers.image.description="Wildfire monitoring and email alerting for Portugal, built on the fogos.pt API" \
      org.opencontainers.image.source="https://github.com/xhico/FogosPtAlerts" \
      org.opencontainers.image.licenses="MIT" \
      maintainer="xhico"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FOGOS_STATE_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY *.py ./

# Run unprivileged; /data is the only path that needs to be writable.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin fogos \
    && mkdir -p /data \
    && chown -R fogos:fogos /data /app
USER fogos

VOLUME ["/data"]

# Unhealthy once the state file stops being refreshed — a stalled loop is the
# failure mode that silence would otherwise hide.
HEALTHCHECK --interval=5m --timeout=10s --start-period=2m --retries=3 \
    CMD python3 -c "import os,sys,time; p=os.environ.get('FOGOS_STATE_DIR','/data')+'/state.json'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 3600 else 1)"

ENTRYPOINT ["python3", "FogosPtAlerts.py"]
