.PHONY: infra api worker web test lint eval-seed eval eval-gate mcp-news

infra:            ## Postgres (pgvector) + Redis only
	docker run -d --name geonews-pg -e POSTGRES_USER=geonews -e POSTGRES_PASSWORD=geonews -e POSTGRES_DB=geonews -p 5432:5432 pgvector/pgvector:pg17 || docker start geonews-pg
	docker run -d --name geonews-redis -p 6379:6379 redis:7-alpine || docker start geonews-redis

api:
	cd backend && uv run uvicorn geonews.api.main:app --reload --port 8000

worker:
	cd backend && uv run arq geonews.ingest.worker.WorkerSettings

web:
	cd web && npm run dev

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check . && cd ../web && npm run lint

eval-seed:
	cd backend && uv run geonews-eval seed

eval:
	cd backend && uv run geonews-eval run --suite all

eval-gate:
	cd backend && uv run geonews-eval run --suite all --baseline geonews_evals/baseline.json

mcp-news:
	cd backend && uv run python -m geonews.mcp_servers.server news
