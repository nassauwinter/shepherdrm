PYTHON ?= python3

.PHONY: install test lint format format-check typecheck docs serve check db-up db-down migrate migration-check clean

install:
	$(PYTHON) scripts/dev.py install

test:
	$(PYTHON) scripts/dev.py test

lint:
	$(PYTHON) scripts/dev.py lint

format:
	$(PYTHON) scripts/dev.py format

format-check:
	$(PYTHON) scripts/dev.py format-check

typecheck:
	$(PYTHON) scripts/dev.py typecheck

docs:
	$(PYTHON) scripts/dev.py docs

serve:
	$(PYTHON) scripts/dev.py serve

check: lint format-check typecheck test docs

db-up:
	$(PYTHON) scripts/dev.py db-up

db-down:
	$(PYTHON) scripts/dev.py db-down

migrate:
	$(PYTHON) scripts/dev.py migrate

migration-check:
	$(PYTHON) scripts/dev.py migration-check

clean:
	$(PYTHON) scripts/dev.py clean
