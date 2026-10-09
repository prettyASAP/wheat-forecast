"""HungaroMet állomási csapadék: mért ellenpróba a modell napi csapadéka mellé.

Forrás: a HungaroMet Nonprofit Zrt. Meteorológiai Adattára (odp.met.hu), automata
állomások napi adatai ("recent": az év eleje óta, naponta frissül). A `rau` oszlop
a napi csapadékösszeg, az adott nap 06 UTC-től a következő nap 06 UTC-ig (mm);
a hiányzó érték -999 (Leiras_automata_napi-HABP_1D_akt-hu.pdf).

Felhasználási feltétel (ODP_altalanos_felhasznalasi_feltetelek.pdf): a letöltött
adat VÁLTOZTATÁS NÉLKÜL szabadon felhasználható, a forrás feltüntetésével
("Adatbázis: Meteorológiai Adattár, HungaroMet Nonprofit Zrt."); a változtatáshoz
(pl. átlagoláshoz) előzetes írásbeli hozzájárulás kell. Ezért itt NINCS átlag és
összeg: fókuszmegyénként egy megnevezett állomás napi értékeit közöljük úgy,
ahogy a HungaroMet kiadta. Az állomás a megye középpontjához (a modell pontjához)
legközelebbi, a vizsgált napokra hiánytalan állomás.

Kimenet: web/data/stations.json. Futtatás: python -m src.fetch_hungaromet
"""
from __future__ import annotations

import io
import json
import math
import sys
import zipfile
from datetime import date, datetime, timedelta

import pandas as pd
import requests

from src import config

BASE = "https://odp.met.hu/climate/observations_hungary/daily/"
SOURCE = "Adatbázis: Meteorológiai Adattár, HungaroMet Nonprofit Zrt."
KEEP_DAYS = 14      # ennyi napot mentünk (a jelentés ebből a legutolsó 7-et mutatja)
CHECK_DAYS = 7      # ezekre a napokra kell hiánytalannak lennie a választott állomásnak
TIMEOUT = 60


def _d(yyyymmdd) -> date:
    return datetime.strptime(str(int(yyyymmdd)), "%Y%m%d").date()


def load_meta() -> pd.DataFrame:
    raw = requests.get(BASE + "station_meta_auto.csv", timeout=TIMEOUT).content
    m = pd.read_csv(io.BytesIO(raw), sep=";", skipinitialspace=True, encoding="utf-8")
    m.columns = [c.strip() for c in m.columns]
    for c in ("StationName", "RegioName"):
        m[c] = m[c].astype(str).str.strip()
    return m.sort_values("EndDate").drop_duplicates("StationNumber", keep="last")


def load_station(sid: int) -> dict | None:
    """dátum -> rau, ahogy a HungaroMet közli (-999 = adathiány)."""
    r = requests.get(f"{BASE}recent/HABP_1D_{sid}_akt.zip", timeout=TIMEOUT)
    if r.status_code != 200:
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    txt = z.read(z.namelist()[0]).decode("utf-8").split("##Meta END")[-1]
    d = pd.read_csv(io.StringIO(txt), sep=";", skipinitialspace=True)
    d.columns = [c.strip() for c in d.columns]
    return {_d(t): float(v) for t, v in zip(d["Time"], d["rau"])}


def published(v):
    """A közölt érték változtatás nélkül; a -999 (adathiány) és a hiányzó nap null.
    (A 0,0 mm valódi mért érték, nem hiány.)"""
    return None if v is None or math.isnan(v) or v < 0 else v


def km(lat1, lon1, lat2, lon2) -> float:
    """Gömbi távolság (km), az állomás és a megye középpontja között."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return 6371.0 * math.acos(max(-1.0, min(1.0, c)))


def pick_station(cands: list, last: date) -> dict | None:
    """A legközelebbi állomás, amelynek a [last-CHECK_DAYS+1, last] napokra
    minden értéke megvan (nem -999). cands: [{..., "km", "series"}] távolság szerint."""
    days = [last - timedelta(days=i) for i in range(CHECK_DAYS)]
    for c in sorted(cands, key=lambda x: x["km"]):
        s = c["series"]
        if all(s.get(d) is not None and s[d] >= 0 for d in days):
            return c
    return None


def main() -> None:
    today = date.today()
    meta = load_meta()
    cent = pd.read_csv(config.RAW_WEATHER / "_centroids.csv")
    active = meta[meta["EndDate"] >= int((today - timedelta(days=5)).strftime("%Y%m%d"))]
    out_st, lasts = [], []
    for county in config.REPORT_FOCUS_COUNTIES:
        c0 = cent[cent["name_latn"] == county]
        if c0.empty:
            continue
        lat0, lon0 = float(c0.iloc[0]["lat"]), float(c0.iloc[0]["lon"])
        cands = []
        for _, st in active[active["RegioName"] == county].iterrows():
            try:
                s = load_station(int(st["StationNumber"]))
            except Exception as e:  # egy állomás hibája nem állítja meg a többit
                print(f"  [kimarad] {st['StationName']}: {e}")
                continue
            if not s:
                continue
            valid = [k for k, v in s.items() if v >= 0]
            if valid:
                lasts.append(max(valid))
            cands.append({"county": county, "name": st["StationName"],
                          "number": int(st["StationNumber"]),
                          "km": km(lat0, lon0, float(st["Latitude"]), float(st["Longitude"])),
                          "series": s})
        out_st.append(cands)
    if not lasts:
        sys.exit("HIBA: egyetlen állomás sem jött le – a meglévő fájlt nem írjuk felül.")
    last = max(set(lasts), key=lasts.count)  # a legtöbb állomásnál meglévő utolsó nap
    stations = []
    for cands in out_st:
        c = pick_station(cands, last)
        if not c:
            continue
        days = [last - timedelta(days=i) for i in range(KEEP_DAYS - 1, -1, -1)]
        stations.append({
            "county": c["county"], "name": c["name"], "number": c["number"],
            "distance_km": round(c["km"], 1),
            # a közölt érték változtatás nélkül; -999 (adathiány) helyett null
            "rau": {d.isoformat(): published(c["series"].get(d)) for d in days},
        })
    if not stations:
        sys.exit("HIBA: egyik fókuszmegyében sincs hiánytalan állomás – nem írjuk felül.")
    out = {"updated_at": today.isoformat(), "last_day": last.isoformat(),
           "source": SOURCE, "day_definition": "06 UTC-től másnap 06 UTC-ig",
           "stations": stations}
    (config.WEB_DATA / "stations.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for s in stations:
        last7 = list(s["rau"].values())[-CHECK_DAYS:]
        print(f"[ok] {s['county']}: {s['name']} ({s['distance_km']} km a megye középpontjától), "
              f"utolsó {CHECK_DAYS} nap: {last7}")


if __name__ == "__main__":
    main()
