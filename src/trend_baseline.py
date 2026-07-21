"""Trend-alapú becslés a mérési kapun ÁT NEM jutó terményekhez (napraforgó, repce).

A hozam ingadozását az általunk mért időjárás ezeknél nem magyarázza megbízhatóan:
a walk-forward szerint a NAIV TREND (vármegye-fixhatás + közös évtrend) veri az
időjárás-modellt (napraforgó 0,41 vs 0,44; repce 0,53 vs 0,64 t/ha). Ezért ezeknél
NEM az időjárás-modellt publikáljuk, hanem a sokéves trendet — őszintén felcímkézve.

A sáv és a „tipikus tévedés" itt is a tényleges, CSAK-MÚLTBÓL-jósolt hibából jön,
ugyanazzal a walk-forward protokollal (2011-től), csak a naiv trend modellre. Így a
kimenet hiteles: nincs benne igazolatlan időjárás-állítás, de a bizonytalanság
számszerű és validált.

Kimenet: data/processed/walkforward_summary_{crop}.json  ("method": "trend").
Futtatás:  python -m src.trend_baseline
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from src import config
from src.model import load_model_data, predict_naive_trend
from src.validate import rmse
from src.walkforward import WF_START, band_calibration, national_wf_rmse

# mely termények kezelendők trend-alapon (a mérési kapu döntése alapján)
TREND_CROPS = [c for c, s in config.CROPS.items() if s.get("method") == "trend"]


def trend_walk_forward(df: pd.DataFrame) -> pd.DataFrame:
    """Expanding-window naiv-trend előrejelzés: minden t tesztévre CSAK a t előtti
    évekből, ugyanúgy, ahogy élesben (a trend extrapolál, nem interpolál)."""
    rows = []
    for t in range(WF_START, int(df["crop_year"].max()) + 1):
        train = df[df["crop_year"] < t]
        test = df[df["crop_year"] == t]
        if test.empty or len(train) < 40:
            continue
        pred = predict_naive_trend(train, test)
        for i, (_, r) in enumerate(test.iterrows()):
            rows.append({"crop_year": t, "nuts_id": r["nuts_id"],
                         "actual": r["yield_t_ha"], "pred": float(pred[i])})
    return pd.DataFrame(rows)


def build_trend_summary(crop: str) -> dict:
    df = load_model_data(crop)
    res = trend_walk_forward(df)
    calib = band_calibration(res)
    rmse_wf = rmse(res["actual"], res["pred"])
    rmse_nat = national_wf_rmse(res, df)
    summary = {
        "method": "trend",
        "winner": "trend",
        "rmse_wf": rmse_wf,
        "rmse_wf_national": rmse_nat,
        "rmse_naive_wf": rmse_wf,          # a trend a naiv alap maga
        "mean_yield": float(res["actual"].mean()),
        "test_years": [WF_START, int(df["crop_year"].max())],
        **calib,
    }
    (config.DATA_PROCESSED / f"walkforward_summary_{crop}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{config.CROPS[crop]['label']:10s}: trend walk-forward RMSE {rmse_wf:.3f} "
          f"(országos {rmse_nat:.3f}) | sáv q10={calib['q10']:.2f} q90={calib['q90']:.2f} "
          f"| lefedettség {100*calib['coverage_overall']:.0f}%")
    return summary


def main() -> None:
    crops = TREND_CROPS or ["sunflower", "rapeseed"]
    for crop in crops:
        build_trend_summary(crop)


if __name__ == "__main__":
    main()
