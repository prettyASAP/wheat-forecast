# Kényelmi célok a búzahozam-előrejelzőhöz.
# A scriptek a .venv Python-jával futnak.

PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: setup fetch data ksh boundaries weather model report clean

setup:            ## venv létrehozása (python3.12) + függőségek telepítése
	python3.12 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

ksh:              ## KSH hozamtáblák letöltése (búza + kukorica)
	$(PY) -m src.fetch_ksh --crop wheat
	$(PY) -m src.fetch_ksh --crop corn
	$(PY) -m src.fetch_ksh --crop barley

boundaries:       ## NUTS3 HU vármegyehatárok letöltése
	$(PY) -m src.fetch_boundaries

weather:          ## ERA5 időjárás letöltése vármegyénként (a boundaries kell hozzá)
	$(PY) -m src.fetch_weather

fetch: ksh boundaries weather   ## mindhárom letöltő sorban

panel:            ## panel + feature-ök mindkét terményre
	$(PY) -m src.build_panel --crop wheat && $(PY) -m src.features --crop wheat
	$(PY) -m src.build_panel --crop corn && $(PY) -m src.features --crop corn
	$(PY) -m src.build_panel --crop barley && $(PY) -m src.features --crop barley

data: fetch panel ## teljes adat-pipeline

model:            ## LOYO validáció mindkét terményre
	$(PY) -m src.validate --crop wheat
	$(PY) -m src.validate --crop corn
	$(PY) -m src.validate --crop barley

report:           ## as-of backtest + magyar riport mindkét terményre
	$(PY) -m src.backtest --crop wheat
	$(PY) -m src.backtest --crop corn
	$(PY) -m src.backtest --crop barley

live:             ## élő előrejelzés mindkét terményre (forecast_*.json)
	$(PY) -m src.predict_live --crop wheat
	$(PY) -m src.predict_live --crop corn
	$(PY) -m src.predict_live --crop barley

clean:            ## nyers/köztes adat törlése
	rm -rf data/raw/* data/interim/* data/processed/*
history:           ## statikus hozam-idősor export a webre (KSH-val keresztellenőrizve)
	$(PY) -m src.export_history --crop wheat
	$(PY) -m src.export_history --crop corn
	$(PY) -m src.export_history --crop barley
prices:            ## termelői árak frissítése (Eurostat, HUF)
	$(PY) -m src.fetch_prices --force
