# Atlas Argus — single deployable unit: FastAPI serves both the API and the
# built frontend (same origin; VITE_API_URL=/).

# ── Stage 1: build the frontend ────────────────────────────────────────────
FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS web
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY index.html vite.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json ./
COPY src ./src
ENV VITE_API_URL=/
# Demo image: the sign-in screen lists the sample-case logins. Build with
# --build-arg DEMO_LOGINS= (empty) for a real deployment.
ARG DEMO_LOGINS=1
ENV VITE_DEMO_LOGINS=$DEMO_LOGINS
RUN npm run build

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.14.6-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS runtime
WORKDIR /app
RUN useradd --system --uid 999 --create-home app

# Document ingestion shells out to these: poppler-utils renders a PDF page to
# an image, tesseract-ocr reads it. pdf2image and pytesseract are only thin
# wrappers — without the binaries, every scanned page fails with
# `ocr_unavailable` rather than being read.
#
# WeasyPrint (controlled PDF export) needs Pango/HarfBuzz/fontconfig, and it
# needs real fonts. The packet stylesheet asks for Georgia/Times/Arial; with no
# fonts installed the text silently falls back and paginates differently, which
# a byte-count assertion would never catch. fonts-liberation is metrically
# compatible with Arial/Times New Roman/Courier New, so line breaks land where
# the stylesheet intends.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        poppler-utils \
        tesseract-ocr \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libffi8 \
        fontconfig \
        fonts-liberation \
        fonts-dejavu-core \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

COPY server/pyproject.toml server/requirements.lock.txt ./server/
# Install pinned, resolved dependency versions first (server/uv.lock is the
# source of truth — regenerate both with `cd server && uv lock && uv export
# --frozen --no-dev --no-hashes --no-emit-project -o requirements.lock.txt`;
# export the CI set with `--extra dev -o requirements-dev.lock.txt`),
# then the local package itself with --no-deps so pip can't silently
# re-resolve a different transitive version at build time.
RUN pip install --no-cache-dir -r server/requirements.lock.txt && rm -rf /root/.cache
COPY server/src ./server/src
RUN pip install --no-cache-dir --no-deps ./server && rm -rf /root/.cache

COPY --from=web /build/dist ./web
ENV ATLAS_ARGUS_WEB_DIST=/app/web
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

USER app
EXPOSE 8100
# Bootstrap is idempotent: it migrates to head, seeds an empty development DB,
# and requires production to have gone through the one-shot initializer.
# --proxy-headers trusts X-Forwarded-* only from configured reverse-proxy
# addresses so direct clients cannot spoof scheme/host metadata.
CMD ["sh", "-c", "python -m atlas_argus.db.bootstrap && unset ATLAS_ARGUS_MIGRATION_DATABASE_URL && exec uvicorn atlas_argus.api.app:app --host 0.0.0.0 --port 8100 --proxy-headers --forwarded-allow-ips \"${ATLAS_ARGUS_FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
