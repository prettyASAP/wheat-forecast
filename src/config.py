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

# Feldolgozott kimenetek — terményenként (lásd CROPS lent)
def panel_parquet(crop: str = "wheat") -> Path:
    return DATA_PROCESSED / f"panel_{crop}.parquet"


def features_parquet(crop: str = "wheat") -> Path:
    return DATA_PROCESSED / f"features_{crop}.parquet"


def weather_daily_parquet(crop: str = "wheat") -> Path:
    return DATA_PROCESSED / f"weather_daily_{crop}.parquet"


# --------------------------------------------------------------------------- #
# Adatforrás: KSH terménytáblák (stadat 19.1.2.x)
# --------------------------------------------------------------------------- #
# Az oldalt töltjük le és onnan szedjük ki a relatív letöltési linket (xlsx/csv),
# mert a fájl URL-je verzióváltáskor változhat (brief 5.1).
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
# Terményregiszter (Fázis 8) — minden termény-specifikus beállítás egy helyen.
#
# Fenológiai ablakok: (kezdő_hónap, kezdő_nap, záró_hónap, záró_nap, év_eltolás),
#   év_eltolás = -1 -> a termésévhez képest az előző naptári év (Y-1); 0 -> Y.
# Megjegyzés: a téli ablak feb 28-án zárul — szökőévben a feb 29. kimarad az
# ablakos mutatókból (a szezon-szintűekben benne van). Szándékos: így az ablak
# minden évben 90 napos; auditált hatás: a 26 évben egyetlen fagynap sem esett
# feb 29-re (Tmin min. -3.4 °C volt).
# season: (kezdő_hónap, záró_hónap) — a termésévhez tartozó időjárási ablak.
#   Ha kezdő > záró (búza: 10..6), az ablak átnyúlik az évhatáron: a Y termésév
#   a Y-1 okt 1 – Y jún 30 időjárást kapja. Ha kezdő < záró (kukorica: 4..9),
#   minden a Y naptári évben van.
# --------------------------------------------------------------------------- #
GDD_BASE_C = 0.0             # GDD bázishőmérséklet (mindkét terményre)
WINTER_FROST_TMIN_C = -15.0  # Tmin < -15 °C fagynap (őszi vetésű terményekre)

