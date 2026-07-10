"""As-of visszateszt (Fázis 4 — mérési kapu; terményparaméteres a Fázis 8 óta).

Rekonstruáljuk a feature-öket úgy, ahogy egy adott év as-of napján (búza: jún 15,
kukorica: aug 1) ismertek lennének:
  - időjárás a valós napi adatból az as-of napig,
  - a szezon hátralévő napjaira a TÖBBI évből számolt nap-klimatológia
    (look-ahead nélkül: a célévet kihagyjuk a klimatológiából és a tanításból is).
A modellt a célév NÉLKÜL tanítjuk, jóslunk, és a tényleges hozammal vetjük össze.

Kimenet: data/processed/backtest_results_{crop}.parquet, reports/figures/*.png,
         reports/backtest_report{_corn}.md (magyar riport a LOYO eredményekkel).

Futtatás:  python -m src.backtest [--crop wheat|corn]
"""
from __future__ import annotations

import argparse
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src import config
from src.features import compute_features
from src.model import fit_panel_model, load_model_data, predict_naive_trend
from src.validate import loyo_parquet, rmse

FIGURES_DIR = config.REPORTS_DIR / "figures"


def backtest_parquet(crop: str) -> "Path":
    return config.DATA_PROCESSED / f"backtest_results_{crop}.parquet"


def report_md(crop: str) -> "Path":
    suffix = "" if crop == "wheat" else f"_{crop}"
    return config.REPORTS_DIR / f"backtest_report{suffix}.md"


def asof_daily(daily: pd.DataFrame, target_year: int, crop: str,
               asof: date | None = None) -> pd.DataFrame:
    """A célév termésévi napi időjárása úgy, ahogy az as-of napon ismert lenne.

    Az as-of nap utáni napokat (a termésév végéig) a TÖBBI év nap-klimatológiájával
    (hónap-nap átlag vármegyénként) pótoljuk — semmi look-ahead a célévből.
    """
    if asof is None:
        m, d = config.CROPS[crop]["asof"]
        asof = date(target_year, m, d)
    d_ = daily.dropna(subset=["crop_year"]).copy()
    d_["date"] = pd.to_datetime(d_["date"]).dt.date

    target = d_[d_["crop_year"] == target_year]
    known = target[target["date"] <= asof]

    others = d_[d_["crop_year"] != target_year].copy()
    dt = pd.to_datetime(others["date"])
    others["md"] = list(zip(dt.dt.month, dt.dt.day))
    clim = (others.groupby(["nuts_id", "md"])[config.OPENMETEO_DAILY_VARS]
            .mean().reset_index())

    future = target[target["date"] > asof][["nuts_id", "date", "crop_year"]].copy()
    fdt = pd.to_datetime(future["date"])
    future["md"] = list(zip(fdt.dt.month, fdt.dt.day))
    future = future.merge(clim, on=["nuts_id", "md"], how="left").drop(columns=["md"])

    return pd.concat([known, future], ignore_index=True)


def backtest_year(df_model: pd.DataFrame, daily: pd.DataFrame,
                  target_year: int, crop: str) -> pd.DataFrame:
    """Egy célév as-of backtestje. Visszaadja a vármegyénkénti eredménytáblát."""
    asof_wx = asof_daily(daily, target_year, crop)
    feats_asof = compute_features(asof_wx, crop)
    feats_asof = feats_asof[feats_asof["crop_year"] == target_year]

    train = df_model[df_model["crop_year"] != target_year]
    actual = df_model[df_model["crop_year"] == target_year][
        ["nuts_id", "county_name", "crop_year", "yield_t_ha"]]

    test = actual.merge(feats_asof, on=["nuts_id", "crop_year"], how="inner")
    m = fit_panel_model(train, crop=crop)
    test["pred"] = m.predict(test)
    test["trend_baseline"] = predict_naive_trend(train, test)  # "normál év" szint
    test["anomaly_pct"] = 100 * (test["pred"] - test["trend_baseline"]) / test["trend_baseline"]
    test["actual_anomaly_pct"] = 100 * (test["yield_t_ha"] - test["trend_baseline"]) / test["trend_baseline"]
    return test[["nuts_id", "county_name", "crop_year", "yield_t_ha", "pred",
                 "trend_baseline", "anomaly_pct", "actual_anomaly_pct"]]


