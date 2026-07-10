"""NUTS3 vármegyehatárok letöltése (Eurostat GISCO, NUTS 2024).

Letölti a teljes EU NUTS3 GeoJSON-t, kiszűri CNTR_CODE == "HU" -> 20 magyar
egység, és elmenti mind a teljeset, mind a szűrt HU réteget a data/raw/boundaries/
alá. Ha a GISCO nem elérhető, sorban próbálja a config-beli mirrorokat, és ha mind
hibázik, megáll (nem talál ki adatot — brief 0. pont).

Futtatás:  python -m src.fetch_boundaries [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

from src import config

TIMEOUT = 120

ALL_DEST = config.RAW_BOUNDARIES / "nuts3_all_20M_2024.geojson"
HU_DEST = config.RAW_BOUNDARIES / "nuts3_hu_20M_2024.geojson"


def download_geojson(force: bool) -> dict:
    """Letölti a NUTS3 GeoJSON-t (fő URL, majd mirrorok). Visszaadja a parse-olt dictet."""
    if ALL_DEST.exists() and not force:
        print(f"  [skip] már megvan: {ALL_DEST.name}")
        return json.loads(ALL_DEST.read_text(encoding="utf-8"))

    urls = [config.GISCO_NUTS3_URL, *config.GISCO_NUTS3_MIRRORS]
    last_err: Exception | None = None
    for url in urls:
        try:
            print(f"  próba: {url}")
            resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "wheat-forecast/1.0"})
            resp.raise_for_status()
            data = resp.json()
            ALL_DEST.parent.mkdir(parents=True, exist_ok=True)
            ALL_DEST.write_text(json.dumps(data), encoding="utf-8")
            print(f"  [ok]   {ALL_DEST.name} ({len(resp.content)} bájt, "
                  f"{len(data.get('features', []))} feature)")
            return data
        except Exception as exc:  # noqa: BLE001 — szándékosan fallbacket próbálunk
            print(f"  [hiba] {exc}")
            last_err = exc

    sys.exit(f"HIBA: egyik NUTS3 forrás sem elérhető. Utolsó hiba: {last_err}")


def filter_hu(data: dict) -> dict:
    """Kiszűri a CNTR_CODE == HU feature-öket (20 magyar NUTS3 egység)."""
    feats = [f for f in data.get("features", [])
             if f.get("properties", {}).get("CNTR_CODE") == config.COUNTRY_CODE]
    return {"type": "FeatureCollection", "features": feats}


def main(force: bool = False) -> None:
    print("NUTS3 vármegyehatárok letöltése (GISCO)")
    data = download_geojson(force)
    hu = filter_hu(data)
    HU_DEST.write_text(json.dumps(hu), encoding="utf-8")

    n = len(hu["features"])
    print(f"  [ok]   {HU_DEST.name} ({n} magyar NUTS3 egység)")
    if n != 20:
        print(f"  FIGYELEM: {n} egységet találtam, várt 20 (19 vármegye + Budapest). Ellenőrizd!")

    for f in sorted(hu["features"], key=lambda x: x["properties"]["NUTS_ID"]):
        p = f["properties"]
        print(f"    {p['NUTS_ID']}  {p.get('NAME_LATN', '?')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="NUTS3 HU vármegyehatárok letöltése")
    ap.add_argument("--force", action="store_true", help="Újratöltés akkor is, ha megvan")
    main(force=ap.parse_args().force)
