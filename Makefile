.PHONY: run smoke docker-build docker-up docker-down

run:
	python3 -m app.server

smoke:
	python3 scripts/smoke_test.py

docker-build:
	docker build -t peptide-power-assistant:mvp .

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