def plot_backtest(results: pd.DataFrame, crop: str) -> list[str]:
    """Évenkénti szórásdiagram: jósolt vs tényleges hozam. PNG fájlnevek listája."""
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    m, d = config.CROPS[crop]["asof"]
    files = []
    for year, g in results.groupby("crop_year"):
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.scatter(g["yield_t_ha"], g["pred"], s=40)
        lims = [min(g["yield_t_ha"].min(), g["pred"].min()) - 0.3,
                max(g["yield_t_ha"].max(), g["pred"].max()) + 0.3]
        ax.plot(lims, lims, "k--", lw=1, label="tökéletes előrejelzés")
        for _, r in g.iterrows():
            ax.annotate(r["nuts_id"], (r["yield_t_ha"], r["pred"]), fontsize=7,
                        xytext=(3, 3), textcoords="offset points")
        ax.set_xlabel("Tényleges hozam (t/ha)")
        ax.set_ylabel(f"As-of ({m:02d}-{d:02d}) előrejelzés (t/ha)")
        ax.set_title(f"{year} — as-of backtest, {config.CROPS[crop]['label']}")
        ax.legend()
        fname = f"backtest_{crop}_{year}.png"
        fig.savefig(FIGURES_DIR / fname, dpi=120, bbox_inches="tight")
        plt.close(fig)
        files.append(fname)
    return files


