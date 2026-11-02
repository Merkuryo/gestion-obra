# Con Docker: docker compose exec web make <orden>
.PHONY: run test lint fmt migrate migrations superuser shell check

run:          ## Arranca el servidor: http://localhost:8001 en tu navegador
	python manage.py runserver 0.0.0.0:8000

test:         ## Tests
	pytest

lint:         ## Revisión de estilo
	ruff check . && ruff format --check .

fmt:          ## Formatea el código
	ruff check --fix . && ruff format .

migrations:   ## Crea migraciones
	python manage.py makemigrations

migrate:      ## Aplica migraciones
	python manage.py migrate

superuser:    ## Crea tu usuario administrador
	python manage.py createsuperuser

shell:
	python manage.py shell

check: lint test   ## Lo que pasa la CI
	python manage.py makemigrations --check --dry-run
