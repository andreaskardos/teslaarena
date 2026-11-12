# Tesla Arena game server.
#
#   docker build -t teslaarena .
#   docker run -p 8000:8000 teslaarena      # then open http://localhost:8000
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Proxies whose X-Forwarded-For header is trusted for client IPs (used by the
    # per-IP rate limits). Set it to your reverse proxy's address, or "*" when
    # the container is only reachable through that proxy.
    FORWARDED_ALLOW_IPS=127.0.0.1

WORKDIR /app

# Runtime dependencies only; keep in sync with [project] dependencies in pyproject.toml.
RUN pip install "fastapi>=0.115,<1" "uvicorn[standard]>=0.34,<1"

# The server serves ../../web relative to its package, so keep the repo layout.
COPY src ./src
COPY web ./web

RUN useradd --system --no-create-home --uid 10001 arena
USER arena

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/rooms', timeout=4)"]

# Exactly one worker: rooms and matches live in this process's memory.
CMD ["uvicorn", "tesla_arena.server:app", "--app-dir", "src", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
