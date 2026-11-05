FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/netops

# Build-time downloads only. No runtime update agent, scanners, or telemetry.
RUN apt-get update && apt-get install -y --no-install-recommends \
      openssh-client iputils-ping traceroute dnsutils ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir 'aiohttp==3.13.3'
RUN groupadd --gid 10001 netops && useradd --uid 10001 --gid 10001 --create-home --shell /bin/sh netops \
    && install -d -m 0700 -o netops -g netops /home/netops/.ssh

WORKDIR /app
COPY --chown=10001:10001 app/ /app/
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=4s --start-period=15s --retries=3 \
  CMD python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/healthz", timeout=3).read()'
CMD ["python", "/app/server.py"]
