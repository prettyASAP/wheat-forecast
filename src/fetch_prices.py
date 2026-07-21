"""Termelői árak letöltése (terminál-bővítés B).

Forrás: Eurostat apri_ap_crpouta — mezőgazdasági termelői árak, abszolút,
Magyarország, nemzeti valuta (HUF), 100 kg-onként, évente (2000-től).
Ez hivatalos, gépileg olvasható forrás; kb. egyéves késéssel publikál,
ezért az ár-évjáratot mindenhol explicit jelöljük.

MÉRTÉKEGYSÉG: az Eurostat HUF/100 kg-ban ad -> x10 = HUF/tonna.

Kimenet: web/data/prices.json  {crop: {years, huf_per_t, latest_year, ...}}

Futtatás:  python -m src.fetch_prices [--force]
"""
from __future__ import annotations

import argparse
import json
import sys

import requests

from src import config

EUROSTAT_URL = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
    "apri_ap_crpouta?format=JSON&geo=HU&currency=NAC&lang=en"
)
# Eurostat termékkód -> a mi termény-kulcsunk
PROD_CODES = {
    "01110000": "wheat",     # soft wheat
    "01500000": "corn",      # grain maize
    "01300000": "barley",    # barley (az Eurostat nem bont őszi/tavaszi szerint)
    "02120000": "sunflower", # napraforgó (Sunflowers)
    "02110000": "rapeseed",  # repce (Rape)
}
RAW_PATH = config.DATA_RAW / "prices" / "eurostat_apri_ap_crpouta_hu.json"
PRICES_JSON = config.WEB_DATA / "prices.json"

# józansági határok HUF/100kg-ban (2000-2024 tartomány: ~2000..13000)
SANE_MIN, SANE_MAX = 1000, 30000


def _flat_index(d: dict, coords: dict[str, str]) -> int:
    """JSON-stat lapos index a dimenziósorrend szerint."""
    pos = 0
    for dim, size in zip(d["id"], d["size"]):
        idx = d["dimension"][dim]["category"]["index"][coords[dim]] if dim in coords else 0
        pos = pos * size + idx
    return pos


def extract_series(d: dict, prod_code: str) -> dict[int, float]:
    """Egy termék teljes éves idősora HUF/100kg-ban."""
    time_idx = d["dimension"]["time"]["category"]["index"]
    freq = next(iter(d["dimension"]["freq"]["category"]["index"]))
    curr = next(iter(d["dimension"]["currency"]["category"]["index"]))
    geo = next(iter(d["dimension"]["geo"]["category"]["index"]))
    out = {}
    for y in time_idx:
        pos = _flat_index(d, {"freq": freq, "currency": curr,
                              "prod_veg": prod_code, "geo": geo, "time": y})
        v = d["value"].get(str(pos))
        if v is not None:
            out[int(y)] = float(v)
    return out


def main(force: bool = False) -> None:
    print("Termelői árak letöltése (Eurostat apri_ap_crpouta, HU, HUF)")
    if RAW_PATH.exists() and not force:
        print(f"  [skip] már megvan: {RAW_PATH.name}")
        d = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    else:
        resp = requests.get(EUROSTAT_URL, timeout=60,
                            headers={"User-Agent": "wheat-forecast/1.0"})
        resp.raise_for_status()
        d = resp.json()
        RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
        RAW_PATH.write_text(json.dumps(d), encoding="utf-8")
        print(f"  [ok] {RAW_PATH.name} ({len(resp.content)} bájt)")

    # ellenőrzés: a várt valuta és ország
    if list(d["dimension"]["geo"]["category"]["index"]) != ["HU"]:
        sys.exit("HIBA: nem HU geo jött vissza — állj meg és ellenőrizd.")
    if list(d["dimension"]["currency"]["category"]["index"]) != ["NAC"]:
        sys.exit("HIBA: nem nemzeti valutás (NAC) adat jött — állj meg.")

    payload = {"source": "Eurostat apri_ap_crpouta (termelői ár, HU, HUF)",
               "unit": "HUF/t", "crops": {}}
    for code, crop in PROD_CODES.items():
        s = extract_series(d, code)
        if not s:
            sys.exit(f"HIBA: üres áridősor ({code}) — állj meg.")
        for y, v in s.items():
            if not (SANE_MIN <= v <= SANE_MAX):
                sys.exit(f"HIBA: {crop} {y} ára ({v} HUF/100kg) a józansági "
                         f"határon kívül — állj meg és ellenőrizd.")
        years = sorted(s)
        payload["crops"][crop] = {
            "years": years,
            "huf_per_t": [round(s[y] * 10) for y in years],  # HUF/100kg -> HUF/t
            "latest_year": years[-1],
            "latest_huf_per_t": round(s[years[-1]] * 10),
        }
        print(f"  {crop}: {years[0]}..{years[-1]}, legutóbbi "
              f"({years[-1]}): {s[years[-1]]*10:,.0f} HUF/t")

    PRICES_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"  [ok] {PRICES_JSON.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    main(force=ap.parse_args().force)
