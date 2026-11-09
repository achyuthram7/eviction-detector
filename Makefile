# Thin wrapper around manage.py (which also works without make: `python manage.py <target>`).

PY ?= python
ifeq ($(OS),Windows_NT)
VENV_PY := .venv/Scripts/python.exe
else
VENV_PY := .venv/bin/python
endif
PYTHON := $(if $(wildcard $(VENV_PY)),$(VENV_PY),$(PY))
RATINGS ?=
FILE ?=
DAYS ?=

.PHONY: setup seed run demo import test lint eval-model eval-learning expert-pack \
        expert-compare usability summary eval-all purge

setup:
	$(PY) -m venv .venv
	$(VENV_PY) -m pip install -r requirements.txt

seed:
	$(PYTHON) manage.py seed

run:
	$(PYTHON) manage.py run

demo:
	$(PYTHON) manage.py demo

import:
	$(PYTHON) manage.py import $(FILE)

test:
	$(PYTHON) manage.py test

lint:
	$(PYTHON) manage.py lint

eval-model:
	$(PYTHON) manage.py eval-model

eval-learning:
	$(PYTHON) manage.py eval-learning

expert-pack:
	$(PYTHON) manage.py expert-pack

expert-compare:
	$(PYTHON) manage.py expert-compare $(if $(RATINGS),--ratings $(RATINGS),)

usability:
	$(PYTHON) manage.py usability

summary:
	$(PYTHON) manage.py summary

eval-all:
	$(PYTHON) manage.py eval-all

purge:
	$(PYTHON) manage.py purge $(if $(DAYS),--days $(DAYS),)
