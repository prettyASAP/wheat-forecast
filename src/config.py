"""Központi konfiguráció — minden konstans egy helyen (brief 0. pont).

Vármegyelista, NUTS crosswalk, fenológiai ablakok, adatforrás-URL-ek, útvonalak.
A kód és a változónevek angolul, a tartalmi kommentek magyarul.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

# --------------------------------------------------------------------------- #
# Útvonalak
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_RAW = DATA_DIR / "raw"
DATA_INTERIM = DATA_DIR / "interim"
DATA_PROCESSED = DATA_DIR / "processed"
WEB_DIR = PROJECT_ROOT / "web"
WEB_DATA = WEB_DIR / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"

# Nyers adat alkönyvtárai forrásonként
RAW_KSH = DATA_RAW / "ksh"
RAW_BOUNDARIES = DATA_RAW / "boundaries"
RAW_WEATHER = DATA_RAW / "weather"

# Feldolgozott kimenetek
PANEL_PARQUET = DATA_PROCESSED / "panel.parquet"

# --------------------------------------------------------------------------- #
# Adatforrás: KSH búzahozam (19.1.2.4. tábla)
# --------------------------------------------------------------------------- #
# Az oldalt töltjük le és onnan szedjük ki a relatív letöltési linket (xlsx/csv),
# mert a fájl URL-je verzióváltáskor változhat (brief 5.1).
KSH_PAGE_URL = "https://www.ksh.hu/stadat_files/mez/hu/mez0071.html"
# A KSH oldal és a letölthető fájlok kódolása
KSH_ENCODING = "iso-8859-2"

# --------------------------------------------------------------------------- #
# Adatforrás: NUTS3 vármegyehatárok (Eurostat GISCO, NUTS 2024)
# --------------------------------------------------------------------------- #
GISCO_NUTS3_URL = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
    "NUTS_RG_20M_2024_4326_LEVL_3.geojson"
)
# Tartalék mirrorok, ha a GISCO nem elérhető (brief 5.2)
GISCO_NUTS3_MIRRORS = [
    (
        "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
        "NUTS_RG_60M_2024_4326_LEVL_3.geojson"
    ),
]
COUNTRY_CODE = "HU"  # CNTR_CODE szűrő -> 20 magyar NUTS3 egység

# --------------------------------------------------------------------------- #
# Adatforrás: Open-Meteo ERA5 időjárás
# --------------------------------------------------------------------------- #
OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPENMETEO_TIMEZONE = "Europe/Budapest"
OPENMETEO_MODEL = "era5"
# Napi változók (brief 5.3). A sorrend a mentett táblákban is ez lesz.
OPENMETEO_DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "et0_fao_evapotranspiration",
]

# --------------------------------------------------------------------------- #
# Időkeretek
# --------------------------------------------------------------------------- #
# A tanító időjárás kezdete: 1999-10-01, hogy a 2000-es termésév őszi vetési
# ablaka (Y-1 okt 1 – nov 15) is lefedett legyen (felhasználói döntés).
WEATHER_START = "1999-10-01"


def last_complete_year(today: date | None = None) -> int:
    """A legutóbbi teljes naptári év (az idei még nem teljes)."""
    today = today or date.today()
    return today.year - 1


def weather_end(today: date | None = None) -> str:
    """Az időjárás-letöltés vége: a legutóbbi teljes év december 31."""
    return f"{last_complete_year(today)}-12-31"


# --------------------------------------------------------------------------- #
# Fenológiai ablakok (őszi búza, Magyarország) — a features.py használja (3. fázis)
# Minden ablak (kezdő_hónap, kezdő_nap, záró_hónap, záró_nap, év_eltolás) alakban:
#   év_eltolás = -1  -> a termésévhez képest az előző naptári év (Y-1)
#   év_eltolás =  0  -> a termésév (Y)
# --------------------------------------------------------------------------- #
PHENOLOGY_WINDOWS = {
    "sowing_emergence": (10, 1, 11, 15, -1),   # vetés/kelés: okt 1 – nov 15 (Y-1)
    "winter_dormancy": (12, 1, 2, 28, -1),     # téli nyugalom: dec 1 (Y-1) – feb 28 (Y)
    "tillering": (3, 1, 4, 30, 0),             # bokrosodás: márc 1 – ápr 30 (Y)
    "grain_filling": (5, 1, 6, 20, 0),         # szemtelítődés KRIT: máj 1 – jún 20 (Y)
}

# Hőstressz és fagy küszöbök (brief 3. fázis)
HEAT_STRESS_TMAX_C = 30.0    # Tmax > 30 °C hőstressznap (szemtelítődés alatt)
WINTER_FROST_TMIN_C = -15.0  # Tmin < -15 °C fagynap (dec – feb)
GDD_BASE_C = 0.0             # GDD bázishőmérséklet

# --------------------------------------------------------------------------- #
# Crosswalk: KSH vármegyenév -> NUTS3 kód (2. fázis tölti ki, kézzel ellenőrizve)
# A KSH magyar vármegyeneveket használ ("... vármegye"), a GISCO NAME_LATN latin
# átírást. A megfeleltetés kézi, nem vak fuzzy matching (brief 8. buktató).
# Budapestet külön kezeljük (elhanyagolható búza) — lásd BUDAPEST_HANDLING.
# --------------------------------------------------------------------------- #
# Ellenőrzés (Fázis 2): a KSH tábla vármegyenevei karakterre egyeznek a GISCO
# NAME_LATN mezővel (2026-07 állapot). A megfeleltetés így 1:1, de explicit
# rögzítjük, hogy egy jövőbeli névváltás (pl. "megye"/"vármegye" toldat) azonnal
# kiderüljön, ne csendben törjön.
KSH_TO_NUTS3: dict[str, str] = {
    "Budapest": "HU110",
    "Pest": "HU120",
    "Fejér": "HU211",
    "Komárom-Esztergom": "HU212",
    "Veszprém": "HU213",
    "Győr-Moson-Sopron": "HU221",
    "Vas": "HU222",
    "Zala": "HU223",
    "Baranya": "HU231",
    "Somogy": "HU232",
    "Tolna": "HU233",
    "Borsod-Abaúj-Zemplén": "HU311",
    "Heves": "HU312",
    "Nógrád": "HU313",
    "Hajdú-Bihar": "HU321",
    "Jász-Nagykun-Szolnok": "HU322",
    "Szabolcs-Szatmár-Bereg": "HU323",
    "Bács-Kiskun": "HU331",
    "Békés": "HU332",
    "Csongrád-Csanád": "HU333",
}
# A KSH "Területi egység szintje" értékei, amelyek NUTS3 egységet jelölnek
# (a régió/nagyrégió/ország aggregátumokat kiszűrjük).
KSH_NUTS3_LEVELS = {"vármegye", "vármegye, régió", "főváros, régió"}

# Budapest kezelése a modellben: "drop" (kihagyás) vagy "merge_pest" (Pesthez).
# Döntés: kihagyjuk — Budapest búzaterülete elhanyagolható (2020 óta < 600 ha),
# a hozama zajos, torzítaná a modellt. A térképen "nincs becslés" jelölést kap.
BUDAPEST_HANDLING = "drop"
BUDAPEST_NUTS_ID = "HU110"

# --------------------------------------------------------------------------- #
# Modell (Fázis 4)
# --------------------------------------------------------------------------- #
# Időjárási magyarázók a feature táblából. A collinearitás miatt szűkített,
# értelmezhető készlet: ablakos GDD-k, ablakos csapadék, stresszmutatók, vízmérleg.
MODEL_FEATURES = [
    "gdd_sowing_emergence",
    "gdd_winter_dormancy",
    "gdd_tillering",
    "gdd_grain_filling",
    "prec_sowing_emergence",
    "prec_winter_dormancy",
    "heat_days_grain_filling",
    "frost_days_winter",
    "wb_tillering",
    "wb_grain_filling",
    # Konvex halmozott aszályjelző: min(wb_total - vármegye-medián, 0), a modell
    # számolja a tanítóminta mediánjával (look-ahead-mentes). A 2022-szerű,
    # több ablakon átívelő szárazság megfogásához (mérési kapu iteráció).
    "wb_deficit",
]
TREND_DEGREE = 1        # közös időtrend foka (1 = lineáris, 2 = kvadratikus)
RIDGE_ALPHA = 25.0      # ridge büntetés az időjárási blokkra (0 = sima OLS);
                        # LOYO ráccsal választva (validate.py, 2026-07)
UNCERTAINTY_Z = 1.282   # 80%-os sáv a normál eloszlás alapján
# As-of backtest dátuma: az adott év jún 15-ig ismert időjárás
ASOF_MONTH, ASOF_DAY = 6, 15
BACKTEST_YEARS = [2022, 2007, 2003]
