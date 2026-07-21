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
    """A 'futó' termésév: szezon közben az aktuális, utána a most zárult.

    Éven belüli szezonnál (kukorica, ápr–szept) a szezonkezdet ELŐTT (jan–márc)
    az előző év zárult szezonja a 'futó' — az idei még el sem kezdődött, arra
    nincs értelmezhető becslés (audit-javítás: korábban itt üres időjárással
    összeomlott).
    """
    start_m, end_m = config.CROPS[crop]["season"]
    if start_m > end_m:  # évhatáron átnyúló (búza, árpa)
        return today.year + 1 if today.month >= start_m else today.year
    return today.year - 1 if today.month < start_m else today.year


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
    # üres ismert-index esetén (elvileg a crop_year-javítás után nem fordul elő)
    # dátumot adunk, ne NaN-t — a downstream összehasonlítások dátumot várnak
    known_until = (have_index.get_level_values("date").max()
                   if len(have_index) else start - timedelta(days=1))
    print(f"  szezon-napok: {combined.groupby('nuts_id')['date'].count().iloc[0]} / vármegye, "
          f"ismert időjárás eddig: {known_until}, klimatológiával pótolt napok: {n_missing_days}")
    return combined, known_until


def scenario_ensemble(season_daily: pd.DataFrame, known_until, crop: str,
                      crop_year: int, m, df_model: pd.DataFrame) -> dict | None:
    """Analóg-év együttes: a szezon hátralévő napjait a historikus évek TÉNYLEGES
    időjárásával pótoljuk (évenként egy pálya), mindegyikre jóslunk.

    Eredmény: (szcenárió-payload, vármegyénkénti együttes-átlag) — az utóbbi a
    fő becslés szezon közben (lásd a return előtti megjegyzést).
    Ha a szezon már lezárult (nincs hátralévő nap), (None, None).
    """
    start, end = season_window(crop_year, crop)
    if known_until >= end:
        return None, None, None

    hist = pd.read_parquet(config.weather_daily_parquet(crop))
    hist["date"] = pd.to_datetime(hist["date"]).dt.date
    hist = hist.dropna(subset=["crop_year"])
    analog_years = sorted(int(y) for y in hist["crop_year"].unique()
                          if y != crop_year)

    known = season_daily[season_daily["date"] <= known_until]
    cols = ["nuts_id", "date", *config.OPENMETEO_DAILY_VARS]

    # a hátralévő naptári napok (month, day) párjai
    remaining_days = [d for d in pd.date_range(known_until, end, freq="D").date
                      if d > known_until]
    rem_md = {(d.month, d.day): d for d in remaining_days}

    # historikus napok indexelése (nuts_id, month, day, crop_year) szerint
    hdt = pd.to_datetime(hist["date"])
    hist = hist.assign(_m=hdt.dt.month, _d=hdt.dt.day)

    # klimatológia tartaléknak (pl. feb 29 hiányzik az analóg évből)
    clim = (hist.groupby(["nuts_id", "_m", "_d"])[config.OPENMETEO_DAILY_VARS]
            .mean().reset_index())

    preds_by_analog = {}
    for ay in analog_years:
        h = hist[hist["crop_year"] == ay]
        tail = h[[( m_, d_) in rem_md for m_, d_ in zip(h["_m"], h["_d"])]].copy()
        tail["date"] = [rem_md[(m_, d_)] for m_, d_ in zip(tail["_m"], tail["_d"])]
        # hiányzó (nuts_id, nap) párok pótlása klimatológiával
        have = set(zip(tail["nuts_id"], tail["date"]))
        need = [(n, d) for n in known["nuts_id"].unique() for d in remaining_days
                if (n, d) not in have]
        if need:
            fill = pd.DataFrame(need, columns=["nuts_id", "date"])
            fill["_m"] = [d.month for d in fill["date"]]
            fill["_d"] = [d.day for d in fill["date"]]
            fill = fill.merge(clim, on=["nuts_id", "_m", "_d"], how="left")
            tail = pd.concat([tail, fill], ignore_index=True)
        spliced = pd.concat([known[cols], tail[cols]], ignore_index=True)
        spliced["crop_year"] = crop_year

        feats = compute_features(spliced, crop)
        feats = feats[feats["nuts_id"].isin(m.counties)].copy()
        feats["crop_year"] = crop_year
        feats = feats.sort_values("nuts_id")
        preds_by_analog[ay] = pd.Series(m.predict(feats),
                                        index=feats["nuts_id"].values)

    ens = pd.DataFrame(preds_by_analog)  # sor: vármegye, oszlop: analóg év

    # országos pálya analóg évenként (a legutóbbi ismert évi területekkel)
    last_year = int(df_model["crop_year"].max())
    areas = (df_model[df_model["crop_year"] == last_year]
             .set_index("nuts_id")["area_ha"]).reindex(ens.index)
    nat_paths = (ens.mul(areas, axis=0).sum() / areas.sum())

    def pcts(s):
        return {f"p{p}": round(float(np.percentile(s, p)), 2) for p in (10, 50, 90)}

    payload = {
        "method": f"{len(analog_years)} analóg év tényleges időjárása a hátralévő "
                  f"{len(remaining_days)} napra",
        "remaining_days": len(remaining_days),
        "national": pcts(nat_paths),
        "counties": {nid: pcts(ens.loc[nid]) for nid in ens.index},
    }
    # Az együttes-átlag a korrekt középbecslés szezon közben: a modell nemlineáris.
    # A wb_deficit = min(wb − medián, 0) affin tagok minimuma → KONKÁV a wb-ben,
    # pozitív együtthatóval, ezért E[f(időjárás)] <= f(E[időjárás]) — a klimatológiai
    # átlag-időjárásból számolt becslés FELFELÉ torzítana (Jensen-egyenlőtlenség
    # konkáv függvényre). Az együttes tényleges átlaga ezt a torzítást elkerüli.
    # A szórás a hátralévő időjárás bizonytalansága (a sáv-kombinációhoz).
    return payload, ens.mean(axis=1), ens.std(axis=1, ddof=1)


