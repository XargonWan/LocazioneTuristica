run-dev:
	python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

seed:
	python scripts/seed.py

docker-up:
	docker compose up --build

docker-down:
	docker compose down