CROPS = {
    "wheat": {
        "label": "búza",
        "ksh_page": "https://www.ksh.hu/stadat_files/mez/hu/mez0071.html",
        "ksh_slug": "mez0071",
        "season": (10, 6),
        "phenology": {
            "sowing_emergence": (10, 1, 11, 15, -1),  # vetés/kelés (Y-1)
            "winter_dormancy": (12, 1, 2, 28, -1),    # téli nyugalom (Y-1/Y)
            "tillering": (3, 1, 4, 30, 0),            # bokrosodás (Y)
            "grain_filling": (5, 1, 6, 20, 0),        # szemtelítődés KRIT (Y)
        },
        "heat_window": "grain_filling",   # ebben az ablakban számoljuk a hőstresszt
        "heat_tmax_c": 30.0,
        "use_frost": True,                # téli fagynapok feature
        "wb_windows": ["tillering", "grain_filling"],  # ablakos vízmérlegek
        "model_features": [
            "gdd_sowing_emergence", "gdd_winter_dormancy", "gdd_tillering",
            "gdd_grain_filling", "prec_sowing_emergence", "prec_winter_dormancy",
            "heat_days", "frost_days_winter", "wb_tillering", "wb_grain_filling",
            # Konvex halmozott aszályjelző: min(wb_total - vármegye-medián, 0),
            # a tanítóminta mediánjával (look-ahead-mentes) — mérési kapu iteráció.
            "wb_deficit",
        ],
        "asof": (6, 15),                  # as-of backtest: jún 15
        "backtest_years": [2022, 2007, 2003],
    },
    "corn": {
        "label": "kukorica",
        "ksh_page": "https://www.ksh.hu/stadat_files/mez/hu/mez0072.html",
        "ksh_slug": "mez0072",
        "season": (4, 9),
        "phenology": {
            "sowing_emergence": (4, 15, 5, 31, 0),    # vetés/kelés (Y)
            "vegetative": (6, 1, 6, 30, 0),           # intenzív növekedés (Y)
            "flowering": (7, 1, 7, 31, 0),            # virágzás/megporzás KRIT (Y)
            "grain_filling": (8, 1, 9, 15, 0),        # szemtelítődés (Y)
        },
        # A kukoricánál a júliusi hővel szembeni érzékenység a döntő (brief 8. fázis);
        # a megporzás 32 °C felett károsodik.
        "heat_window": "flowering",
        "heat_tmax_c": 32.0,
        "use_frost": False,               # nincs téli kitettség
        "wb_windows": ["flowering", "grain_filling"],
        "model_features": [
            "gdd_sowing_emergence", "gdd_vegetative", "gdd_flowering",
            "gdd_grain_filling", "prec_sowing_emergence", "prec_vegetative",
            "heat_days", "wb_flowering", "wb_grain_filling", "wb_deficit",
        ],
        "asof": (8, 1),                   # as-of backtest: aug 1 (virágzás után)
        "backtest_years": [2022, 2012, 2007],
    },
    "barley": {
        # A KSH árpa-táblában az őszi árpa KÜLÖN szekcióban szerepel — tisztán
        # az őszi árpát modellezzük (nincs őszi/tavaszi keverés). Fenológia:
        # a búzánál ~10 nappal korábbi érés. A mérési kapu dönt a publikálásról.
        "label": "őszi árpa",
        "ksh_sections": {
            "Őszi árpa betakarított területe, hektár": "area_ha",
            "Őszi árpa betakarított összes termése, tonna": "production_t",
            "Őszi árpa termésátlaga, kg/hektár": "yield_kg_ha",
        },
        "ksh_page": "https://www.ksh.hu/stadat_files/mez/hu/mez0073.html",
        "ksh_slug": "mez0073",
        "season": (10, 6),
        "phenology": {
            "sowing_emergence": (10, 1, 11, 10, -1),  # vetés/kelés (Y-1)
            "winter_dormancy": (12, 1, 2, 28, -1),    # téli nyugalom (Y-1/Y)
            "tillering": (3, 1, 4, 20, 0),            # bokrosodás (Y)
            "grain_filling": (4, 21, 6, 10, 0),       # szemtelítődés KRIT (Y)
        },
        "heat_window": "grain_filling",
        "heat_tmax_c": 30.0,
        "use_frost": True,
        "wb_windows": ["tillering", "grain_filling"],
        "model_features": [
            "gdd_sowing_emergence", "gdd_winter_dormancy", "gdd_tillering",
            "gdd_grain_filling", "prec_sowing_emergence", "prec_winter_dormancy",
            "heat_days", "frost_days_winter", "wb_tillering", "wb_grain_filling",
            "wb_deficit",
        ],
        "asof": (6, 15),
        "backtest_years": [2022, 2007, 2003],
    },
}
DEFAULT_CROP = "wheat"

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
# Modell (Fázis 4) — termény-független beállítások.
# A termény-specifikus magyarázólista a CROPS[crop]["model_features"].
# --------------------------------------------------------------------------- #
TREND_DEGREE = 1        # közös időtrend foka (1 = lineáris, 2 = kvadratikus)
RIDGE_ALPHA = 25.0      # ridge büntetés az időjárási blokkra (0 = sima OLS);
                        # LOYO ráccsal választva (validate.py, 2026-07)
UNCERTAINTY_Z = 1.282   # 80%-os sáv a normál eloszlás alapján

# --------------------------------------------------------------------------- #
# Napi PDF-jelentés (report_pdf.py)
# --------------------------------------------------------------------------- #
# Fókusz-vármegyék az 1. oldal blokkjához (ügyfél-igényfelmérés alapján;
# név szerint, a NUTS-kód futásidőben oldódik fel a forecast JSON-ból).
REPORT_FOCUS_COUNTIES = ["Békés", "Hajdú-Bihar", "Fejér"]