def _load_price(crop: str) -> dict | None:
    """A termény legutolsó hivatalos termelői ára a prices.json-ból (vagy None)."""
    prices_path = config.WEB_DATA / "prices.json"
    if not prices_path.exists():
        return None
    return json.loads(prices_path.read_text(encoding="utf-8"))["crops"].get(crop)


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
    wf = json.loads((config.DATA_PROCESSED /
                     f"walkforward_summary_{crop}.json").read_text())
    sigma_nat = wf["rmse_wf_national"]  # az ORSZÁGOS becslés walk-forward szórása
    # ORSZÁGOS 80%-os sáv: a becslés + a standardizált empirikus kvantilisek ×
    # az országos szórás (a vármegyei sávval azonos módszertan, országos szinten).
    # A szakértői panel (agrárprofesszor) fő kérése: a bizonytalanság a SZÁM
    # MELLÉ kerüljön, ne csak a lábjegyzetbe.
    out = {
        "predicted_yield_t_ha": round(predicted, 2),
        "pred_low_t_ha": round(predicted + wf["q10"] * sigma_nat, 2),
        "pred_high_t_ha": round(predicted + wf["q90"] * sigma_nat, 2),
        "anomaly_pct": round(anomaly_pct, 1),
        "trend_t_ha": round(trend_now, 2),
        "yoy_pct": round(100 * (predicted - last_official) / last_official, 1),
        "prev_year": int(nat["years"][-1]),
        "prev_year_yield_t_ha": last_official,
        "rank_from_worst": weaker + 1,
        "rank_total": n_total,
        "weights": f"{last_year}. évi vármegyei betakarított területek, Budapest nélkül",
        # a bizalmi kérdésre ("mekkorát szokott tévedni?"): a WALK-FORWARD
        # (csak múltból jósolt) hiba a trend-szint százalékában — testületi
        # döntés: ez az őszinte szám, nem a LOYO. Az ORSZÁGOS fejléc mellé az
        # ORSZÁGOS (terület-súlyozott) walk-forward RMSE tartozik, NEM a
        # vármegyei pooled RMSE (az utóbbi túlbecsülné az országos szám hibáját,
        # mert aggregáláskor a vármegyei tévedések részben kioltják egymást —
        # matematikai audit 5.1).
        "model_error_pct": round(100 * sigma_nat / trend_now, 1),
    }

    # Forintosítás (ha van árfájl): termelési érték és a trendtől való elmaradás
    # értéke, a LEGUTÓBBI elérhető évi termelői átlagáron (az ár-évjárat jelölve).
    # Mértékegységek: (t/ha) x (ha) = t; t x (HUF/t) = HUF; /1e9 = mrd HUF.
    prices = _load_price(crop)
    if True:
        if prices:
            area_total = float(w.sum())
            price = prices["latest_huf_per_t"]
            value_bn = predicted * area_total * price / 1e9
            trend_gap_bn = (predicted - trend_now) * area_total * price / 1e9
            out["value"] = {
                "price_huf_per_t": price,
                "price_year": prices["latest_year"],
                "area_ha": round(area_total),
                "production_value_bn_huf": round(value_bn, 1),
                "trend_gap_bn_huf": round(trend_gap_bn, 1),
                "note": (f"a {prices['latest_year']}. évi termelői átlagáron, "
                         f"a {last_year}. évi területtel számolva"),
            }
    return out


