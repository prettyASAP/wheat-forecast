"""Leave-one-year-out keresztvalidáció (Fázis 4 — mérési kapu első fele).

Mindig egy teljes évet kihagyunk a tanításból, arra jósolunk, minden évre
ismételve — így minden előrejelzés valóban nem látott évre szól.
Összevetés a naiv alapokkal (vármegye-trend, előző 3 év átlaga), és a
modellváltozatok (trend fok x ridge alpha) rács-összehasonlítása.

Kimenet: data/processed/loyo_results.parquet (soronként egy out-of-sample
előrejelzés a fő modellel), + konzol-összefoglaló.

Futtatás:  python -m src.validate
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import config
from src.model import (fit_panel_model, load_model_data,
                       predict_naive_last3, predict_naive_trend)

LOYO_PARQUET = config.DATA_PROCESSED / "loyo_results.parquet"


def loyo_predict(df: pd.DataFrame, **model_kw) -> pd.DataFrame:
    """LOYO: minden évre a többi évből tanított modell előrejelzése."""
    out = []
    for year in sorted(df["crop_year"].unique()):
        train, test = df[df["crop_year"] != year], df[df["crop_year"] == year]
        m = fit_panel_model(train, **model_kw)
        out.append(pd.DataFrame({
            "nuts_id": test["nuts_id"].values,
            "crop_year": year,
            "actual": test["yield_t_ha"].values,
            "pred": m.predict(test),
        }))
    return pd.concat(out, ignore_index=True)


def rmse(a: np.ndarray, p: np.ndarray) -> float:
    return float(np.sqrt(np.mean((a - p) ** 2)))


def main() -> None:
    df = load_model_data()
    years = sorted(df["crop_year"].unique())
    print(f"LOYO keresztvalidáció: {len(df)} megfigyelés, {len(years)} év")

    # --- Modellváltozat-rács ---
    print("\n=== Változatok (out-of-sample RMSE, t/ha) ===")
    grid_results = {}
    for deg in (1, 2):
        for alpha in (0.0, 5.0, 10.0, 25.0):
            r = loyo_predict(df, trend_degree=deg, ridge_alpha=alpha)
            grid_results[(deg, alpha)] = rmse(r["actual"], r["pred"])
            print(f"  trend fok {deg}, ridge {alpha:5.1f}: {grid_results[(deg, alpha)]:.3f}")
    best_deg, best_alpha = min(grid_results, key=grid_results.get)
    print(f"  -> legjobb: trend fok {best_deg}, ridge {best_alpha} "
          f"(config: {config.TREND_DEGREE}, {config.RIDGE_ALPHA})")

    # --- Fő modell (config szerinti) vs naiv alapok ---
    res = loyo_predict(df)
    naive_t, naive_3 = [], []
    for year in years:
        train, test = df[df["crop_year"] != year], df[df["crop_year"] == year]
        naive_t.append(predict_naive_trend(train, test))
        # a "előző 3 év" naivnak a teljes df-et adjuk historikumként, de csak
        # a year ELŐTTI éveket használja -> nincs look-ahead
        naive_3.append(predict_naive_last3(df, test))
    res["naive_trend"] = np.concatenate(naive_t)
    res["naive_last3"] = np.concatenate(naive_3)

    a = res["actual"].to_numpy()
    print("\n=== Fő modell vs naiv alapok (out-of-sample) ===")
    for name, col in [("panelmodell", "pred"), ("naiv: vármegye-trend", "naive_trend"),
                      ("naiv: előző 3 év átlaga", "naive_last3")]:
        p = res[col].to_numpy()
        r = rmse(a, p)
        r2 = 1 - np.sum((a - p) ** 2) / np.sum((a - a.mean()) ** 2)
        print(f"  {name:26s} RMSE {r:.3f} t/ha ({100*r/a.mean():.1f}%)  R2 {r2:.3f}")

    # vármegyénkénti RMSE a fő modellre
    print("\n=== Vármegyénkénti RMSE (fő modell) ===")
    by_c = res.groupby("nuts_id").apply(
        lambda g: rmse(g["actual"].to_numpy(), g["pred"].to_numpy()), include_groups=False)
    print(by_c.round(3).to_string())

    # bizonytalansági sáv kalibrációja: out-of-sample reziduum szórás -> 80% sáv
    oos_std = float((res["actual"] - res["pred"]).std())
    z = config.UNCERTAINTY_Z
    cover = float(((res["actual"] >= res["pred"] - z * oos_std)
                   & (res["actual"] <= res["pred"] + z * oos_std)).mean())
    print(f"\nOut-of-sample reziduum szórás: {oos_std:.3f} t/ha")
    print(f"80%-os sáv (±{z}·szórás) tényleges lefedettsége: {100*cover:.1f}%")

    res.attrs = {}
    res.to_parquet(LOYO_PARQUET, index=False)
    pd.Series({"oos_std": oos_std, "coverage": cover}).to_json(
        config.DATA_PROCESSED / "loyo_summary.json")
    print(f"\n[ok] {LOYO_PARQUET.name} mentve")


if __name__ == "__main__":
    main()
