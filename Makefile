# Kényelmi célok a búzahozam-előrejelzőhöz.
# A scriptek a .venv Python-jával futnak.

PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: setup fetch data ksh boundaries weather model report clean

setup:            ## venv létrehozása (python3.12) + függőségek telepítése
	python3.12 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

ksh:              ## KSH búzahozam letöltése
	$(PY) -m src.fetch_ksh

boundaries:       ## NUTS3 HU vármegyehatárok letöltése
	$(PY) -m src.fetch_boundaries

weather:          ## ERA5 időjárás letöltése vármegyénként (a boundaries kell hozzá)
	$(PY) -m src.fetch_weather

fetch: ksh boundaries weather   ## mindhárom letöltő sorban

data: fetch       ## alias

# --- Későbbi fázisok (TODO) ---
model:            ## Fázis 4 — modell + validáció
	@echo "TODO: Fázis 4"

report:           ## Fázis 4 — backtest riport
	@echo "TODO: Fázis 4"

clean:            ## nyers/köztes adat törlése
	rm -rf data/raw/* data/interim/* data/processed/*
