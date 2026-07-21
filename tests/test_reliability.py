"""P0 megbízhatósági teszt-öv (BACKEND + ADATELEMZŐ csomag).

Kis, fagyasztott szintetikus fixture-ök; NINCS hálózat és NINCS teljes pipeline.
A tesztek a TÉNYLEGES, tisztán importálható függvényaláírásokra épülnek:

  - src.features.window_dates / compute_features
  - src.build_panel.assign_crop_year / season_days / _parse_number
  - src.predict_live.current_crop_year / season_window
  - src.model.fit_panel_model (wb_deficit look-ahead-mentesség)
  - src.walkforward.band_calibration / v2_features

Futtatás:  .venv/bin/python -m pytest -q
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src import config
from src.build_panel import _parse_number, assign_crop_year, season_days
from src.features import compute_features, window_dates
from src.model import fit_panel_model
from src.predict_live import current_crop_year, season_window
from src.walkforward import band_calibration, v2_features


# --------------------------------------------------------------------------- #
# 1) Fenológiai ablak-dátumok — évhatár-átnyúló logika + tengelyek
# --------------------------------------------------------------------------- #
def test_window_dates_winter_dormancy_spans_year_boundary():
    """Búza téli nyugalom (12,1,2,28,-1): dec (Y-1) – feb (Y)."""
    start, end = window_dates(2004, (12, 1, 2, 28, -1))
    assert start == date(2003, 12, 1)
    assert end == date(2004, 2, 28)  # a záró hónap < kezdő -> +1 naptári év


def test_window_dates_autumn_offset_no_spill():
    """Búza vetés/kelés (10,1,11,15,-1): teljes egészében az előző naptári évben."""
    start, end = window_dates(2004, (10, 1, 11, 15, -1))
    assert start == date(2003, 10, 1)
    assert end == date(2003, 11, 15)  # m2>=m1 -> nincs évhatár-átnyúlás


def test_window_dates_same_calendar_year_corn():
    """Kukorica virágzás (7,1,7,31,0): azonos naptári év, eltolás nélkül."""
    start, end = window_dates(2010, (7, 1, 7, 31, 0))
    assert start == date(2010, 7, 1)
    assert end == date(2010, 7, 31)


def test_window_dates_grain_filling_current_year():
    start, end = window_dates(2004, (5, 1, 6, 20, 0))
    assert (start, end) == (date(2004, 5, 1), date(2004, 6, 20))


# --------------------------------------------------------------------------- #
# 2) Termésév-hozzárendelés — őszi vetés vs kukorica, évhatár, szökőév
# --------------------------------------------------------------------------- #
def _cy(dates: list[str], crop: str) -> list:
    s = assign_crop_year(pd.Series(pd.to_datetime(dates)), crop)
    return [None if pd.isna(v) else int(v) for v in s]


def test_assign_crop_year_wheat_boundary():
    """Búza: hónap>=10 -> Y+1; hónap<=6 -> Y; júl–szept a szezonon kívül (NA)."""
    got = _cy(["2003-10-01", "2003-12-31", "2004-01-01",
               "2004-06-30", "2004-07-01", "2004-09-15"], "wheat")
    assert got == [2004, 2004, 2004, 2004, None, None]


def test_assign_crop_year_corn_same_year():
    """Kukorica: 4<=hónap<=9 -> Y; azon kívül NA (nincs évhatár-átnyúlás)."""
    got = _cy(["2010-03-31", "2010-04-01", "2010-09-30", "2010-10-01"], "corn")
    assert got == [None, 2010, 2010, None]


def test_assign_crop_year_leap_day_wheat():
    """Szökőnap (2004-02-29) a búza-szezonban a tárgyévhez tartozik."""
    assert _cy(["2004-02-29"], "wheat") == [2004]


def test_season_days_positive_and_ordered():
    """A búza (évhatár-átnyúló) szezon hosszabb naptári ablak, mint a kukoricáé."""
    assert season_days("wheat") > 250
    assert season_days("corn") > 150
    # évhatár-átnyúló (okt–jún) hosszabb, mint a naptári éven belüli (ápr–szept)
    assert season_days("wheat") > season_days("corn")


# --------------------------------------------------------------------------- #
# 3) current_crop_year / szezonhatár edge-case
#    (dokumentáltan itt volt szezon eleji összeomlás — külön teszt)
# --------------------------------------------------------------------------- #
def test_current_crop_year_wheat():
    """Búza (évhatáron átnyúló): ősztől a következő év a futó termésév."""
    assert current_crop_year(date(2026, 7, 15), "wheat") == 2026   # nyár: most zárult
    assert current_crop_year(date(2025, 10, 1), "wheat") == 2026   # ősz: már a következő
    assert current_crop_year(date(2026, 1, 15), "wheat") == 2026   # tél: futó szezon


def test_current_crop_year_corn_preseason_does_not_crash():
    """Kukorica jan–márc: a szezon EL SEM kezdődött -> az előző, zárult év a futó.

    Audit-javítás: korábban ez a szezon eleji ág üres időjárással összeomlott.
    """
    assert current_crop_year(date(2026, 1, 15), "corn") == 2025   # szezon előtt
    assert current_crop_year(date(2026, 3, 31), "corn") == 2025   # közvetlen a start előtt
    assert current_crop_year(date(2026, 4, 1), "corn") == 2026    # szezonkezdet
    assert current_crop_year(date(2026, 9, 30), "corn") == 2026   # szezon vége
    assert current_crop_year(date(2026, 12, 1), "corn") == 2026   # utána: a most zárult


def test_season_window_bounds():
    ws, we = season_window(2026, "wheat")
    assert ws == date(2025, 10, 1) and we == date(2026, 6, 30)
    cs, ce = season_window(2026, "corn")
    assert cs == date(2026, 4, 1) and ce == date(2026, 9, 30)


# --------------------------------------------------------------------------- #
# 4) compute_features — hőstressz-ablak, szökőév-fagy kizárás, vízmérleg
# --------------------------------------------------------------------------- #
def _wheat_daily_2004() -> pd.DataFrame:
    """Egy vármegye teljes búza-2004 szezonja (2003-10-01 .. 2004-06-30), konstans
    alapidőjárással; célzott anomáliákkal a hőstressz- és fagy-tesztekhez.

    2004 SZÖKŐÉV -> feb 29. a szezonban van, de a téli ablak (12,1..2,28) kizárja.
    """
    days = pd.date_range("2003-10-01", "2004-06-30", freq="D").date
    df = pd.DataFrame({
        "nuts_id": "HU211",
        "date": days,
        "temperature_2m_mean": 10.0,
        "temperature_2m_max": 15.0,
        "temperature_2m_min": 5.0,
        "precipitation_sum": 1.0,
        "et0_fao_evapotranspiration": 2.0,
        "crop_year": 2004,
    })
    d = df["date"]
    # hőstressz CSAK a szemtelítődés (grain_filling, máj 1 – jún 20) ablakban számít
    df.loc[d == date(2004, 5, 15), "temperature_2m_max"] = 35.0   # BEszámít
    df.loc[d == date(2004, 4, 15), "temperature_2m_max"] = 35.0   # bokrosodás: NEM
    # téli fagynap (Tmin < -15): csak a téli ablakon belül és nem szökőnapon
    df.loc[d == date(2004, 2, 28), "temperature_2m_min"] = -20.0  # BEszámít
    df.loc[d == date(2004, 2, 29), "temperature_2m_min"] = -20.0  # szökőnap: KIZÁRVA
    df.loc[d == date(2004, 3, 15), "temperature_2m_min"] = -20.0  # ablakon kívül: NEM
    return df


def test_compute_features_heat_days_only_in_heat_window():
    feats = compute_features(_wheat_daily_2004(), "wheat")
    assert len(feats) == 1
    r = feats.iloc[0]
    # a máj 15-i tmax=35 az egyetlen hőstressznap a grain_filling ablakban
    assert int(r["heat_days"]) == 1
    # EDD = max(tmax - 30, 0) az ablakban = 35 - 30 = 5
    assert r["edd"] == pytest.approx(5.0)


def test_compute_features_frost_excludes_leap_day():
    """A feb 29-i fagy NEM számít (téli ablak feb 28-án zárul — dokumentált)."""
    feats = compute_features(_wheat_daily_2004(), "wheat")
    r = feats.iloc[0]
    # csak a feb 28. esik a téli nyugalom ablakba; feb 29. és márc 15. nem
    assert int(r["frost_days_winter"]) == 1


def test_compute_features_water_balance_sign_and_total():
    """wb_total = sum(precip - et0); konstans (1-2) mellett = -(napok száma)."""
    daily = _wheat_daily_2004()
    feats = compute_features(daily, "wheat")
    r = feats.iloc[0]
    n_days = len(daily)
    assert r["wb_total"] == pytest.approx(-(n_days))
    assert r["wb_total"] < 0  # aszályos jelleg (et0 > precip)


# --------------------------------------------------------------------------- #
# 5) wb_deficit konvex aszály-tag — a medián a TANÍTÓMINTÁBÓL (look-ahead-mentes)
# --------------------------------------------------------------------------- #
def _panel_for_wb(wb_by_year: dict[int, float]) -> pd.DataFrame:
    """Minimál panel egyetlen vármegyére: yield lineárisan nő, adott wb_total-ok."""
    rows = []
    for i, (year, wb) in enumerate(sorted(wb_by_year.items())):
        rows.append({"nuts_id": "HU211", "crop_year": year,
                     "yield_t_ha": 4.0 + 0.05 * i, "wb_total": wb})
    return pd.DataFrame(rows)


def test_wb_median_comes_from_training_only():
    """A wb_median a tanítómintából számolódik, a tesztév nem szivárog bele."""
    train = _panel_for_wb({2001: 10.0, 2002: 20.0, 2003: 30.0, 2004: 40.0})
    m = fit_panel_model(train, features=["wb_deficit"], trend_degree=1,
                        ridge_alpha=0.0)
    # a négy tanítóértéken (10,20,30,40) a medián 25
    assert m.wb_median["HU211"] == pytest.approx(25.0)

    # egy szélsőségesen nedves tesztév (wb_total=100) NEM módosítja a mediánt,
    # és a konvex deficit-tag rá 0 (min(100-25, 0) = 0)
    test = pd.DataFrame([{"nuts_id": "HU211", "crop_year": 2005,
                          "yield_t_ha": np.nan, "wb_total": 100.0}])
    derived = m._with_derived(test)
    assert derived["wb_deficit"].iloc[0] == pytest.approx(0.0)


def test_wb_deficit_is_concave_negative_side_only():
    """wb_deficit = min(wb_total - medián, 0): hiánynál negatív, többletnél 0.
    (Affin tagok minimuma → KONKÁV a wb-ben; a szezonközi Jensen-korrekció
    ezért felfelé-torzítás ellen véd — matematikai audit 3.1.)"""
    train = _panel_for_wb({2001: 10.0, 2002: 20.0, 2003: 30.0, 2004: 40.0})
    m = fit_panel_model(train, features=["wb_deficit"], trend_degree=1,
                        ridge_alpha=0.0)
    med = m.wb_median["HU211"]  # 25
    dry = pd.DataFrame([{"nuts_id": "HU211", "crop_year": 2006,
                         "yield_t_ha": np.nan, "wb_total": med - 15}])
    wet = pd.DataFrame([{"nuts_id": "HU211", "crop_year": 2007,
                         "yield_t_ha": np.nan, "wb_total": med + 15}])
    assert m._with_derived(dry)["wb_deficit"].iloc[0] == pytest.approx(-15.0)
    assert m._with_derived(wet)["wb_deficit"].iloc[0] == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# 6) Walk-forward / sáv-kalibráció invariánsai
# --------------------------------------------------------------------------- #
def _synthetic_residuals() -> pd.DataFrame:
    """Fagyasztott, determinisztikus reziduum-tábla több évre, több vármegyére."""
    rng = np.random.default_rng(0)
    counties = ["HU211", "HU212", "HU213", "HU321"]
    years = range(2011, 2026)
    rows = []
    for y in years:
        for c in counties:
            actual = 5.0 + rng.normal(0, 0.5)
            pred = actual - rng.normal(0, 0.4)  # kis hiba
            rows.append({"crop_year": y, "nuts_id": c,
                         "actual": actual, "pred": pred})
    return pd.DataFrame(rows)


def test_band_calibration_invariants():
    calib = band_calibration(_synthetic_residuals())
    # q10 < q90 (a sáv nem fordul meg)
    assert calib["q10"] < calib["q90"]
    # a standardizált q10 negatív, q90 pozitív oldal
    assert calib["q10"] < 0 < calib["q90"]
    # minden szórás szigorúan pozitív
    assert calib["sigma_pooled"] > 0
    assert all(s > 0 for s in calib["sigma_by_county"].values())
    # a lefedettség értelmes tartományban (0,1]
    assert 0.0 < calib["coverage_overall"] <= 1.0
    # évek száma és a >=80%-os évek konzisztensek
    assert calib["n_years"] == 15
    assert 0 <= calib["years_with_ge80_coverage"] <= calib["n_years"]
    lo, hi = calib["year_coverage_ci95"]
    assert 0.0 <= lo <= hi <= 1.0


def test_v2_features_mapping():
    """v2: gdd_* -> gddc_*, heat_days -> edd; warm=True hozzáfűzi a warm_nights-t."""
    v2 = v2_features("corn")
    assert "edd" in v2 and "heat_days" not in v2
    assert all(not f.startswith("gdd_") for f in v2)  # csak gddc_ marad
    assert "warm_nights" not in v2
    assert "warm_nights" in v2_features("corn", warm=True)


def test_v2_features_no_duplicate_warm_nights():
    """Ha a config.model_features MÁR tartalmazza a warm_nights-t (barley),
    a warm=True nem duplikálhatja — a duplikált (kollineáris) oszlop α=0-nál
    szinguláris mátrixot okozna (walk-forward barley-crash gyökéroka)."""
    for crop in config.CROPS:
        feats = v2_features(crop, warm=True)
        assert feats.count("warm_nights") <= 1, f"{crop}: duplikált warm_nights"


def test_fit_panel_model_robust_to_degenerate_weather_column():
    """A megoldó (lstsq) ne hasaljon el, ha egy időjárási oszlop egy foldban
    ~degenerált (majdnem csupa nulla) és nincs ridge (α=0) — ez volt a barley
    warm_nights walk-forward szingularitás oka."""
    rng = np.random.default_rng(1)
    rows = []
    for i, year in enumerate(range(2001, 2013)):
        for c in ("HU211", "HU212", "HU321"):
            rows.append({"nuts_id": c, "crop_year": year,
                         "yield_t_ha": 4.0 + 0.05 * i + rng.normal(0, 0.1),
                         "wb_total": rng.normal(-200, 40),
                         # majdnem csupa nulla oszlop: egyetlen nem-nulla érték
                         "warm_nights": 3.0 if (year == 2003 and c == "HU211")
                         else 0.0})
    train = pd.DataFrame(rows)
    # α=0 (nincs ridge) + degenerált oszlop: solve elhasalna, lstsq nem
    m = fit_panel_model(train, features=["wb_total", "warm_nights"],
                        trend_degree=1, ridge_alpha=0.0)
    assert np.all(np.isfinite(m.beta))


# --------------------------------------------------------------------------- #
# 7) KSH szám-parszolás (tiszta segédfüggvény)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cell,expected", [
    ("5 234", 5234.0),        # szóköz ezres elválasztó
    ("4,5", 4.5),             # tizedesvessző
    ("\xa01 000", 1000.0),    # nem törő szóköz
    ("..", None), ("…", None), ("–", None), ("", None), ("  ", None),
])
def test_parse_number(cell, expected):
    assert _parse_number(cell) == expected


# --------------------------------------------------------------------------- #
# 8) config konzisztencia — nincs duplikált fenológiai kulcs, wb_windows valid
# --------------------------------------------------------------------------- #
def test_wb_windows_reference_existing_phenology():
    for crop, spec in config.CROPS.items():
        for w in spec["wb_windows"]:
            assert w in spec["phenology"], f"{crop}: ismeretlen wb_window {w}"
        assert spec["heat_window"] in spec["phenology"]


# --------------------------------------------------------------------------- #
# 9) HTML-jelentés (Claude Design szedés) — szerkezeti füst-teszt
# --------------------------------------------------------------------------- #
def test_report_html_has_three_pages_and_all_crops():
    """A HTML-generátor 3 A4-oldalt ad, mindhárom terménnyel; a Playwright
    (PDF) importja lusta, így böngésző nélkül is fut. Az élő forecast-JSON-okból
    dolgozik (a repóban jelen vannak)."""
    from src import report_html
    fcs = {c: report_html.load_fc(c) for c in config.REPORT_CROPS}
    html = report_html.build_html(fcs, "2026-07-15", "2026-07-15 12:00")
    assert html.count('<section class="page">') == 3
    for fc in fcs.values():
        assert fc["crop"].capitalize() in html
    # a bizonytalansági sáv a szám mellett (agrárprofesszori elv) és a
    # forgatókönyv-tábla is jelen van
    assert "80%-os sáv" in html
    assert "Terményben és forintban" in html