def main(crop: str = config.DEFAULT_CROP) -> None:
    spec = config.CROPS[crop]
    # TREND-alapú termények (napraforgó, repce): a mérési kapu elutasította az
    # időjárás-modellt, ezért a becslés a sokéves trend, a sáv a trend tényleges
    # walk-forward hibájából. Nincs időjárás-vezérelt anomália és szcenárió —
    # így a kimenet hiteles (nincs igazolatlan időjárás-állítás).
    is_trend = spec.get("method") == "trend"
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

    # FRISSESSÉG-ŐR (programozói audit 1.): ha a szezon MÉG TART, a legfrissebb
    # ismert időjárásnak közel kell lennie a mai naphoz. Ha az Open-Meteo forecast
    # csendben degradálódik (érvényes JSON, de kevés/nulla jövőbeli nappal), a
    # "mért" ablak észrevétlenül összezsugorodna, a klimatológiával pótolt rész
    # megnőne — riasztás nélkül. Ezt inkább leállítjuk, mint hogy elavult
    # alapon számolt becslést publikáljunk.
    STALE_LIMIT_DAYS = 8
    if not is_trend and today <= end and (today - known_until).days > STALE_LIMIT_DAYS:
        sys.exit(f"HIBA: a szezon még tart (vége {end}), de a legfrissebb ismert "
                 f"időjárás {known_until} ({(today - known_until).days} napja) — "
                 f"az adatforrás valószínűleg degradálódott. Nem publikálunk "
                 f"elavult alapon számolt becslést.")

    # A kijelzett "időjárás eddig" mutatók CSAK a ténylegesen ismert napokból
    # (audit-javítás: korábban a klimatológiával szezonvégig feltöltött sorból
    # számoltuk, így pl. a csapadék részben szintetikus jövőt tartalmazott).
    known_only = season_daily[season_daily["date"] <= known_until]
    feats_todate = compute_features(known_only, crop)
    feats_todate = feats_todate[feats_todate["crop_year"] == crop_year]

    df = load_model_data(crop)
    m = fit_panel_model(df, crop=crop)
    # M2 (testületi csomag): a sáv a walk-forward reziduumok EMPIRIKUS
    # kvantiliseiből, vármegyénkénti zsugorított szórással — aszimmetrikus.
    wf = json.loads((config.DATA_PROCESSED /
                     f"walkforward_summary_{crop}.json").read_text())
    q10, q90 = wf["q10"], wf["q90"]
    sigma_by_county = wf["sigma_by_county"]
    sigma_pooled = wf["sigma_pooled"]
    oos_std = sigma_pooled  # a szcenárió-kombinációhoz (σ_w mellé)
    z = max(abs(q10), q90)  # ellenőrzési tolerancia: a sáv szélesebb oldala

    counties = pd.read_parquet(config.panel_parquet(crop))[
        ["nuts_id", "county_name"]].drop_duplicates()
    rows = []
    model_feats = feats[feats["nuts_id"].isin(m.counties)].copy()
    model_feats["crop_year"] = crop_year
    preds = m.predict(model_feats)
    baseline = predict_naive_trend(df, model_feats)

    if is_trend:
        # trend-alapú: a becslés MAGA a trend (anomália = 0, „a szokásos szint
        # körül"); nincs időjárás-szcenárió. A sáv a trend-summary-ből (lásd wf).
        preds = baseline
        sc, ens_mean, ens_std = None, None, None
    else:
        # Szezon közben a fő becslés az analóg-együttes átlaga (Jensen-korrekció,
        # lásd scenario_ensemble); lezárt szezonnál a valós időjárásos becslés.
        sc, ens_mean, ens_std = scenario_ensemble(season_daily, known_until, crop,
                                                  crop_year, m, df)
        if ens_mean is not None:
            preds = ens_mean.reindex(model_feats["nuts_id"].values).to_numpy()

    # vármegyénkénti sáv: modell-hiba + (szezon közben) a hátralévő időjárás
    # bizonytalansága, függetlenként kombinálva: sigma_tot = sqrt(m^2 + w^2)
    # (audit-észrevétel: a csak-modell sáv szezon közben alulbecsülte a teljeset)
    def _sigma_tot(nid: str) -> float:
        # vármegyei zsugorított modell-hiba + (szezon közben) a hátralévő
        # időjárás szórása, függetlenként kombinálva
        s_c = float(sigma_by_county.get(nid, sigma_pooled))
        if ens_std is not None and nid in ens_std.index:
            return float(np.sqrt(s_c ** 2 + float(ens_std[nid]) ** 2))
        return s_c

    # vármegyénkénti forintosítás (érthetőség-teszt kérése: a döntéshozó első
    # kérdése megyei szinten is a pénz) — a legutóbbi ismert évi területtel és a
    # legutolsó hivatalos árral, mint az országos blokkban
    last_year = int(df["crop_year"].max())
    county_area = df[df["crop_year"] == last_year].set_index("nuts_id")["area_ha"]
    price_info = _load_price(crop)

    pred_map = dict(zip(model_feats["nuts_id"], zip(preds, baseline)))
    for _, c in counties.sort_values("nuts_id").iterrows():
        f = feats_todate[feats_todate["nuts_id"] == c["nuts_id"]].iloc[0]
        wx = {
            "prec_total_mm": round(float(f["prec_total"]), 1),
            "wb_total_mm": round(float(f["wb_total"]), 1),
            "heat_days": int(f["heat_days"]),
            "frost_days_winter": int(f["frost_days_winter"]) if spec["use_frost"] else None,
            "gdd_total": round(float(f["gdd_total"]), 0),
        }
        if c["nuts_id"] in pred_map:  # Budapest kimarad a modellből
            p, b = pred_map[c["nuts_id"]]
            row = {
                "nuts_id": c["nuts_id"], "county_name": c["county_name"],
                "predicted_yield_t_ha": round(float(p), 2),
                "anomaly_pct": round(100 * (p - b) / b, 1),
                "low": round(float(p + q10 * _sigma_tot(c["nuts_id"])), 2),
                "high": round(float(p + q90 * _sigma_tot(c["nuts_id"])), 2),
                "weather_todate": wx,
            }
            if price_info and c["nuts_id"] in county_area.index:
                area = float(county_area[c["nuts_id"]])
                price = price_info["latest_huf_per_t"]
                row["value_bn_huf"] = round(p * area * price / 1e9, 1)
                row["trend_gap_bn_huf"] = round((p - b) * area * price / 1e9, 1)
            rows.append(row)
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
        "method": spec.get("method", "weather"),
        "national": national_block(crop, crop_year, rows, df),
        "scenarios": sc,
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
    # szcenárió-ellenőrzés: P10<=P50<=P90, és a P50 közel a pontbecsléshez
    # (mindkettő "átlagos folytatás" — a klimatológiai átlag vs analóg-medián
    # eltérése kicsi kell legyen a modell-sávhoz képest)
    sc = payload["scenarios"]
    if sc is not None:
        pred_by_id = {r["nuts_id"]: r["predicted_yield_t_ha"] for r in est}
        for nid, p in sc["counties"].items():
            if not (p["p10"] <= p["p50"] <= p["p90"]):
                problems.append(f"{nid} szcenárió-percentilisek nem monotonok: {p}")
            if nid in pred_by_id and abs(p["p50"] - pred_by_id[nid]) > z * oos_std:
                problems.append(f"{nid} szcenárió-P50 ({p['p50']}) messze a "
                                f"pontbecsléstől ({pred_by_id[nid]})")
    if problems:
        sys.exit("HIBA az élő előrejelzés ellenőrzésén: " + "; ".join(problems))
    if sc is not None:
        n = sc["national"]
        print(f"  szcenáriók ({sc['method']}): országos P10 {n['p10']} / "
              f"P50 {n['p50']} / P90 {n['p90']} t/ha")
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
