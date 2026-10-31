.PHONY: run test build up down logs

run:
	uvicorn app.main:app --reload --port 8080

test:
	pytest -q

build:
	docker build -t contact-form .

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f contact
