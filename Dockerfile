FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY kumo ./kumo
RUN uv sync --frozen --no-dev
ENV KUMO_DB=/data/kumo.db PATH="/app/.venv/bin:$PATH"
VOLUME /data
EXPOSE 8080
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"
CMD ["kumo", "serve"]
