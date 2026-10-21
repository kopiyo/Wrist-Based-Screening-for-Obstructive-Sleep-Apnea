.PHONY: up down api web test-api build-web

up:
	docker compose up --build

down:
	docker compose down

api:
	uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000

web:
	cd apps/web && npm run dev

test-api:
	python -m pytest apps/api/tests -q

build-web:
	cd apps/web && npm run build
