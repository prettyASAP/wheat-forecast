"""Panel összeállítás (Fázis 2).

A három forrás egyesítése:
  1. KSH csv -> hosszú hozamtábla (nuts_id, county_name, crop_year, yield_t_ha, ...)
  2. ERA5 napi időjárás -> napi tábla termésév-hozzárendeléssel
  3. NUTS3 geometria -> a crosswalk ellenőrzéséhez

KRITIKUS termésévi logika (brief 8. buktató): a Y évben betakarított búzát az
előző ősszel vetették, ezért a Y termésévhez a Y-1 okt 1 – Y jún 30 időjárás
tartozik. A napi táblában: hónap >= 10 -> crop_year = év + 1;
hónap <= 6 -> crop_year = év; júl–szept -> nincs termésév (kimarad).

Kimenet:
  data/processed/panel.parquet          (nuts_id, county_name, crop_year, yield_t_ha,
                                         area_ha, production_t)
  data/processed/weather_daily.parquet  (nuts_id, date, crop_year, + 5 napi változó)

Futtatás:  python -m src.build_panel
"""
from __future__ import annotations

import sys

import pandas as pd

from src import config

WEATHER_DAILY_PARQUET = config.DATA_PROCESSED / "weather_daily.parquet"

# A KSH csv szekciócímei -> a panel oszlopnevei
KSH_SECTIONS = {
    "Betakarított terület, hektár": "area_ha",
    "Betakarított összes termés, tonna": "production_t",
    "Termésátlag, kg/hektár": "yield_kg_ha",
}


def _parse_number(cell: str) -> float | None:
    """KSH számformátum: szóköz ezres elválasztó; '..' / '–' / üres = hiányzó."""
    s = cell.strip().replace("\xa0", " ").replace(" ", "")
    if not s or s in {"..", "…", "–", "-"}:
        return None
    return float(s.replace(",", "."))


def parse_ksh_csv() -> pd.DataFrame:
    """A KSH csv-ből hosszú tábla: nuts_id, county_name, crop_year, yield_t_ha, ..."""
    path = config.RAW_KSH / "mez0071.csv"
    if not path.exists():
        sys.exit(f"HIBA: hiányzik {path}. Futtasd előbb: python -m src.fetch_ksh")
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
        if first in KSH_SECTIONS:
            current_section = KSH_SECTIONS[first]
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


def assign_crop_year(dates: pd.Series) -> pd.Series:
    """Termésév hozzárendelés: okt–dec -> év+1, jan–jún -> év, júl–szept -> <NA>."""
    d = pd.to_datetime(dates)
    cy = pd.Series(pd.NA, index=dates.index, dtype="Int64")
    cy[d.dt.month >= 10] = (d.dt.year + 1)[d.dt.month >= 10]
    cy[d.dt.month <= 6] = d.dt.year[d.dt.month <= 6]
    return cy


def build_weather_daily() -> pd.DataFrame:
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
    daily["crop_year"] = assign_crop_year(daily["date"])
    return daily


def main() -> None:
    print("Panel összeállítás")
    yields = parse_ksh_csv()
    print(f"  KSH hozamtábla: {len(yields)} sor "
          f"({yields['nuts_id'].nunique()} egység x {yields['crop_year'].nunique()} év)")

    daily = build_weather_daily()
    print(f"  Napi időjárás: {len(daily)} sor, {daily['nuts_id'].nunique()} egység")

    # Csak azok a termésévek maradnak a panelben, amelyekhez TELJES időjárási
    # ablak tartozik (okt 1 – jún 30). Az időjárás 1999-10-01-től indul, így a
    # 2000-es termésév az első teljes.
    wx_years = daily.dropna(subset=["crop_year"]).groupby("crop_year")["date"].count()
    complete_years = wx_years[wx_years >= 20 * 270].index  # ~273 nap x 20 vármegye
    panel = yields[yields["crop_year"].isin(complete_years)].copy()

    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(config.PANEL_PARQUET, index=False)
    daily.to_parquet(WEATHER_DAILY_PARQUET, index=False)
    print(f"  [ok] {config.PANEL_PARQUET.name}: {len(panel)} sor, "
          f"termésévek {panel['crop_year'].min()}..{panel['crop_year'].max()}")
    print(f"  [ok] {WEATHER_DAILY_PARQUET.name}: {len(daily)} sor")

    # --- Fázis-záró ellenőrzés ---
    problems = []
    if panel["nuts_id"].nunique() != 20:
        problems.append(f"nem 20 egység: {panel['nuts_id'].nunique()}")
    per_cy = daily.dropna(subset=["crop_year"]).groupby(["nuts_id", "crop_year"])["date"].count()
    bad = per_cy[(per_cy < 272) | (per_cy > 274)]
    # az utolsó termésév (okt-dec megvan, jan-jún jövőre) lehet csonka — az nem hiba
    bad = bad[bad.index.get_level_values("crop_year") <= int(panel["crop_year"].max())]
    if len(bad):
        problems.append(f"lyukas termésév-ablak: {len(bad)} db")
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
    main()
