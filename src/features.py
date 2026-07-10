"""Származtatott időjárási mutatók (Fázis 3).

Termésévenként és vármegyénként, a config.PHENOLOGY_WINDOWS ablakaival:
  - GDD (0 °C bázis): teljes szezon + ablakonként
  - Hőstressznapok: Tmax > 30 °C a szemtelítődés alatt (máj 1 – jún 20)
  - Csapadékösszeg ablakonként
  - Téli fagynapok: Tmin < -15 °C (dec – feb)
  - Vízmérleg (precip - et0, halmozott): tavaszi (bokrosodás) és szemtelítődési ablak

Kimenet: data/processed/features.parquet, kulcs (nuts_id, crop_year).

Futtatás:  python -m src.features
"""
from __future__ import annotations

import sys
from datetime import date

import numpy as np
import pandas as pd

from src import config
from src.build_panel import WEATHER_DAILY_PARQUET

FEATURES_PARQUET = config.DATA_PROCESSED / "features.parquet"


def window_dates(crop_year: int, window: tuple[int, int, int, int, int]) -> tuple[date, date]:
    """Egy fenológiai ablak konkrét kezdő/záró dátuma egy adott termésévre.

    Az ablak (m1, d1, m2, d2, év_eltolás). A kezdő év = termésév + eltolás;
    ha a záró hónap kisebb a kezdőnél, az ablak átnyúlik a következő naptári évbe
    (pl. téli nyugalom: dec (Y-1) – feb (Y)).
    """
    m1, d1, m2, d2, off = window
    start_year = crop_year + off
    end_year = start_year if m2 >= m1 else start_year + 1
    return date(start_year, m1, d1), date(end_year, m2, d2)


def _window_slice(df: pd.DataFrame, crop_year: int,
                  window: tuple[int, int, int, int, int]) -> pd.DataFrame:
    start, end = window_dates(crop_year, window)
    return df[(df["date"] >= start) & (df["date"] <= end)]


def compute_features(daily: pd.DataFrame) -> pd.DataFrame:
    """Feature tábla minden (nuts_id, crop_year) párra a napi időjárásból."""
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.date

    rows: list[dict] = []
    for (nuts_id, cy), g in daily.dropna(subset=["crop_year"]).groupby(["nuts_id", "crop_year"]):
        cy = int(cy)
        # a csoport a teljes termésévi ablak (Y-1 okt 1 – Y jún 30)
        gdd = np.maximum(g["temperature_2m_mean"], config.GDD_BASE_C)
        row = {
            "nuts_id": nuts_id,
            "crop_year": cy,
            "gdd_total": gdd.sum(),
            "prec_total": g["precipitation_sum"].sum(),
        }
        for name, win in config.PHENOLOGY_WINDOWS.items():
            w = _window_slice(g, cy, win)
            row[f"gdd_{name}"] = np.maximum(w["temperature_2m_mean"], config.GDD_BASE_C).sum()
            row[f"prec_{name}"] = w["precipitation_sum"].sum()

        # Hőstressznapok a szemtelítődés alatt
        gf = _window_slice(g, cy, config.PHENOLOGY_WINDOWS["grain_filling"])
        row["heat_days_grain_filling"] = int((gf["temperature_2m_max"]
                                              > config.HEAT_STRESS_TMAX_C).sum())
        # Téli fagynapok (dec – feb)
        wd = _window_slice(g, cy, config.PHENOLOGY_WINDOWS["winter_dormancy"])
        row["frost_days_winter"] = int((wd["temperature_2m_min"]
                                        < config.WINTER_FROST_TMIN_C).sum())
        # Vízmérleg (aszályjelző): tavaszi és szemtelítődési ablak
        for name in ("tillering", "grain_filling"):
            w = _window_slice(g, cy, config.PHENOLOGY_WINDOWS[name])
            row[f"wb_{name}"] = (w["precipitation_sum"]
                                 - w["et0_fao_evapotranspiration"]).sum()
        # Teljes termésévi halmozott vízmérleg — a több ablakon átívelő,
        # halmozódó szárazság (pl. 2022) jelzője. A modell ebből képez
        # konvex deficit-tagot (wb_deficit) a tanítóminta mediánjához képest.
        row["wb_total"] = (g["precipitation_sum"]
                           - g["et0_fao_evapotranspiration"]).sum()
        rows.append(row)

    return pd.DataFrame(rows).sort_values(["nuts_id", "crop_year"]).reset_index(drop=True)


def main() -> None:
    print("Feature engineering")
    if not WEATHER_DAILY_PARQUET.exists():
        sys.exit(f"HIBA: hiányzik {WEATHER_DAILY_PARQUET}. Futtasd: python -m src.build_panel")
    daily = pd.read_parquet(WEATHER_DAILY_PARQUET)
    feats = compute_features(daily)

    # csak a panelben szereplő teljes termésévek
    panel = pd.read_parquet(config.PANEL_PARQUET)
    feats = feats[feats["crop_year"].isin(panel["crop_year"].unique())]
    feats.to_parquet(FEATURES_PARQUET, index=False)
    print(f"  [ok] {FEATURES_PARQUET.name}: {len(feats)} sor, {feats.shape[1]} oszlop, "
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
    main()
