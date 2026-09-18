"""Hajtóerő-bontás és modelltartomány-ellenőrzés — a modell VÁLTOZTATÁSA NÉLKÜL.

A panelmodell a standardizált időjárási mutatókban lineáris, ezért a becslés
időjárási része PONTOSAN szétszedhető: mutatónként  β_f · (x_f − átlag_f)/szórás_f
(t/ha). Ez nem új modell és nem közelítés, hanem a meglévő becslés számtana
más csoportosításban: a tagok összege bitre a modell időjárási hozzájárulása.

A mutatókat érthető csoportokba vonjuk (vízellátás, hőstressz, hőmérséklet /
fejlődési ütem, téli fagy). A csoport-szint a stabil olvasat: az egyes,
egymással korreláló mutatók együtthatói ridge mellett külön-külön nehezen
értelmezhetők, a csoportösszeg viszont robusztus.

A modelltartomány-ellenőrzés azt jelzi, ha az idei (országos, terület-súlyozott)
mutató kívül esik azon a tartományon, amelyet a modell a tanítóévekben valaha
látott. Ilyenkor a modell extrapolál, a tévedése a szokásosnál nagyobb lehet —
2026 tanulsága, hogy ezt ki kell mondani, nem elhallgatni.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# (kulcs, teljes címke, rövid címke a szűk helyekre)
GROUPS = [
    ("water", "Vízellátás (csapadék, vízmérleg)", "Vízellátás"),
    ("heat", "Hőstressz", "Hőstressz"),
    ("temp", "Hőmérséklet, fejlődési ütem", "Hőmérséklet"),
    ("frost", "Téli fagy", "Téli fagy"),
]

FEATURE_LABELS = {
    "wb_deficit": "a szezon halmozott vízhiánya",
    "wb_tillering": "a bokrosodás vízmérlege",
    "wb_grain_filling": "a szemtelítődés vízmérlege",
    "wb_flowering": "a virágzás vízmérlege",
    "prec_sowing_emergence": "a vetés és kelés csapadéka",
    "prec_winter_dormancy": "a téli csapadék",
    "prec_vegetative": "az intenzív növekedés csapadéka",
    "heat_days": "a virágzáskori hőstressznapok száma",
    "edd": "a szemtelítődés alatti hőstressz",
    "warm_nights": "a meleg éjszakák száma",
    "frost_days_winter": "a kemény téli fagynapok száma",
}


_WINDOW_HU = {
    "sowing_emergence": "a vetés és kelés", "winter_dormancy": "a téli nyugalom",
    "tillering": "a bokrosodás", "grain_filling": "a szemtelítődés",
    "vegetative": "az intenzív növekedés", "flowering": "a virágzás",
    "stem_flowering": "a szárba indulás és virágzás", "pod_filling": "a becőtelítődés",
}


def feature_label(name: str) -> str:
    if name in FEATURE_LABELS:
        return FEATURE_LABELS[name]
    for prefix in ("gddc_", "gdd_"):
        if name.startswith(prefix):
            win = _WINDOW_HU.get(name[len(prefix):])
            if win:
                return f"{win} hőösszege"
    return name


def feature_group(name: str) -> str:
    if name.startswith(("wb_", "prec_")):
        return "water"
    if name in ("heat_days", "edd", "warm_nights"):
        return "heat"
    if name.startswith("frost"):
        return "frost"
    return "temp"  # gdd_*, gddc_*


def contributions(m, feats: pd.DataFrame) -> pd.DataFrame:
    """Vármegyénkénti, mutatónkénti hozzájárulás (t/ha). Sor: nuts_id."""
    df = m._with_derived(feats)
    z = (df[m.feature_names].to_numpy(dtype=float) - m.feat_mean) / m.feat_std
    k = len(m.counties) + m.trend_degree
    return pd.DataFrame(z * m.beta[k:], index=df["nuts_id"].values,
                        columns=m.feature_names)


def national_drivers(contrib: pd.DataFrame, areas: pd.Series,
                     baseline_nat: float, anomaly_pct: float) -> dict:
    """Országos (terület-súlyozott) csoportbontás a szokásos szint %-ában.

    A csoportok összege a modell időjárási hozzájárulása. A közölt anomália a
    külön illesztett naiv trendhez viszonyít, ezért a kettő között kis
    különbség marad ('alapszint'): ezt külön, őszintén közöljük, nem osztjuk szét.
    """
    w = areas.reindex(contrib.index).astype(float)
    nat = contrib.mul(w, axis=0).sum() / w.sum()          # t/ha mutatónként
    by_group: dict[str, float] = {}
    for f, v in nat.items():
        by_group[feature_group(f)] = by_group.get(feature_group(f), 0.0) + float(v)
    groups = [{"key": key, "label": label, "short": short,
               "t_ha": round(by_group[key], 3),
               "pct": round(100 * by_group[key] / baseline_nat, 1)}
              for key, label, short in GROUPS if key in by_group]
    weather_pct = 100 * float(nat.sum()) / baseline_nat
    return {
        "groups": groups,
        "weather_total_pct": round(weather_pct, 1),
        "baseline_shift_pct": round(anomaly_pct - weather_pct, 1),
        "note": ("a modell időjárási tagjainak pontos bontása a szokásos szint "
                 "százalékában; az 'alapszint' a modell és a naiv trend "
                 "alapszintjének különbsége"),
    }


def envelope_check(m, train: pd.DataFrame, feats_now: pd.DataFrame,
                   areas: pd.Series) -> list[dict]:
    """Mely mutatók idei országos értéke esik kívül a tanítóévek tartományán?

    Az összevetés országos, azonos (legutóbbi évi) terület-súlyokkal az idei és a
    historikus évekre is, hogy a két oldal összemérhető legyen.
    """
    hist = m._with_derived(train)
    hist = hist[hist["nuts_id"].isin(m.counties)]
    w_hist = hist["nuts_id"].map(areas).astype(float)
    now = m._with_derived(feats_now)
    w_now = now["nuts_id"].map(areas).astype(float)
    out = []
    for f in m.feature_names:
        yearly = ((hist[f] * w_hist).groupby(hist["crop_year"]).sum()
                  / w_hist.groupby(hist["crop_year"]).sum())
        cur = float((now[f] * w_now).sum() / w_now.sum())
        lo, hi = float(yearly.min()), float(yearly.max())
        if cur < lo or cur > hi:
            ext_year = int(yearly.idxmin() if cur < lo else yearly.idxmax())
            out.append({
                "feature": f,
                "label": feature_label(f),
                "direction": "below" if cur < lo else "above",
                "value": round(cur, 1),
                "hist_extreme": round(lo if cur < lo else hi, 1),
                "hist_extreme_year": ext_year,
            })
    return out
