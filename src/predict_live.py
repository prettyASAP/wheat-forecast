"""Napi élő előrejelzés (Fázis 5; terményparaméteres a Fázis 8 óta).

A futó termésévre összerakja a napi időjárást négy rétegből:
  1. historikus tanítóadat (weather_daily_{crop}.parquet),
  2. Open-Meteo archive — a szezonból hiányzó rész ~1 hétig ezelőttig,
  3. Open-Meteo forecast (past_days + forecast_days) — a közelmúlt és a jövő hét,
  4. a szezon hátralévő napjaira: vármegyénkénti nap-klimatológia (25 év átlaga).
Ebből as-of feature-öket számol, a teljes panelen tanított modellel jósol
anomáliát és 80%-os sávot (a LOYO out-of-sample szórásból).

Futó termésév: búzánál okt–jún a szezon (júl–szept: az épp betakarított évet
mutatjuk); kukoricánál ápr–szept (okt–márc: az előző szezon záró becslése).

Kimenet: web/data/forecast_{crop}.json + web/data/history/{crop}/YYYY-MM-DD.json.

Futtatás:  python -m src.predict_live [--crop wheat|corn]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src import config
from src.build_panel import assign_crop_year
from src.features import compute_features
from src.fetch_weather import CENTROIDS_CSV, fetch_county, get_daily
from src.model import fit_panel_model, load_model_data, predict_naive_trend
from src.validate import loyo_summary_json

ARCHIVE_LAG_DAYS = 7   # az ERA5 archive kb. 5 nap késéssel teljes


def forecast_json(crop: str):
    return config.WEB_DATA / f"forecast_{crop}.json"


def history_dir(crop: str):
    return config.WEB_DATA / "history" / crop


def current_crop_year(today: date, crop: str) -> int:
    """A 'futó' termésév: szezon közben az aktuális, utána a most zárult."""
    start_m, end_m = config.CROPS[crop]["season"]
    if start_m > end_m:  # évhatáron átnyúló (búza)
        return today.year + 1 if today.month >= start_m else today.year
    return today.year    # naptári éven belüli (kukorica): szezon előtt/után is az idei


def season_window(crop_year: int, crop: str) -> tuple[date, date]:
    start_m, end_m = config.CROPS[crop]["season"]
    start_y = crop_year - 1 if start_m > end_m else crop_year
    # a záró hónap utolsó napja
    end = (pd.Timestamp(crop_year, end_m, 1) + pd.offsets.MonthEnd(0)).date()
    return date(start_y, start_m, 1), end


def build_season_daily(today: date, crop_year: int, crop: str):
    """A futó termésév napi időjárása vármegyénként, 4 rétegből (lásd modul-doc)."""
    start, end = season_window(crop_year, crop)
    hist = pd.read_parquet(config.weather_daily_parquet(crop))
    hist["date"] = pd.to_datetime(hist["date"]).dt.date

    centroids = pd.read_csv(CENTROIDS_CSV)
    cols = ["nuts_id", "date", *config.OPENMETEO_DAILY_VARS]

    # 1. réteg: ami a historikus táblában megvan a szezonból
    season = hist[(hist["date"] >= start) & (hist["date"] <= end)][cols]

    # 2-3. réteg: a hiányzó rész letöltése (archive + forecast)
    have_max = season["date"].max() if len(season) else start - timedelta(days=1)
    fetch_from = have_max + timedelta(days=1)
    archive_to = min(today - timedelta(days=ARCHIVE_LAG_DAYS), end)

    frames = [season]
    if fetch_from <= end and fetch_from <= today:
        print(f"  élő letöltés: {fetch_from} .. {min(end, today)} (20 vármegye)")
        for row in centroids.itertuples(index=False):
            parts = []
            if fetch_from <= archive_to:
                parts.append(fetch_county(row.lat, row.lon,
                                          str(fetch_from), str(archive_to)))
            past_needed = (today - max(fetch_from, archive_to + timedelta(days=1))).days
            if max(fetch_from, archive_to + timedelta(days=1)) <= end:
                fc = get_daily(config.OPENMETEO_FORECAST_URL, {
                    "latitude": row.lat, "longitude": row.lon,
                    "timezone": config.OPENMETEO_TIMEZONE,
                    "daily": ",".join(config.OPENMETEO_DAILY_VARS),
                    "past_days": min(max(past_needed, 0) + 1, 92),
                    "forecast_days": 7,
                })
                fc = fc[(fc["date"] > archive_to) & (fc["date"] <= end)]
                parts.append(fc)
            if parts:
                live = pd.concat(parts, ignore_index=True)
                live = live[(live["date"] >= fetch_from) & (live["date"] <= end)]
                live.insert(0, "nuts_id", row.nuts_id)
                frames.append(live[cols])

    combined = pd.concat(frames, ignore_index=True).dropna(
        subset=config.OPENMETEO_DAILY_VARS)
    combined = combined.drop_duplicates(subset=["nuts_id", "date"], keep="first")

    # 4. réteg: klimatológia a szezon még hiányzó napjaira
    clim_src = hist.dropna(subset=["crop_year"]).copy()
    dt = pd.to_datetime(clim_src["date"])
    clim_src["md"] = list(zip(dt.dt.month, dt.dt.day))
    clim = (clim_src.groupby(["nuts_id", "md"])[config.OPENMETEO_DAILY_VARS]
            .mean().reset_index())

    all_days = pd.date_range(start, end, freq="D").date
    full_index = pd.MultiIndex.from_product(
        [centroids["nuts_id"], all_days], names=["nuts_id", "date"])
    have_index = pd.MultiIndex.from_frame(combined[["nuts_id", "date"]])
    missing = full_index.difference(have_index).to_frame(index=False)
    n_missing_days = missing["date"].nunique()
    if len(missing):
        missing["md"] = [(d.month, d.day) for d in missing["date"]]
        missing = missing.merge(clim, on=["nuts_id", "md"], how="left").drop(columns=["md"])
        combined = pd.concat([combined, missing[cols]], ignore_index=True)

    combined["crop_year"] = assign_crop_year(pd.Series(combined["date"]), crop)
    known_until = have_index.get_level_values("date").max()
    print(f"  szezon-napok: {combined.groupby('nuts_id')['date'].count().iloc[0]} / vármegye, "
          f"ismert időjárás eddig: {known_until}, klimatológiával pótolt napok: {n_missing_days}")
    return combined, known_until


def national_block(crop: str, crop_year: int, rows: list[dict],
                   df_model: pd.DataFrame) -> dict:
    """Országos fejléc-mutatók a forecast JSON-ba.

    - predicted: a 19 modellezett vármegye becslése a LEGUTÓBBI ismert évi
      betakarított területtel súlyozva (az idei terület még nem ismert;
      Budapest kimarad — részaránya < 0.1%, dokumentált elhanyagolás).
    - anomaly_pct: a KSH hivatalos országos idősorra illesztett lineáris trend
      extrapolációjához képest (yield_history JSON-ból, export_history generálja).
    - yoy_pct: az előző év hivatalos országos átlagához képest.
    - percentile: az idei trend-anomália helye a historikus anomáliák közt.
    """
    from src.export_history import yield_history_json
    hist = json.loads(yield_history_json(crop).read_text(encoding="utf-8"))
    nat = hist["national"]

    # terület-súlyok: a legutóbbi ismert év vármegyei területei
    last_year = int(df_model["crop_year"].max())
    areas = (df_model[df_model["crop_year"] == last_year]
             .set_index("nuts_id")["area_ha"])
    est = {r["nuts_id"]: r["predicted_yield_t_ha"] for r in rows
           if r["predicted_yield_t_ha"] is not None}
    w = areas.reindex(est.keys()).dropna()
    predicted = float(sum(est[c] * w[c] for c in w.index) / w.sum())

    # trend-extrapoláció és historikus anomáliák a hivatalos idősoron
    t = crop_year - nat["trend_base_year"]
    trend_now = nat["trend_intercept"] + nat["trend_slope"] * t
    anomaly_pct = 100 * (predicted - trend_now) / trend_now
    hist_anoms = []
    for y, v in zip(nat["years"], nat["yields"]):
        tr = nat["trend_intercept"] + nat["trend_slope"] * (y - nat["trend_base_year"])
        hist_anoms.append(100 * (v - tr) / tr)
    weaker = sum(1 for a in hist_anoms if a < anomaly_pct)
    n_total = len(hist_anoms) + 1

    last_official = nat["yields"][-1]
    return {
        "predicted_yield_t_ha": round(predicted, 2),
        "anomaly_pct": round(anomaly_pct, 1),
        "trend_t_ha": round(trend_now, 2),
        "yoy_pct": round(100 * (predicted - last_official) / last_official, 1),
        "prev_year": int(nat["years"][-1]),
        "prev_year_yield_t_ha": last_official,
        "rank_from_worst": weaker + 1,
        "rank_total": n_total,
        "weights": f"{last_year}. évi vármegyei betakarított területek, Budapest nélkül",
    }


def main(crop: str = config.DEFAULT_CROP) -> None:
    spec = config.CROPS[crop]
    today = date.today()
    crop_year = current_crop_year(today, crop)
    start, end = season_window(crop_year, crop)
    print(f"Élő előrejelzés — {spec['label']}, {crop_year}-es termésév "
          f"({start} .. {end}), ma: {today}")

    season_daily, known_until = build_season_daily(today, crop_year, crop)
    feats = compute_features(season_daily, crop)
    feats = feats[feats["crop_year"] == crop_year]
    if len(feats) != 20:
        sys.exit(f"HIBA: {len(feats)} vármegyére van feature, várt 20.")

    df = load_model_data(crop)
    m = fit_panel_model(df, crop=crop)
    summary = json.loads(loyo_summary_json(crop).read_text())
    oos_std, z = summary["oos_std"], config.UNCERTAINTY_Z

    counties = pd.read_parquet(config.panel_parquet(crop))[
        ["nuts_id", "county_name"]].drop_duplicates()
    rows = []
    model_feats = feats[feats["nuts_id"].isin(m.counties)].copy()
    model_feats["crop_year"] = crop_year
    preds = m.predict(model_feats)
    baseline = predict_naive_trend(df, model_feats)

    pred_map = dict(zip(model_feats["nuts_id"], zip(preds, baseline)))
    for _, c in counties.sort_values("nuts_id").iterrows():
        f = feats[feats["nuts_id"] == c["nuts_id"]].iloc[0]
        wx = {
            "prec_total_mm": round(float(f["prec_total"]), 1),
            "wb_total_mm": round(float(f["wb_total"]), 1),
            "heat_days": int(f["heat_days"]),
            "frost_days_winter": int(f["frost_days_winter"]) if spec["use_frost"] else None,
            "gdd_total": round(float(f["gdd_total"]), 0),
        }
        if c["nuts_id"] in pred_map:  # Budapest kimarad a modellből
            p, b = pred_map[c["nuts_id"]]
            rows.append({
                "nuts_id": c["nuts_id"], "county_name": c["county_name"],
                "predicted_yield_t_ha": round(float(p), 2),
                "anomaly_pct": round(100 * (p - b) / b, 1),
                "low": round(float(p - z * oos_std), 2),
                "high": round(float(p + z * oos_std), 2),
                "weather_todate": wx,
            })
        else:
            rows.append({
                "nuts_id": c["nuts_id"], "county_name": c["county_name"],
                "predicted_yield_t_ha": None, "anomaly_pct": None,
                "low": None, "high": None, "weather_todate": wx,
                "note": "nincs becslés — elhanyagolható termőterület",
            })

    payload = {
        "crop": spec["label"],
        "crop_year": crop_year,
        "updated_at": today.isoformat(),
        "weather_known_until": str(known_until),
        "unit": "t/ha",
        "band": "80%",
        "national": national_block(crop, crop_year, rows, df),
        "counties": rows,
    }
    hdir = history_dir(crop)
    hdir.mkdir(parents=True, exist_ok=True)
    forecast_json(crop).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    (hdir / f"{today.isoformat()}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    dates = sorted(p.stem for p in hdir.glob("????-??-??.json"))
    (hdir / "index.json").write_text(json.dumps(dates), encoding="utf-8")
    print(f"  [ok] {forecast_json(crop).name} + history snapshot "
          f"({len(dates)} nap az indexben)")

    # --- Fázis-záró ellenőrzés ---
    est = [r for r in rows if r["predicted_yield_t_ha"] is not None]
    problems = []
    if len(rows) != 20:
        problems.append(f"{len(rows)} sor, várt 20")
    if len(est) != 19:
        problems.append(f"{len(est)} becslés, várt 19")
    lo, hi = (1.5, 9.0) if crop == "wheat" else (2.0, 12.0)
    for r in est:
        if not (lo <= r["predicted_yield_t_ha"] <= hi):
            problems.append(f"{r['nuts_id']} hozam kilóg: {r['predicted_yield_t_ha']}")
        if abs(r["anomaly_pct"]) > 50:
            problems.append(f"{r['nuts_id']} anomália kilóg: {r['anomaly_pct']}%")
    # az országos becslésnek a vármegyei becslések tartományába kell esnie
    nat = payload["national"]
    preds = [r["predicted_yield_t_ha"] for r in est]
    if not (min(preds) <= nat["predicted_yield_t_ha"] <= max(preds)):
        problems.append(f"országos becslés ({nat['predicted_yield_t_ha']}) a vármegyei "
                        f"tartományon kívül [{min(preds)}, {max(preds)}]")
    if problems:
        sys.exit("HIBA az élő előrejelzés ellenőrzésén: " + "; ".join(problems))
    anoms = [r["anomaly_pct"] for r in est]
    print(f"  ellenőrzés OK: 19 becslés + Budapest; vármegyei anomália "
          f"{min(anoms):+.1f}% .. {max(anoms):+.1f}%")
    print(f"  országos: {nat['predicted_yield_t_ha']} t/ha, trendhez {nat['anomaly_pct']:+.1f}%, "
          f"YoY {nat['yoy_pct']:+.1f}%, a {nat['rank_total']} évből a "
          f"{nat['rank_from_worst']}. leggyengébb")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--crop", choices=list(config.CROPS), default=config.DEFAULT_CROP)
    main(crop=ap.parse_args().crop)
