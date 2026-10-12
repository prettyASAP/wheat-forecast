"""HungaroMet állomási csapadék: mért ellenpróba a modellből számolt csapadék mellé.

Forrás: a HungaroMet Nonprofit Zrt. Meteorológiai Adattára (odp.met.hu), automata
állomások napi adatai ("recent": az év eleje óta, naponta frissül). A `rau` oszlop
a napi csapadékösszeg, az adott nap 06 UTC-től a következő nap 06 UTC-ig (mm);
a hiányzó érték -999 (Leiras_automata_napi-HABP_1D_akt-hu.pdf).

Felhasználási feltétel (ODP_altalanos_felhasznalasi_feltetelek.pdf): a forrást
fel kell tüntetni; átlagolt adatnál a feltételek saját példája szerint:
"Adatbázis: Meteorológiai Adattár, HungaroMet Nonprofit Zrt. átlagolva az egyes
értékekre". Ezt a szöveget írjuk ki a jelentésben.

Csak teljes idősorú állomás számít: ha egy állomásnak az időszak bármely napján
hiányzik az adata, az állomás kimarad (hiányt nem pótolunk, nem becslünk).
Megyei érték csak akkor, ha legalább MIN_STATIONS állomás teljes.

Az állomási nap 06 UTC-től másnap 06 UTC-ig tart, a modell napja éjféltől éjfélig.
Az összevetéshez ezért a modell ÓRÁS csapadékát is összegezzük pontosan az állomási
időablakra (window["model"]); különben egy éjszakai eső az egyik forrásban még
benne van, a másikban már nincs (2026. okt. 10–11.: 20 mm kontra 6 mm).

Kimenet: web/data/stations.json. Futtatás: python -m src.fetch_hungaromet
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta

import time
from datetime import timezone

import pandas as pd
import requests

from src import config
from src.predict_live import current_crop_year, season_window

BASE = "https://odp.met.hu/climate/observations_hungary/daily/"
SOURCE = "Adatbázis: Meteorológiai Adattár, HungaroMet Nonprofit Zrt. átlagolva az egyes értékekre"
MIN_STATIONS = 2
TIMEOUT = 60


def _d(yyyymmdd: int) -> date:
    return datetime.strptime(str(int(yyyymmdd)), "%Y%m%d").date()


def load_meta() -> pd.DataFrame:
    raw = requests.get(BASE + "station_meta_auto.csv", timeout=TIMEOUT).content
    m = pd.read_csv(io.BytesIO(raw), sep=";", skipinitialspace=True, encoding="utf-8")
    m.columns = [c.strip() for c in m.columns]
    for c in ("StationName", "RegioName"):
        m[c] = m[c].astype(str).str.strip()
    # egy állomásnak több (időszakos) sora is lehet: a legutolsót tartjuk meg
    return m.sort_values("EndDate").drop_duplicates("StationNumber", keep="last")


def load_station(sid: int) -> pd.DataFrame | None:
    r = requests.get(f"{BASE}recent/HABP_1D_{sid}_akt.zip", timeout=TIMEOUT)
    if r.status_code != 200:
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    txt = z.read(z.namelist()[0]).decode("utf-8").split("##Meta END")[-1]
    d = pd.read_csv(io.StringIO(txt), sep=";", skipinitialspace=True)
    d.columns = [c.strip() for c in d.columns]
    return d[["Time", "rau"]]


def county_of(regio: str) -> str:
    """A HungaroMet megyeneve a mi megyeneveinkre (Budapest kerületei egyben)."""
    return "Budapest" if regio.startswith("Budapest") else regio


def window_sums(series: dict, start: date, end: date, county: dict,
                model: dict | None = None) -> dict:
    """Állomásonkénti összeg a [start, end] napokra, csak hiánytalan állomással;
    megyei és országos (állomás-)átlag. A model ({megye: mm} ugyanarra az időablakra)
    esetén a modell megyei értéke és állomássúlyú országos átlaga is bekerül."""
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    per = {}
    for sid, s in series.items():
        vals = [s.get(d) for d in days]
        if all(v is not None and v >= 0 for v in vals):
            per[sid] = sum(vals)
    by_c: dict = {}
    for sid, v in per.items():
        by_c.setdefault(county[sid], []).append(v)
    counties = {c: {"mm": round(sum(v) / len(v), 1), "n": len(v)}
                for c, v in by_c.items() if len(v) >= MIN_STATIONS}
    nat = list(per.values())
    out = {"from": start.isoformat(), "to": end.isoformat(), "days": len(days),
           "national": {"mm": round(sum(nat) / len(nat), 1), "n": len(nat)} if nat else None,
           "counties": counties}
    if model:
        # országos: minden állomás a saját megyéje modellértékével (ugyanaz a mintavétel)
        mv = [model[county[sid]] for sid in per if county[sid] in model]
        if mv and len(mv) == len(per):
            out["model"] = {"national": round(sum(mv) / len(mv), 1),
                            "counties": {c: round(model[c], 1) for c in counties if c in model}}
    return out


def _hourly(url: str, params: dict) -> pd.Series:
    """Open-Meteo órás csapadék (UTC) újrapróbálással."""
    for k in range(5):
        try:
            r = requests.get(url, params={**params, "hourly": "precipitation", "timezone": "UTC"},
                             timeout=TIMEOUT)
            if r.status_code == 429:
                time.sleep(30 * (k + 1)); continue
            r.raise_for_status()
            j = r.json()["hourly"]
            return pd.Series(j["precipitation"], index=pd.to_datetime(j["time"], utc=True)).dropna()
        except Exception:  # hálózat, hibás vagy hiányos JSON (pl. "hourly": null)
            time.sleep(10 * (k + 1))
    raise RuntimeError(f"Open-Meteo órás lekérés sikertelen: {url}")


def model_window_mm(start: date, end: date, today: date) -> dict | None:
    """A modell csapadéka megyénként PONTOSAN az állomási időablakra:
    [start 06 UTC, end+1 06 UTC). Régebbi rész: ERA5 archívum, a friss rész
    (az archívum késése miatt): időjárási modellek, mint a napi adatnál."""
    from src.fetch_weather import CENTROIDS_CSV
    from src.predict_live import ARCHIVE_LAG_DAYS
    t0 = datetime.combine(start, datetime.min.time(), timezone.utc) + timedelta(hours=6)
    t1 = datetime.combine(end + timedelta(days=1), datetime.min.time(), timezone.utc) + timedelta(hours=6)
    archive_to = today - timedelta(days=ARCHIVE_LAG_DAYS)
    out = {}
    try:
        for r in pd.read_csv(CENTROIDS_CSV).itertuples(index=False):
            parts = []
            if start <= archive_to:
                parts.append(_hourly(config.OPENMETEO_ARCHIVE_URL, {
                    "latitude": r.lat, "longitude": r.lon,
                    "models": config.OPENMETEO_MODEL,  # ugyanaz az ERA5, mint a becslés bemenete
                    "start_date": start.isoformat(), "end_date": min(end + timedelta(days=1), archive_to).isoformat()}))
            fc_from = max(start, archive_to + timedelta(days=1))
            if fc_from <= end + timedelta(days=1):  # csak ha van az archívumon túli rész
                parts.append(_hourly(config.OPENMETEO_FORECAST_URL, {
                    "latitude": r.lat, "longitude": r.lon,
                    "start_date": fc_from.isoformat(),
                    "end_date": (end + timedelta(days=1)).isoformat()}))
            h = pd.concat(parts)
            h = h[~h.index.duplicated(keep="first")].sort_index()  # az archívum az elsőbbségi
            # az Open-Meteo órás csapadéka a MEGELŐZŐ óra összege: a (t0, t1] időbélyegek
            # fedik le pontosan a [06 UTC, másnap 06 UTC) állomási időszakot
            h = h[(h.index > t0) & (h.index <= t1)]
            need = int((t1 - t0).total_seconds() // 3600)
            if len(h) < need:  # hiányos órasor: nem adunk ki összevetést
                print(f"  [info] {r.name_latn}: {len(h)}/{need} óra, a modell-összevetés kimarad")
                return None
            out[r.name_latn] = float(h.sum())
    except Exception as e:  # az összevetés hibája nem állíthatja meg az állomási adatot
        print(f"  [info] {e} — a modell-összevetés kimarad")
        return None
    return out


def main() -> None:
    today = date.today()
    meta = load_meta()
    active = meta[meta["EndDate"] >= int((today - timedelta(days=5)).strftime("%Y%m%d"))]
    series, county, last_days = {}, {}, []
    for _, st in active.iterrows():
        try:
            d = load_station(int(st["StationNumber"]))
        except Exception as e:  # egy állomás hibája nem állítja meg a többit
            print(f"  [kimarad] {st['StationName']}: {e}")
            continue
        if d is None or d.empty:
            continue
        s = {_d(t): float(v) for t, v in zip(d["Time"], d["rau"])}
        series[int(st["StationNumber"])] = s
        county[int(st["StationNumber"])] = county_of(st["RegioName"])
        valid = [k for k, v in s.items() if v >= 0]
        if valid:
            last_days.append(max(valid))
    if len(series) < 100 or not last_days:
        sys.exit(f"HIBA: csak {len(series)} állomás jött le – a meglévő fájlt nem írjuk felül.")
    # az időszak vége: a legtöbb állomásnál meglévő utolsó nap (közös nap)
    last = Counter(last_days).most_common(1)[0][0]
    windows = {}
    for crop in config.REPORT_CROPS:
        start, _ = season_window(current_crop_year(today, crop), crop)
        if start <= last and start.isoformat() not in windows:
            w = window_sums(series, start, last, county, model_window_mm(start, last, today))
            # a 'recent' fájl január 1-jén indul: az ennél korábbi kezdetű ablakban
            # nincs hiánytalan állomás, ilyenkor nem adunk ki (üres) ablakot
            if w["national"]:
                windows[start.isoformat()] = w
    out = {"updated_at": today.isoformat(), "last_day": last.isoformat(),
           "source": SOURCE, "stations_downloaded": len(series), "windows": windows}
    (config.WEB_DATA / "stations.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, w in windows.items():
        n = w["national"]
        print(f"[ok] stations.json: {k} – {w['to']}: országos állomásátlag {n['mm'] if n else '?'} mm "
              f"({n['n'] if n else 0} állomás), {len(w['counties'])} megye")


if __name__ == "__main__":
    main()
