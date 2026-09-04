FROM ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 AS uv

FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS builder

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

LABEL org.opencontainers.image.title="karakeep-opds"
LABEL org.opencontainers.image.description="OPDS bridge for Karakeep bookmarks"

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN python -m pip uninstall -y pip setuptools wheel \
  && rm -rf /usr/local/lib/python*/ensurepip /root/.cache /tmp/* \
  && addgroup --system --gid 65532 app \
  && adduser --system --uid 65532 --ingroup app --home /nonexistent --no-create-home app

COPY --from=builder --chown=app:app /app /app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"

USER 65532:65532

CMD ["python", "-m", "karakeep_opds"]
