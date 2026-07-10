"""Panel összeállítás (Fázis 2, terményparaméteres a Fázis 8 óta).

A három forrás egyesítése terményenként:
  1. KSH csv -> hosszú hozamtábla (nuts_id, county_name, crop_year, yield_t_ha, ...)
  2. ERA5 napi időjárás -> napi tábla termésév-hozzárendeléssel
  3. NUTS3 geometria -> a crosswalk ellenőrzéséhez

KRITIKUS termésévi logika (brief 8. buktató):
  - búza (season 10..6): a Y évben betakarított búzát az előző ősszel vetették,
    a Y termésévhez a Y-1 okt 1 – Y jún 30 időjárás tartozik
    (hónap >= 10 -> crop_year = év + 1; hónap <= 6 -> crop_year = év)
  - kukorica (season 4..9): tavaszi vetés, minden a Y naptári évben
    (4 <= hónap <= 9 -> crop_year = év)

Kimenet: data/processed/panel_{crop}.parquet, weather_daily_{crop}.parquet.

Futtatás:  python -m src.build_panel [--crop wheat|corn]
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from src import config

# A KSH csv szekciócímei -> a panel oszlopnevei (mindkét terménynél azonosak)
KSH_SECTIONS = {
    "Betakarított terület, hektár": "area_ha",
    "Betakarított összes termés, tonna": "production_t",
    "Termésátlag, kg/hektár": "yield_kg_ha",
}


def _parse_number(cell: str) -> float | None:
    """KSH számformátum: szóköz ezres elválasztó; '..' / '–' / üres = hiányzó."""
    s = cell.strip().replace("\xa0", " ").replace(" ", "")
    # A KSH hiányjelei: "..", "…", kötőjel-változatok; a 0x96/0x97 bájt (cp1252
    # en/em-dash) ISO-8859-2 dekódolással kontrollkarakterként jön át.
    if not s or s in {"..", "…", "–", "—", "-", "\x96", "\x97"}:
        return None
    return float(s.replace(",", "."))


def parse_ksh_csv(crop: str) -> pd.DataFrame:
    """A KSH csv-ből hosszú tábla: nuts_id, county_name, crop_year, yield_t_ha, ..."""
    path = config.RAW_KSH / f"{config.CROPS[crop]['ksh_slug']}.csv"
    if not path.exists():
        sys.exit(f"HIBA: hiányzik {path}. Futtasd előbb: python -m src.fetch_ksh --crop {crop}")
    # terményspecifikus szekciócímek (pl. árpánál "Őszi árpa ..."), különben az alap
    sections = config.CROPS[crop].get("ksh_sections", KSH_SECTIONS)
    lines = path.read_bytes().decode(config.KSH_ENCODING).splitlines()

    # Fejléc: a 2. sor tartalmazza az évoszlopokat
    header = lines[1].split(";")
    years = [int(c) for c in header if c.strip().isdigit()]
    year_start_col = next(i for i, c in enumerate(header) if c.strip().isdigit())

    records: list[dict] = []
    current_section: str | None = None
    for ln in lines[2:]:
        cells = ln.split(";")
        first = cells[0].strip()
        if first in sections:
            current_section = sections[first]
            continue
        if first in KSH_SECTIONS or (first.endswith(("hektár", "tonna", "kg/hektár"))
                                     and len(cells) > 1 and not cells[1].strip()):
            current_section = None  # másik (nem kért) szekció kezdődik
            continue
        if current_section is None or not first:
            continue
        level = cells[1].strip() if len(cells) > 1 else ""
        if level not in config.KSH_NUTS3_LEVELS:
            continue  # régió/nagyrégió/ország aggregátum — kihagyjuk
        if first not in config.KSH_TO_NUTS3:
            sys.exit(f"HIBA: ismeretlen KSH területi egység: '{first}'. "
                     "A crosswalk (config.KSH_TO_NUTS3) frissítése kell — állj meg és ellenőrizd.")
        for year, cell in zip(years, cells[year_start_col:]):
            records.append({
                "nuts_id": config.KSH_TO_NUTS3[first],
                "county_name": first,
                "crop_year": year,
                "variable": current_section,
                "value": _parse_number(cell),
            })

    long_df = pd.DataFrame(records)
    wide = (long_df.pivot_table(index=["nuts_id", "county_name", "crop_year"],
                                columns="variable", values="value", aggfunc="first")
            .reset_index())
    wide["yield_t_ha"] = wide["yield_kg_ha"] / 1000.0  # KSH egység: kg/ha -> t/ha
    return wide[["nuts_id", "county_name", "crop_year", "yield_t_ha",
                 "area_ha", "production_t"]].sort_values(["nuts_id", "crop_year"])


def assign_crop_year(dates: pd.Series, crop: str = config.DEFAULT_CROP) -> pd.Series:
    """Termésév hozzárendelés a termény szezonja szerint (lásd modul-doc)."""
    start_m, end_m = config.CROPS[crop]["season"]
    d = pd.to_datetime(dates)
    cy = pd.Series(pd.NA, index=dates.index, dtype="Int64")
    if start_m > end_m:  # évhatáron átnyúló szezon (búza)
        cy[d.dt.month >= start_m] = (d.dt.year + 1)[d.dt.month >= start_m]
        cy[d.dt.month <= end_m] = d.dt.year[d.dt.month <= end_m]
    else:                # naptári éven belüli szezon (kukorica)
        in_season = (d.dt.month >= start_m) & (d.dt.month <= end_m)
        cy[in_season] = d.dt.year[in_season]
    return cy


def season_days(crop: str) -> int:
    """A termésévi ablak hossza napokban (ellenőrzéshez, ~1 nap tűréssel)."""
    start_m, end_m = config.CROPS[crop]["season"]
    if start_m > end_m:
        return (pd.Timestamp(2001, end_m + 1, 1) - pd.Timestamp(2000, start_m, 1)).days
    return (pd.Timestamp(2000, end_m + 1, 1) - pd.Timestamp(2000, start_m, 1)).days


def build_weather_daily(crop: str) -> pd.DataFrame:
    """A 20 vármegye napi időjárása egy táblában, termésév-oszloppal."""
    frames = []
    files = sorted(config.RAW_WEATHER.glob("HU*.parquet"))
    if len(files) != 20:
        sys.exit(f"HIBA: {len(files)} időjárás-fájl van, várt 20. "
                 "Futtasd: python -m src.fetch_weather")
    for f in files:
        df = pd.read_parquet(f)
        df.insert(0, "nuts_id", f.stem)
        frames.append(df)
    daily = pd.concat(frames, ignore_index=True)
    daily["crop_year"] = assign_crop_year(daily["date"], crop)
    return daily


def main(crop: str = config.DEFAULT_CROP) -> None:
    label = config.CROPS[crop]["label"]
    print(f"Panel összeállítás — {label}")
    yields = parse_ksh_csv(crop)
    print(f"  KSH hozamtábla: {len(yields)} sor "
          f"({yields['nuts_id'].nunique()} egység x {yields['crop_year'].nunique()} év)")

    daily = build_weather_daily(crop)
    print(f"  Napi időjárás: {len(daily)} sor, {daily['nuts_id'].nunique()} egység")

    # Csak azok a termésévek maradnak a panelben, amelyekhez TELJES időjárási
    # ablak tartozik.
    n_days = season_days(crop)
    wx_years = daily.dropna(subset=["crop_year"]).groupby("crop_year")["date"].count()
    complete_years = wx_years[wx_years >= 20 * (n_days - 3)].index
    panel = yields[yields["crop_year"].isin(complete_years)].copy()

    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(config.panel_parquet(crop), index=False)
    daily.to_parquet(config.weather_daily_parquet(crop), index=False)
    print(f"  [ok] {config.panel_parquet(crop).name}: {len(panel)} sor, "
          f"termésévek {panel['crop_year'].min()}..{panel['crop_year'].max()}")
    print(f"  [ok] {config.weather_daily_parquet(crop).name}: {len(daily)} sor")

    # --- Fázis-záró ellenőrzés ---
    problems = []
    if panel["nuts_id"].nunique() != 20:
        problems.append(f"nem 20 egység: {panel['nuts_id'].nunique()}")
    per_cy = daily.dropna(subset=["crop_year"]).groupby(["nuts_id", "crop_year"])["date"].count()
    bad = per_cy[(per_cy < n_days - 2) | (per_cy > n_days + 2)]
    # az utolsó (csonka) termésév nem hiba, ha a panelben nincs benne
    bad = bad[bad.index.get_level_values("crop_year") <= int(panel["crop_year"].max())]
    if len(bad):
        problems.append(f"lyukas termésév-ablak: {len(bad)} db")
    wx_na = daily[daily["crop_year"].notna()][config.OPENMETEO_DAILY_VARS].isna().sum().sum()
    if wx_na:
        problems.append(f"NaN a szezonon belüli nyers időjárásban: {wx_na} érték "
                        "(pl. ERA5 archívum-késés évkezdetkor — töltsd újra --force-szal)")
    dup = panel.duplicated(subset=["nuts_id", "crop_year"]).sum()
    if dup:
        problems.append(f"duplikált (egység, év) sor: {dup}")
    miss = panel["yield_t_ha"].isna().sum()
    print(f"  ellenőrzés: hiányzó hozam {miss} sor "
          f"(Budapest/kis termőterület esetén elfogadható)")
    if problems:
        sys.exit("HIBA a panel-ellenőrzésen: " + "; ".join(problems))
    print("  ellenőrzés: OK — 20 egység, teljes termésév-ablakok, nincs duplikátum")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--crop", choices=list(config.CROPS), default=config.DEFAULT_CROP)
    main(crop=ap.parse_args().crop)
