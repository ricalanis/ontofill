# Ontofill Console image: one process, non-root, read-only root filesystem friendly.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv/console
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra s3 --no-install-project
COPY ontofill_console ./ontofill_console
RUN uv sync --frozen --no-dev --extra s3 --no-editable \
 && useradd --system --uid 10001 --home /nonexistent console
USER console
EXPOSE 8410
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8410/healthz', timeout=2)"
CMD ["ontofill-console", "serve", "--host", "0.0.0.0", "--port", "8410"]