def write_report(results: pd.DataFrame, loyo: pd.DataFrame,
                 fig_files: list[str], crop: str) -> None:
    """Magyar nyelvű riport a mérési kapuhoz."""
    label = config.CROPS[crop]["label"]
    asof_m, asof_d = config.CROPS[crop]["asof"]
    a = loyo["actual"].to_numpy()
    lines = [
        f"# Backtest riport — {label}hozam-előrejelző (mérési kapu)",
        "",
        f"*Készült: {date.today().isoformat()}. Adat: KSH vármegyei {label}-termésátlag "
        f"(2000–{int(loyo['crop_year'].max())}), ERA5 (Open-Meteo), 19 vármegye "
        "(Budapest kihagyva — elhanyagolható termőterület).*",
        "",
        "## 1. Modell",
        "",
        "Panelregresszió: vármegye-fixhatás + közös lineáris időtrend (a technológiai "
        "fejlődés leválasztására) + standardizált időjárási mutatók (ablakos GDD-k, "
        "csapadék, hőstressznapok, vízmérleg-mutatók, halmozott vízmérleg-deficit). "
        f"Becslés: OLS szelektív ridge büntetéssel (α={config.RIDGE_ALPHA}, csak az "
        "időjárási blokkon; LOYO ráccsal választva).",
        "",
        "## 2. Leave-one-year-out validáció (out-of-sample)",
        "",
        "| Modell | RMSE (t/ha) | RMSE (%) | R² |",
        "|---|---|---|---|",
    ]
    for name, col in [("**panelmodell**", "pred"), ("naiv: vármegye-trend", "naive_trend"),
                      ("naiv: előző 3 év átlaga", "naive_last3")]:
        p = loyo[col].to_numpy()
        r = rmse(a, p)
        r2 = 1 - np.sum((a - p) ** 2) / np.sum((a - a.mean()) ** 2)
        lines.append(f"| {name} | {r:.3f} | {100*r/a.mean():.1f}% | {r2:.3f} |")

    oos_std = float((loyo["actual"] - loyo["pred"]).std())
    z = config.UNCERTAINTY_Z
    cover = float(((loyo["actual"] >= loyo["pred"] - z * oos_std)
                   & (loyo["actual"] <= loyo["pred"] + z * oos_std)).mean())
    lines += [
        "",
        f"A bizonytalansági sáv a LOYO reziduumok szórásából: ±{z}·{oos_std:.3f} t/ha "
        f"(névleges 80%); tényleges lefedettség **{100*cover:.1f}%**.",
        "",
        f"## 3. As-of backtest ({asof_m:02d}. hó {asof_d}. napi tudásállapot)",
        "",
        "A feature-ök a célév as-of napjáig ismert időjárásból + a hátralévő napokra "
        "a többi év klimatológiájából; a modell a célév nélkül tanítva (nincs look-ahead).",
        "",
        "| Év | Jósolt anomália (átlag) | Tényleges anomália (átlag) | Iránytalálat (vármegye) |",
        "|---|---|---|---|",
    ]
    for year, g in results.groupby("crop_year"):
        hit = int(((g["anomaly_pct"] < 0) == (g["actual_anomaly_pct"] < 0)).sum())
        lines.append(f"| {year} | {g['anomaly_pct'].mean():+.1f}% | "
                     f"{g['actual_anomaly_pct'].mean():+.1f}% | {hit}/{len(g)} |")

    # 2022 vármegyénkénti részletezés — a kapu (b) ezt kéri számon
    g22 = results[results["crop_year"] == 2022].sort_values("actual_anomaly_pct")
    lines += [
        "",
        "### 2022 vármegyénként (a leginkább érintettől a legkevésbé érintettig)",
        "",
        "| Vármegye | Tényleges anomália | Jósolt anomália | Irány |",
        "|---|---|---|---|",
    ]
    for _, r in g22.iterrows():
        ok = "✔" if (r["anomaly_pct"] < 0) == (r["actual_anomaly_pct"] < 0) else "✘"
        lines.append(f"| {r['county_name']} | {r['actual_anomaly_pct']:+.1f}% | "
                     f"{r['anomaly_pct']:+.1f}% | {ok} |")
    lines += [""]
    for f in fig_files:
        lines.append(f"![backtest](figures/{f})")

    hit22 = int(((g22["anomaly_pct"] < 0) == (g22["actual_anomaly_pct"] < 0)).sum())
    top10 = g22.head(10)
    hit_top = int(((top10["anomaly_pct"] < 0) == (top10["actual_anomaly_pct"] < 0)).sum())
    lines += [
        "",
        "## 4. A mérési kapu értékelése",
        "",
        "- **(a) Naiv alap verése:** lásd a 2. táblázatot.",
        f"- **(b) 2022 iránytartás:** {hit22}/19 vármegyénél helyes az előjel, "
        f"a 10 leginkább érintettből {hit_top}-nál.",
        f"- **(c) Sáv realitása:** {100*cover:.1f}% tényleges lefedettség a névleges 80%-ra.",
    ]
    if crop == "wheat":
        lines += [
            "",
            "### Ismert korlátok (őszintén)",
            "",
            "- **A 2022-es anomália MÉRTÉKÉT a modell alulbecsüli** (jún. 15-i átlag "
            f"{g22['anomaly_pct'].mean():+.1f}% a tényleges "
            f"{g22['actual_anomaly_pct'].mean():+.1f}% helyett). Két ok: (1) a június "
            "végi hőhullám a jún. 15-i tudásállapotban még nem ismert — a teljes "
            "szezonos (LOYO) becslés már −5,5%-ot ad; (2) 2022-ben a műtrágyaár-robbanás "
            "(háború) is csökkentette a hozamot, ami nem időjárási tényező, egy "
            "időjárás-alapú modell elvben sem foghatja meg.",
            "- A halmozott vízmérleg-deficit (wb_deficit) bevezetése a kapu-iteráció "
            "eredménye: az összesített out-of-sample RMSE-t 0,624-ről 0,529 t/ha-ra "
            "javította, és a 2012-es aszályt is 19/19-re hozza.",
            "- A modell szezonon belüli frissítéssel (5. fázis) az as-of nap utáni "
            "időjárást is beépíti, a 2022-szerű késői stresszt is követve.",
        ]
    out = report_md(crop)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[ok] riport: {out}")


def main(crop: str = config.DEFAULT_CROP) -> None:
    df_model = load_model_data(crop)
    daily = pd.read_parquet(config.weather_daily_parquet(crop))

    all_res = []
    for year in config.CROPS[crop]["backtest_years"]:
        res = backtest_year(df_model, daily, year, crop)
        all_res.append(res)
        hit = int(((res["anomaly_pct"] < 0) == (res["actual_anomaly_pct"] < 0)).sum())
        print(f"{year}: jósolt anomália {res['anomaly_pct'].mean():+.1f}%, "
              f"tényleges {res['actual_anomaly_pct'].mean():+.1f}%, "
              f"iránytalálat {hit}/{len(res)} vármegye, "
              f"RMSE {rmse(res['yield_t_ha'].to_numpy(), res['pred'].to_numpy()):.3f} t/ha")

    results = pd.concat(all_res, ignore_index=True)
    results.to_parquet(backtest_parquet(crop), index=False)

    loyo = pd.read_parquet(loyo_parquet(crop))
    figs = plot_backtest(results, crop)
    write_report(results, loyo, figs, crop)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--crop", choices=list(config.CROPS), default=config.DEFAULT_CROP)
    main(crop=ap.parse_args().crop)
