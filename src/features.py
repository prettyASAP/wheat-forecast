"""Származtatott időjárási mutatók (Fázis 3, terményparaméteres a Fázis 8 óta).

Termésévenként és vármegyénként, a CROPS[crop]["phenology"] ablakaival:
  - GDD (0 °C bázis): teljes szezon + ablakonként
  - Hőstressznapok: Tmax > küszöb a kritikus ablakban (búza: szemtelítődés >30 °C,
    kukorica: virágzás >32 °C)
  - Csapadékösszeg ablakonként
  - Téli fagynapok: Tmin < -15 °C (csak őszi vetésű terménynél)
  - Vízmérleg (precip - et0): a termény kritikus ablakaira + teljes szezonra

Kimenet: data/processed/features_{crop}.parquet, kulcs (nuts_id, crop_year).

Futtatás:  python -m src.features [--crop wheat|corn]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

import numpy as np
import pandas as pd

from src import config


def window_dates(crop_year: int, window: tuple[int, int, int, int, int]) -> tuple[date, date]:
    """Egy fenológiai ablak konkrét kezdő/záró dátuma egy adott termésévre.

    Az ablak (m1, d1, m2, d2, év_eltolás). A kezdő év = termésév + eltolás;
    ha a záró hónap kisebb a kezdőnél, az ablak átnyúlik a következő naptári évbe
    (pl. búza téli nyugalom: dec (Y-1) – feb (Y)).
    """
    m1, d1, m2, d2, off = window
    start_year = crop_year + off
    end_year = start_year if m2 >= m1 else start_year + 1
    return date(start_year, m1, d1), date(end_year, m2, d2)


def _window_slice(df: pd.DataFrame, crop_year: int,
                  window: tuple[int, int, int, int, int]) -> pd.DataFrame:
    start, end = window_dates(crop_year, window)
    return df[(df["date"] >= start) & (df["date"] <= end)]


def compute_features(daily: pd.DataFrame, crop: str = config.DEFAULT_CROP) -> pd.DataFrame:
    """Feature tábla minden (nuts_id, crop_year) párra a napi időjárásból."""
    spec = config.CROPS[crop]
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.date

    rows: list[dict] = []
    for (nuts_id, cy), g in daily.dropna(subset=["crop_year"]).groupby(["nuts_id", "crop_year"]):
        cy = int(cy)
        # a csoport a teljes termésévi ablak
        gdd = np.maximum(g["temperature_2m_mean"], config.GDD_BASE_C)
        row = {
            "nuts_id": nuts_id,
            "crop_year": cy,
            "gdd_total": gdd.sum(),
            "prec_total": g["precipitation_sum"].sum(),
        }
        for name, win in spec["phenology"].items():
            w = _window_slice(g, cy, win)
            row[f"gdd_{name}"] = np.maximum(w["temperature_2m_mean"], config.GDD_BASE_C).sum()
            row[f"prec_{name}"] = w["precipitation_sum"].sum()

        # Hőstressznapok a termény kritikus ablakában
        hw = _window_slice(g, cy, spec["phenology"][spec["heat_window"]])
        row["heat_days"] = int((hw["temperature_2m_max"] > spec["heat_tmax_c"]).sum())
        # Téli fagynapok (csak őszi vetésű terménynél)
        if spec["use_frost"]:
            wd = _window_slice(g, cy, spec["phenology"]["winter_dormancy"])
            row["frost_days_winter"] = int((wd["temperature_2m_min"]
                                            < config.WINTER_FROST_TMIN_C).sum())
        # Vízmérleg (aszályjelző): kritikus ablakok + teljes szezon halmozva.
        # A wb_total-ból a modell konvex deficit-tagot képez (wb_deficit).
        for name in spec["wb_windows"]:
            w = _window_slice(g, cy, spec["phenology"][name])
            row[f"wb_{name}"] = (w["precipitation_sum"]
                                 - w["et0_fao_evapotranspiration"]).sum()
        row["wb_total"] = (g["precipitation_sum"]
                           - g["et0_fao_evapotranspiration"]).sum()
        rows.append(row)

    return pd.DataFrame(rows).sort_values(["nuts_id", "crop_year"]).reset_index(drop=True)


def main(crop: str = config.DEFAULT_CROP) -> None:
    print(f"Feature engineering — {config.CROPS[crop]['label']}")
    wx_path = config.weather_daily_parquet(crop)
    if not wx_path.exists():
        sys.exit(f"HIBA: hiányzik {wx_path}. Futtasd: python -m src.build_panel --crop {crop}")
    daily = pd.read_parquet(wx_path)
    feats = compute_features(daily, crop)

    # csak a panelben szereplő teljes termésévek
    panel = pd.read_parquet(config.panel_parquet(crop))
    feats = feats[feats["crop_year"].isin(panel["crop_year"].unique())]
    out = config.features_parquet(crop)
    feats.to_parquet(out, index=False)
    print(f"  [ok] {out.name}: {len(feats)} sor, {feats.shape[1]} oszlop, "
          f"termésévek {feats['crop_year'].min()}..{feats['crop_year'].max()}")

    # --- Fázis-záró ellenőrzés ---
    problems = []
    if feats[["nuts_id", "crop_year"]].duplicated().sum():
        problems.append("duplikált kulcs")
    if feats.drop(columns=["nuts_id"]).isna().sum().sum():
        problems.append("NaN a feature-ökben")
    expected = 20 * panel["crop_year"].nunique()
    if len(feats) != expected:
        problems.append(f"{len(feats)} sor, várt {expected}")
    if problems:
        sys.exit("HIBA a feature-ellenőrzésen: " + "; ".join(problems))
    print("  ellenőrzés: teljes tábla, nincs NaN/duplikátum")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--crop", choices=list(config.CROPS), default=config.DEFAULT_CROP)
    main(crop=ap.parse_args().crop)
