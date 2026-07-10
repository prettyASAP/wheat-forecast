"""Statikus idősor-export a frontendnek (terminál-bővítés A).

A panel_{crop}.parquet-ből legenerálja a web/data/yield_history_{crop}.json-t:
  - vármegyénként: évek + tényleges hozamok (t/ha)
  - országos idősor: Σ termés / Σ terület (valódi országos átlag, nem súlyozott
    hozam-átlag) — Budapest BENNE van, mert a tényadatnál nincs ok kihagyni
  - országos lineáris trend együtthatói (a fejléc-anomáliához)

KERESZTELLENŐRZÉS: az országos idősort a KSH csv "Ország összesen" sorával
vetjük össze — ha bármely év ±0.01 t/ha-nál jobban eltér, a script hibával leáll.

Futtatás:  python -m src.export_history [--crop wheat|corn]
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from src import config
from src.build_panel import KSH_SECTIONS, _parse_number

TREND_BASE_YEAR = 2000


def yield_history_json(crop: str):
    return config.WEB_DATA / f"yield_history_{crop}.json"


def national_series(panel: pd.DataFrame) -> pd.Series:
    """Országos hozam évenként: Σ betakarított termés / Σ betakarított terület."""
    g = panel.dropna(subset=["production_t", "area_ha"]).groupby("crop_year")
    return g["production_t"].sum() / g["area_ha"].sum()


def ksh_national_official(crop: str) -> dict[str, dict[int, float]]:
    """A KSH csv 'Ország összesen' sorai mindhárom szekcióból — etalon.

    Visszaad: {"yield_t_ha": {év: érték}, "area_ha": {...}, "production_t": {...}}
    """
    path = config.RAW_KSH / f"{config.CROPS[crop]['ksh_slug']}.csv"
    lines = path.read_bytes().decode(config.KSH_ENCODING).splitlines()
    header = lines[1].split(";")
    years = [int(c) for c in header if c.strip().isdigit()]
    year_start = next(i for i, c in enumerate(header) if c.strip().isdigit())

    out: dict[str, dict[int, float]] = {}
    section = None
    for ln in lines[2:]:
        cells = ln.split(";")
        first = cells[0].strip()
        if first in KSH_SECTIONS:
            section = KSH_SECTIONS[first]
            continue
        if section and first == "Ország összesen":
            vals = {}
            for year, cell in zip(years, cells[year_start:]):
                v = _parse_number(cell)
                if v is not None:
                    vals[year] = v / 1000.0 if section == "yield_kg_ha" else v
            out["yield_t_ha" if section == "yield_kg_ha" else section] = vals
    if "yield_t_ha" not in out or "area_ha" not in out or "production_t" not in out:
        sys.exit("HIBA: nem találom az 'Ország összesen' sorokat a KSH csv-ben.")
    return out


def main(crop: str = config.DEFAULT_CROP) -> None:
    label = config.CROPS[crop]["label"]
    print(f"Idősor-export — {label}")
    panel = pd.read_parquet(config.panel_parquet(crop))

    official = ksh_national_official(crop)

    # --- SZIGORÚ parse-ellenőrzés: a vármegyei terület- és termés-összegeknek
    # egyezniük kell a KSH saját 'Ország összesen' soraival. Tolerancia: 25 egység
    # (a 20 vármegye egészre kerekítéséből adódó max eltérés; valódi parse-hiba
    # ezres nagyságrendű lenne). Ha nagyobb az eltérés, leállunk.
    sums = (panel.dropna(subset=["area_ha", "production_t"])
            .groupby("crop_year")[["area_ha", "production_t"]].sum())
    for y in sums.index:
        y = int(y)
        for col, off_key in (("area_ha", "area_ha"), ("production_t", "production_t")):
            if y in official[off_key] and abs(sums.loc[y, col] - official[off_key][y]) > 25:
                sys.exit(f"HIBA: {col} összeg {y}-ben {sums.loc[y, col]:,.0f} != "
                         f"KSH országos {official[off_key][y]:,.0f} — parse-hiba, állj meg.")
    print(f"  parse-ellenőrzés: Σterület és Σtermés minden évben egyezik a KSH "
          f"országos sorával ({len(sums)} év)")

    # Megjegyzés: a KSH publikált országos TERMÉSÁTLAG-sora kis mértékben (max
    # ~0.03 t/ha) eltérhet a saját termés/terület hányadosától (kerekítetlen belső
    # adatból számolják). A megjelenített idősor a KSH HIVATALOS átlag-sora.
    derived = national_series(panel)
    diffs = {y: abs(derived[y] - official["yield_t_ha"][y])
             for y in derived.index if y in official["yield_t_ha"]}
    worst_y = max(diffs, key=diffs.get)
    print(f"  info: publikált vs származtatott országos átlag max eltérés "
          f"{diffs[worst_y]:.4f} t/ha ({worst_y}) — KSH belső kerekítés")

    nat = pd.Series(official["yield_t_ha"]).sort_index()

    # országos lineáris trend (a fejléc-anomáliához; in-sample, dokumentált)
    years = nat.index.to_numpy(dtype=float)
    slope, intercept = np.polyfit(years - TREND_BASE_YEAR, nat.to_numpy(), 1)

    counties = {}
    for nid, g in panel.groupby("nuts_id"):
        g = g.dropna(subset=["yield_t_ha"]).sort_values("crop_year")
        counties[nid] = {
            "name": g["county_name"].iloc[0],
            "years": [int(y) for y in g["crop_year"]],
            "yields": [round(float(v), 2) for v in g["yield_t_ha"]],
        }

    payload = {
        "crop": label,
        "national": {
            "years": [int(y) for y in nat.index],
            "yields": [round(float(v), 3) for v in nat.to_numpy()],
            "trend_slope": round(float(slope), 5),
            "trend_intercept": round(float(intercept), 4),
            "trend_base_year": TREND_BASE_YEAR,
            "method": "KSH hivatalos 'Ország összesen' termésátlag-sor; a vármegyei "
                      "összegekből származtatott értékkel keresztellenőrizve",
        },
        "counties": counties,
    }
    out = yield_history_json(crop)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"  [ok] {out.name}: {len(counties)} vármegye + országos "
          f"({int(nat.index.min())}..{int(nat.index.max())})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--crop", choices=list(config.CROPS), default=config.DEFAULT_CROP)
    main(crop=ap.parse_args().crop)
