# Two stages: build the React bundle with node, then serve it plus the API
# from one Python process. Cloud Run routes all traffic to a single port,
# and api/main.py already mounts web/dist as static files underneath the
# API routes, so one container serves both.

FROM node:22-slim AS web
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


FROM python:3.14-slim
WORKDIR /app

# No apt-get build deps: lxml (pulled in by readability-lxml for the URL
# input path) ships manylinux wheels, so nothing here needs a compiler.
# Verified by building this image with no system packages installed.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY panel/ ./panel/
COPY api/ ./api/
# data/ is runtime input, not just fixtures: personas.json is the pool,
# examples/ backs GET /examples, cached/ backs the ?cached=true fallback.
COPY data/personas.json ./data/personas.json
COPY data/examples/ ./data/examples/
COPY data/cached/ ./data/cached/
COPY --from=web /build/dist ./web/dist

# Run records go to /tmp: the image filesystem is the wrong place for
# them, and /tmp is the one reliably writable path on Cloud Run.
ENV RUNS_DIR=/tmp/runs
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# ONE worker, deliberately. Run state (the RUNS dict and its SSE
# subscriber queues) lives in process memory, so a second worker would
# serve requests that know nothing about a run started on the first --
# SSE reconnects would silently fail. Scale is capped at the Cloud Run
# service too (max_instance_count = 1); both limits are load-bearing.
#
# --no-proxy-headers: client addresses are resolved in one place,
# api/main.py's _client_ip (TRUSTED_PROXY_HOPS), not also by uvicorn.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --no-proxy-headers"]
