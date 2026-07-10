"""ERA5 időjárás letöltése vármegyénként (Open-Meteo archive).

A NUTS3 HU geometriából vármegyénként reprezentatív pontot (a poligonon belüli
pont) számol, és minden vármegyére EGY archive-kérést futtat a teljes idősorra
(WEATHER_START .. legutóbbi teljes év) — így 20 kérés, bőven a napi limit alatt
(brief 8. buktató). Idempotens: meglévő vármegye-parquetet nem tölt újra --force nélkül.

Futtatás:  python -m src.fetch_weather [--force]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from src import config

TIMEOUT = 120
SLEEP_BETWEEN = 2.0     # udvarias késleltetés sikeres kérések közt (mp)
RATE_LIMIT_WAIT = 65    # 429 esetén ennyit várunk (a percenkénti keret resetel)
MAX_RETRIES = 6         # hányszor próbáljuk újra egy vármegye letöltését 429-re

HU_GEOJSON = config.RAW_BOUNDARIES / "nuts3_hu_20M_2024.geojson"
CENTROIDS_CSV = config.RAW_WEATHER / "_centroids.csv"


def load_centroids() -> pd.DataFrame:
    """Betölti a HU geometriát és vármegyénként (nuts_id) reprezentatív pontot ad."""
    if not HU_GEOJSON.exists():
        sys.exit(f"HIBA: hiányzik {HU_GEOJSON}. Futtasd előbb: python -m src.fetch_boundaries")
    gdf = gpd.read_file(HU_GEOJSON)
    # representative_point() garantáltan a poligonon belül van (a centroiddal ellentétben)
    pts = gdf.geometry.representative_point()
    out = pd.DataFrame({
        "nuts_id": gdf["NUTS_ID"].values,
        "name_latn": gdf.get("NAME_LATN", pd.Series([""] * len(gdf))).values,
        "lat": pts.y.round(4).values,
        "lon": pts.x.round(4).values,
    }).sort_values("nuts_id").reset_index(drop=True)
    return out


def get_daily(url: str, params: dict) -> pd.DataFrame:
    """Open-Meteo napi lekérés backoff-fal. Visszaad: date + napi változók tábla.

    Az Open-Meteo súlyozottan számol: egy hosszú, több változós kérés sok "hívás".
    429 (Too Many Requests) esetén várunk (Retry-After vagy alap) és újrapróbálunk.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        resp = requests.get(url, params=params, timeout=TIMEOUT,
                            headers={"User-Agent": "wheat-forecast/1.0"})
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", RATE_LIMIT_WAIT))
            if attempt == MAX_RETRIES:
                resp.raise_for_status()
            print(f"    [429] rate limit — várok {wait} mp, újrapróba {attempt}/{MAX_RETRIES-1}")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        daily = resp.json().get("daily")
        if not daily or "time" not in daily:
            raise RuntimeError(f"Üres/hibás válasz: {resp.text[:200]}")
        df = pd.DataFrame(daily).rename(columns={"time": "date"})
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df
    raise RuntimeError("Nem sikerült letölteni (rate limit).")  # elvileg elérhetetlen


def fetch_county(lat: float, lon: float, start: str, end: str) -> pd.DataFrame:
    """Egy vármegye teljes napi ERA5 idősora DataFrame-ként (date + 5 változó)."""
    return get_daily(config.OPENMETEO_ARCHIVE_URL, {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "timezone": config.OPENMETEO_TIMEZONE,
        "models": config.OPENMETEO_MODEL,
        "daily": ",".join(config.OPENMETEO_DAILY_VARS),
    })


def main(force: bool = False) -> None:
    print("ERA5 időjárás letöltése vármegyénként (Open-Meteo)")
    start, end = config.WEATHER_START, config.weather_end()
    print(f"  időszak: {start} .. {end}")

    centroids = load_centroids()
    config.RAW_WEATHER.mkdir(parents=True, exist_ok=True)
    centroids.to_csv(CENTROIDS_CSV, index=False)
    print(f"  {len(centroids)} vármegye-centroid mentve: {CENTROIDS_CSV.name}")

    sample_stats = None
    for row in centroids.itertuples(index=False):
        dest = config.RAW_WEATHER / f"{row.nuts_id}.parquet"
        if dest.exists() and not force:
            print(f"  [skip] {row.nuts_id} ({row.name_latn}) — már megvan")
            continue
        df = fetch_county(row.lat, row.lon, start, end)
        df.to_parquet(dest, index=False)
        print(f"  [ok]   {row.nuts_id} ({row.name_latn}) — {len(df)} nap")
        if sample_stats is None:
            sample_stats = (row.nuts_id, df)
        time.sleep(SLEEP_BETWEEN)

    # Szanity egy mintára
    if sample_stats is None:  # minden skippelve volt — töltsünk be egyet
        any_pq = next(iter(sorted(config.RAW_WEATHER.glob("*.parquet"))), None)
        if any_pq:
            sample_stats = (any_pq.stem, pd.read_parquet(any_pq))
    if sample_stats:
        nid, df = sample_stats
        print(f"  szanity ({nid}): {len(df)} nap, "
              f"Tmean {df['temperature_2m_mean'].min():.1f}..{df['temperature_2m_mean'].max():.1f} °C, "
              f"csapadék össz {df['precipitation_sum'].sum():.0f} mm")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ERA5 időjárás letöltése vármegyénként")
    ap.add_argument("--force", action="store_true", help="Újratöltés akkor is, ha megvan")
    main(force=ap.parse_args().force)
