.PHONY: up down install migrate seed load test lint dagster dashboard viz

up:
	docker compose up -d

down:
	docker compose down

install:
	pip install -e ".[dev]"

migrate:
	alembic upgrade head

seed:
	python scripts/seed_tenants.py
	python scripts/generate_sample_project.py

load:
	baseline load sample_data/hyperscale_campus/ --tenant demo_owner

test:
	pytest -q

lint:
	ruff check src tests scripts dashboard

dagster:
	dagster dev -m baseline.orchestration.definitions

dashboard:
	PYTHONPATH=src:. streamlit run dashboard/app.py

viz:
	python -m http.server 8765 --directory viz
