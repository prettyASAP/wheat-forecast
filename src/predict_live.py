"""Napi élő előrejelzés (Fázis 5).

A futó termésévre (okt–jún: az adott szezon; júl–szept: az épp betakarított)
összerakja a napi időjárást három rétegből:
  1. historikus tanítóadat (weather_daily.parquet) — ami már megvan,
  2. Open-Meteo archive — a szezonból hiányzó rész ~1 hétig ezelőttig,
  3. Open-Meteo forecast (past_days + forecast_days) — a közelmúlt és a jövő hét,
  4. a szezon hátralévő napjaira: vármegyénkénti nap-klimatológia (25 év átlaga).
Ebből as-of feature-öket számol, a teljes panelen tanított modellel jósol
anomáliát és 80%-os sávot (a LOYO out-of-sample szórásból).

Kimenet: web/data/forecast.json + web/data/history/YYYY-MM-DD.json snapshot.

Futtatás:  python -m src.predict_live
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src import config
from src.build_panel import WEATHER_DAILY_PARQUET, assign_crop_year
from src.features import compute_features
from src.fetch_weather import CENTROIDS_CSV, fetch_county, get_daily
from src.model import fit_panel_model, load_model_data, predict_naive_trend

FORECAST_JSON = config.WEB_DATA / "forecast.json"
HISTORY_DIR = config.WEB_DATA / "history"
ARCHIVE_LAG_DAYS = 7   # az ERA5 archive kb. 5 nap késéssel teljes


def current_crop_year(today: date) -> int:
    """okt–dec -> következő év termése; jan–szept -> az idei termés(év)."""
    return today.year + 1 if today.month >= 10 else today.year


def season_window(crop_year: int) -> tuple[date, date]:
    return date(crop_year - 1, 10, 1), date(crop_year, 6, 30)


def build_season_daily(today: date, crop_year: int) -> pd.DataFrame:
    """A futó termésév napi időjárása vármegyénként, 4 rétegből (lásd modul-doc)."""
    start, end = season_window(crop_year)
    hist = pd.read_parquet(WEATHER_DAILY_PARQUET)
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
            # forecast API: past_days lefedi az archive-lag rést, plusz pár nap előre
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

    combined["crop_year"] = assign_crop_year(pd.Series(combined["date"]))
    known_until = have_index.get_level_values("date").max()
    print(f"  szezon-napok: {combined.groupby('nuts_id')['date'].count().iloc[0]} / vármegye, "
          f"ismert időjárás eddig: {known_until}, klimatológiával pótolt napok: {n_missing_days}")
    return combined, known_until


def main() -> None:
    today = date.today()
    crop_year = current_crop_year(today)
    start, end = season_window(crop_year)
    print(f"Élő előrejelzés — {crop_year}-es termésév ({start} .. {end}), ma: {today}")

    season_daily, known_until = build_season_daily(today, crop_year)
    feats = compute_features(season_daily)
    feats = feats[feats["crop_year"] == crop_year]
    if len(feats) != 20:
        sys.exit(f"HIBA: {len(feats)} vármegyére van feature, várt 20.")

    # modell a teljes panelen; a sáv a LOYO out-of-sample szórásból
    df = load_model_data()
    m = fit_panel_model(df)
    summary = json.loads((config.DATA_PROCESSED / "loyo_summary.json").read_text())
    oos_std, z = summary["oos_std"], config.UNCERTAINTY_Z

    counties = pd.read_parquet(config.PANEL_PARQUET)[
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
            "heat_days_grain_filling": int(f["heat_days_grain_filling"]),
            "frost_days_winter": int(f["frost_days_winter"]),
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
                "note": "nincs becslés — elhanyagolható búzaterület",
            })

    payload = {
        "crop": "búza",
        "crop_year": crop_year,
        "updated_at": today.isoformat(),
        "weather_known_until": str(known_until),
        "unit": "t/ha",
        "band": "80%",
        "counties": rows,
    }
    config.WEB_DATA.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    FORECAST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    (HISTORY_DIR / f"{today.isoformat()}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"  [ok] {FORECAST_JSON} + history snapshot")

    # --- Fázis-záró ellenőrzés ---
    est = [r for r in rows if r["predicted_yield_t_ha"] is not None]
    problems = []
    if len(rows) != 20:
        problems.append(f"{len(rows)} sor, várt 20")
    if len(est) != 19:
        problems.append(f"{len(est)} becslés, várt 19")
    for r in est:
        if not (1.5 <= r["predicted_yield_t_ha"] <= 9.0):
            problems.append(f"{r['nuts_id']} hozam kilóg: {r['predicted_yield_t_ha']}")
        if abs(r["anomaly_pct"]) > 45:
            problems.append(f"{r['nuts_id']} anomália kilóg: {r['anomaly_pct']}%")
    if problems:
        sys.exit("HIBA az élő előrejelzés ellenőrzésén: " + "; ".join(problems))
    anoms = [r["anomaly_pct"] for r in est]
    print(f"  ellenőrzés OK: 19 becslés + Budapest; anomália "
          f"{min(anoms):+.1f}% .. {max(anoms):+.1f}%, "
          f"országos átlag {np.mean(anoms):+.1f}%")


if __name__ == "__main__":
    main()
