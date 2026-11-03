FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --extra embed --extra ml --no-install-project
COPY . .
RUN uv sync --no-dev --extra embed --extra ml
# Pre-download the embedding model so containers start fast and offline.
RUN uv run python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "geonews.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
