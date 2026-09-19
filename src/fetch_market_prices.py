"""Hivatalos piaci árjegyzések letöltése az EU agrifood API-ból (DG AGRI).

A magyar adatok forrása a tagállami jelentés (AKI PÁIR → Európai Bizottság),
tehát tartalmilag AKI PÁIR-eredetű, de GÉPI, kulcs nélküli, stabil API-n át.
FONTOS: hivatalos "napi piaci ár" nem létezik (az AKI PÁIR is heti rendszerű) —
a jegyzések HETI (a cukor HAVI) hivatalosak; a jelentés naponta frissül, és a
referencia-időszakot minden tételnél kiírjuk.

Csak az itt VALIDÁLTAN elérhető termékek kerülnek be. Ami nem érhető el
megbízható, gépi forrásból (bioetanol, izocukor, keményítő, takarmánykeverék,
malac, pulyka, tenyészállat, víz), az NEM kerül a jelentésbe — a projekt
alapszabálya szerint inkább kihagyjuk, mint hogy bizonytalan adatot közöljünk.

Minden tétel nagyságrendi szanity-ellenőrzésen megy át (plauzibilis ártartomány);
ami kilóg vagy elavult (28 napnál régebbi heti jegyzés), az kimarad, jelezve.

Kimenet: web/data/market_prices.json
Futtatás: python -m src.fetch_market_prices
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta

import requests

from src import config

API = "https://ec.europa.eu/agrifood/api"
TIMEOUT = 60
STALE_DAYS_WEEKLY = 28    # ennél régebbi "legfrissebb" heti jegyzés gyanús
STALE_DAYS_MONTHLY = 120  # a havi cukorjegyzés átfutása hosszú

# (csoport, magyar címke, kör/megjegyzés, min–max plauzibilis ár, pénznem/egység)
# A min–max a szanity-kapu: ezen kívül eső értéket NEM közlünk.


def _num(price_str: str) -> float:
    """'€209,13' / '€224.29' / '630' -> float (az API vegyesen formáz)."""
    s = re.sub(r"[^\d,.\-]", "", str(price_str))
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    return float(s)


def _d(dmy: str) -> date:
    return datetime.strptime(dmy, "%d/%m/%Y").date()


def _get(path: str, params: dict) -> list:
    r = requests.get(f"{API}/{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    out = r.json()
    if not isinstance(out, list):
        raise ValueError(f"nem lista válasz: {str(out)[:120]}")
    return out


def _latest_weekly(rows: list, price_key: str = "price") -> dict | None:
    """A legfrissebb heti rekord (beginDate szerint, VALÓDI dátum-rendezéssel)."""
    dated = [r for r in rows if r.get("beginDate")]
    if not dated:
        return None
    return max(dated, key=lambda r: _d(r["beginDate"]))


def _weekly_item(rows: list, label: str, scope: str, lo: float, hi: float,
                 unit: str, today: date) -> dict | None:
    r = _latest_weekly(rows)
    if r is None:
        print(f"  [kimarad] {label}: nincs adat")
        return None
    price = _num(r["price"])
    end = _d(r["endDate"]) if r.get("endDate") else _d(r["beginDate"])
    if not (lo <= price <= hi):
        print(f"  [kimarad] {label}: ár a plauzibilis sávon kívül ({price} ∉ [{lo},{hi}])")
        return None
    if (today - end).days > STALE_DAYS_WEEKLY:
        print(f"  [kimarad] {label}: elavult jegyzés (utolsó hét vége: {end})")
        return None
    return {
        "label": label, "scope": scope, "freq": "heti",
        "price": round(price, 2), "unit": unit,
        "period": f"{_d(r['beginDate']).isoformat()} – {end.isoformat()}",
    }


_REGION_HU = {"Transdanubia": "Dunántúl", "Great Plain": "Alföld",
              "North Hungary": "Észak-Magyarország"}


def _cereal_item(rows: list, prods: tuple, label: str, lo: float, hi: float,
                 today: date) -> dict | None:
    """Gabonajegyzés a legfrissebb hétről: ha van országos átlag, azt közöljük;
    ha a forrás csak régiós termelői árakat jelent, azok egyszerű átlagát —
    a körben KIÍRVA, hány régióból. Minden régiós ár átmegy a szanity-kapun."""
    cand = [r for r in rows if r.get("productName") in prods and r.get("beginDate")]
    if not cand:
        print(f"  [kimarad] {label}: nincs adat")
        return None
    newest = max(_d(r["beginDate"]) for r in cand)
    week = [r for r in cand if _d(r["beginDate"]) == newest]
    # ha ugyanazon a héten több terméknév is szerepel, az elsődlegeset használjuk
    for p in prods:
        if any(r["productName"] == p for r in week):
            week = [r for r in week if r["productName"] == p]
            break
    end = max(_d(r["endDate"]) if r.get("endDate") else newest for r in week)
    if (today - end).days > STALE_DAYS_WEEKLY:
        print(f"  [kimarad] {label}: elavult jegyzés (utolsó hét vége: {end})")
        return None
    nat = [r for r in week if r.get("marketName") == "National Average"]
    if nat:
        prices, scope = [_num(nat[0]["price"])], "hazai, országos átlag"
    else:
        regional = {r.get("marketName"): _num(r["price"]) for r in week}
        prices = list(regional.values())
        if len(prices) == 1:
            # egyetlen régió jegyzése nem országos ár — a régiót névvel jelöljük
            scope = f"hazai, csak {_REGION_HU.get(next(iter(regional)), next(iter(regional)))}"
        else:
            scope = f"hazai, {len(prices)} régió átlaga"
    if not prices or not all(lo <= p <= hi for p in prices):
        print(f"  [kimarad] {label}: ár a plauzibilis sávon kívül ({prices})")
        return None
    return {
        "label": label, "scope": scope, "freq": "heti",
        "price": round(sum(prices) / len(prices), 2), "unit": "EUR/t",
        "period": f"{newest.isoformat()} – {end.isoformat()}",
    }


def _mean_by_week(rows: list) -> dict:
    """hét kezdőnapja -> a heti sorok egyszerű átlaga (tagállamok / piacok)."""
    by_week: dict = {}
    for r in rows:
        if r.get("beginDate"):
            by_week.setdefault(_d(r["beginDate"]), []).append(_num(r["price"]))
    return {wk: sum(v) / len(v) for wk, v in by_week.items()}


# A jelentés NAPONTA, felügyelet nélkül készül, ezért csak MAGÁTÓL, RENDSZERESEN
# frissülő jegyzés szerepelhet benne. Egy tétel akkor marad, ha:
#   - heti rendszerű (a havi, több hónapos késéssel érkező adat eleve kimarad),
#   - a legutolsó jegyzett hét vége legfeljebb FRESH_MAX_DAYS napos,
#   - az utolsó REGULAR_WINDOW_DAYS napban legalább REGULAR_MIN_QUOTES jegyzés jött,
#   - és az ár nem "befagyott": az utolsó FROZEN_WEEKS hétben legalább egyszer változott.
FRESH_MAX_DAYS = 21
REGULAR_WINDOW_DAYS = 42
REGULAR_MIN_QUOTES = 3
FROZEN_WEEKS = 8


def _filter_regular(items: list, skipped: list, today: date) -> list:
    kept = []
    for it in items:
        series = it.get("_series") or {}
        reason = None
        if it["freq"] != "heti":
            reason = "nem heti rendszerű adat"
        elif not series:
            reason = "nincs idősor"
        else:
            newest = max(series)
            recent = [k for k in series if 0 <= (newest - k).days < REGULAR_WINDOW_DAYS]
            last = sorted(series)[-FROZEN_WEEKS:]
            if (today - (newest + timedelta(days=6))).days > FRESH_MAX_DAYS:
                reason = f"a legutolsó jegyzés {FRESH_MAX_DAYS} napnál régebbi"
            elif len(recent) < REGULAR_MIN_QUOTES:
                reason = "rendszertelenül érkező jegyzés"
            elif len(last) == FROZEN_WEEKS and len({round(series[k], 2) for k in last}) == 1:
                reason = f"{FROZEN_WEEKS} hete változatlan (befagyott) ár"
        if reason:
            print(f"  [kimarad] {it['label']}: {reason}")
            skipped.append(it["label"])
        else:
            kept.append(it)
    return kept


def _attach_context(items: list, fx: dict | None) -> None:
    """Árkontextus tételenként a SAJÁT idősorából (nincs új forrás): forint-
    egyenérték, éves változás, és az ár helye az utolsó 52 hét sávjában.
    Heti TREND-mutatók, nem napi zaj. Kevés adatnál a kontextus kimarad."""
    for it in items:
        series = it.pop("_series", None) or {}
        newest = it.pop("_newest", None)
        if newest is None and it["freq"] == "heti":
            newest = date.fromisoformat(it["period"][:10])
        if fx:
            if it["unit"] == "EUR/100 kg":
                it["huf"], it["huf_unit"] = round(it["price"] * fx["rate"] / 100, 1), "Ft/kg"
            elif it["unit"] == "EUR/db":
                it["huf"], it["huf_unit"] = int(round(it["price"] * fx["rate"], -1)), "Ft/db"
            else:
                it["huf"], it["huf_unit"] = int(round(it["price"] * fx["rate"], -1)), "Ft/t"
        if not series or newest is None:
            continue
        monthly = it["freq"] == "havi"
        window = {k: v for k, v in series.items() if 0 <= (newest - k).days <= 365}
        if len(window) < (6 if monthly else 20):
            continue
        lo, hi = min(window.values()), max(window.values())
        cur = it["price"]
        it["lo52"], it["hi52"] = round(lo, 2), round(hi, 2)
        it["pos52"] = round(min(max((cur - lo) / (hi - lo), 0.0), 1.0), 3) if hi > lo else 0.5
        target = newest - timedelta(days=364)
        tol = 31 if monthly else 21
        cands = [(abs((k - target).days), k) for k in series if abs((k - target).days) <= tol]
        if cands:
            it["yoy_pct"] = round(100 * (cur / series[min(cands)[1]] - 1), 1)


_PARITY_HU = [("farm gate", "termelői ár"), ("departure from farm", "termelőtől elszállítva"),
              ("departure from silo", "silóból kitárolva"),
              ("deliver to first customer", "vevőhöz szállítva"),
              ("free on board", "FOB kikötő"), ("national average", "országos átlag")]
_MS_HU = {"HU": "Magyarország", "AT": "Ausztria", "SK": "Szlovákia", "RO": "Románia",
          "PL": "Lengyelország", "DE": "Németország"}


def collect_regional(today: date) -> list:
    """Regionális árkörkép (takarmánykukorica, takarmánybúza): tagállamonként a
    legfrissebb hét piacainak átlaga, a PARITÁS feltüntetésével. A paritások
    eltérnek (termelői, silóból kitárolt, szállított), ezért különbözetet
    ('bázist') szándékosan NEM számolunk — az almát körtével vetne össze."""
    my = f"{today.year - 1}/{today.year}" if today.month < 7 else f"{today.year}/{today.year + 1}"
    try:
        rows = _get("cereal/prices", {"memberStateCodes": ",".join(_MS_HU),
                                      "marketingYears": my})
    except Exception as e:
        print(f"  [info] regionális árkörkép nem elérhető: {e}")
        return []
    out = []
    for prod, label in (("Feed maize", "Takarmánykukorica"), ("Feed wheat", "Takarmánybúza")):
        cells = []
        for ms, name in _MS_HU.items():
            rs = [r for r in rows if r.get("memberStateCode") == ms
                  and r.get("productName") == prod and r.get("beginDate")]
            if not rs:
                continue
            newest = max(_d(r["beginDate"]) for r in rs)
            if (today - (newest + timedelta(days=6))).days > FRESH_MAX_DAYS:
                continue
            wk = [r for r in rs if _d(r["beginDate"]) == newest]
            nat = [r for r in wk if r.get("marketName") == "National Average"]
            use = nat or wk
            prices = [_num(r["price"]) for r in use]
            if not all(80 <= v <= 600 for v in prices):
                continue
            stage = (use[0].get("stageName") or "").lower()
            parity = next((hu_ for key, hu_ in _PARITY_HU if key in stage), "egyéb paritás")
            cells.append({"ms": ms, "name": name, "price": round(sum(prices) / len(prices), 2),
                          "parity": parity, "week": newest.isoformat()})
        if len(cells) >= 3 and any(c["ms"] == "HU" for c in cells):
            out.append({"label": label, "cells": cells})
    return out


def collect(today: date) -> tuple[list, list]:
    """(tételek csoportosítva, kihagyások listája)"""
    items, skipped = [], []
    my = f"{today.year - 1}/{today.year}" if today.month < 7 else f"{today.year}/{today.year + 1}"
    my_prev = f"{int(my[:4])-1}/{int(my[:4])}"

    # -- Gabona / takarmány (HU, heti) --------------------------------------- #
    try:
        rows = _get("cereal/prices", {"memberStateCodes": "HU",
                                      "marketingYears": f"{my_prev},{my}"})
        # A 2026/27-es gazdasági évtől a forrás az étkezési búzát "Milling wheat"
        # néven, és országos átlag helyett jellemzően RÉGIÓS (Dunántúl, Alföld,
        # Észak-Magyarország) termelői áron jelenti — mindkét alakot kezeljük.
        for prods, label in [(("Milling wheat", "Breadmaking common wheat"), "Étkezési búza"),
                             (("Feed wheat",), "Takarmánybúza"),
                             (("Feed maize",), "Takarmánykukorica"),
                             (("Feed barley",), "Takarmányárpa")]:
            it = _cereal_item(rows, prods, label, 80, 600, today)
            if it:
                it["_series"] = _weekly_series(rows, "productName", prods)
                it["group"] = "Gabona és takarmány"
                items.append(it)
            else:
                skipped.append(label)
    except Exception as e:
        print(f"  [hiba] gabona: {e}")
        skipped += ["Étkezési búza", "Takarmánybúza", "Takarmánykukorica", "Takarmányárpa"]

    # -- Olajos magvak, darák, olaj (kizárólag hazai heti jegyzések) ---------- #
    try:
        rows = _get("oilseeds/prices", {"memberStateCodes": "HU",
                                        "marketingYears": f"{my_prev},{my}"})
        # A napraforgómagnál a forrás KÉT típust jelent (hagyományos és magas
        # olajsavas, ~10–15% felárral): külön sorok, különben a két ár keveredne.
        for prod, ptype, label, lo, hi in [
                ("Sunflower seed", "Standard", "Napraforgómag (hagyományos)", 200, 900),
                ("Sunflower seed", "High-oleic", "Napraforgómag (magas olajsavas)", 200, 1000),
                ("Rapeseed", None, "Repcemag", 250, 900),
                ("Sunflower seed meal", None, "Napraforgódara", 100, 600),
                ("Rapeseed meal", None, "Repcedara", 100, 600),
                ("Crude sunflower oil", None, "Napraforgóolaj (nyers)", 500, 2500)]:
            sub = [r for r in rows if r.get("product") == prod
                   and (ptype is None or r.get("productType") == ptype)]
            it = _weekly_item(sub, label, "hazai", lo, hi, "EUR/t", today)
            if it:
                it["_series"] = _mean_by_week(sub)
                it["group"] = "Olajos termékek"
                items.append(it)
            else:
                skipped.append(label)
    except Exception as e:
        print(f"  [hiba] olajos: {e}")
        skipped += ["Napraforgómag (hagyományos)", "Napraforgómag (magas olajsavas)",
                    "Repcemag", "Napraforgódara", "Repcedara",
                    "Napraforgóolaj (nyers)"]

    # -- Sertés (HU, heti, hasított S és E osztály) -------------------------- #
    try:
        rows = _get("pigmeat/prices", {"memberStateCodes": "HU",
                                       "years": f"{today.year - 1},{today.year}"})
        for cls, label in [("S", "Vágósertés (hasított, S oszt.)"),
                           ("E", "Vágósertés (hasított, E oszt.)")]:
            sub = [r for r in rows if r.get("pigClass") == cls]
            it = _weekly_item(sub, label, "hazai", 80, 400, "EUR/100 kg", today)
            if it:
                it["_series"] = _mean_by_week(sub)
                it["group"] = "Sertés"
                items.append(it)
            else:
                skipped.append(label)
        sub = [r for r in rows if r.get("pigClass") == "Piglet"]
        it = _weekly_item(sub, "Malac", "hazai, kb. havonta frissül", 15, 150,
                          "EUR/db", today)
        if it:
            it["_series"] = _mean_by_week(sub)
            it["group"] = "Sertés"
            items.append(it)
        else:
            skipped.append("Malac")
    except Exception as e:
        print(f"  [hiba] sertés: {e}")
        skipped += ["Vágósertés (hasított, S oszt.)", "Vágósertés (hasított, E oszt.)", "Malac"]

    # -- Baromfi (HU, heti, vágott/darabolt csirke) -------------------------- #
    try:
        rows = _get("poultry/prices", {"memberStateCodes": "HU",
                                       "years": f"{today.year - 1},{today.year}"})
        by_prod = {}
        for r in rows:
            by_prod.setdefault(r.get("productName"), []).append(r)
        for prod, label, lo, hi in [("Whole broiler (65%)", "Egész csirke (65%-os)", 120, 500),
                                    ("Breast Fillet", "Csirkemell-filé", 250, 1200),
                                    ("Legs", "Csirkecomb", 100, 600)]:
            it = _weekly_item(by_prod.get(prod, []), label, "hazai", lo, hi,
                              "EUR/100 kg", today)
            if it:
                it["_series"] = _mean_by_week(by_prod.get(prod, []))
                it["group"] = "Baromfi"
                items.append(it)
            else:
                skipped.append(label)
    except Exception as e:
        print(f"  [hiba] baromfi: {e}")
        skipped += ["Egész csirke (65%-os)", "Csirkemell-filé", "Csirkecomb"]

    # -- Cukor (EU-átlag, HAVI) ---------------------------------------------- #
    try:
        rows = _get("sugar/prices", {})
        eu = [r for r in rows if r.get("sugarRegion") == "EU Average"
              and r.get("price") not in (None, "", "-")]

        def ym_key(r):
            # 'ym' pl. '2006/07' nem rendezhető közvetlenül; a marketingYear+hónap
            # sorrendjét a rekordok sorrendje adja — a legbiztosabb a tényleges
            # (év, hónap) kulcs a marketingYearMonth + marketingYear mezőkből
            months = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May",
                      "Jun", "Jul", "Aug", "Sep"]
            m = r.get("marketingYearMonth", "")
            years = r.get("marketingYear", "0/0").split("/")
            if m not in months or len(years) != 2:
                return (0, 0)
            idx = months.index(m)
            year = int(years[0]) if idx <= 2 else int(years[1])
            month = [10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9][idx]
            return (year, month)

        eu = [r for r in eu if ym_key(r) != (0, 0)]
        if eu:
            latest = max(eu, key=ym_key)
            y, m = ym_key(latest)
            price = _num(latest["price"])
            ref = date(y, m, 1)
            if 300 <= price <= 1500 and (today - ref).days <= STALE_DAYS_MONTHLY:
                items.append({
                    "label": "Kristálycukor", "group": "Feldolgozóipari termékek",
                    "scope": "EU-átlag (hazai bontás nincs)", "freq": "havi",
                    "price": round(price, 2), "unit": "EUR/t",
                    "period": f"{y}. {m:02d}. hó",
                    "_series": {date(*ym_key(r), 1): _num(r["price"]) for r in eu},
                    "_newest": ref,
                })
            else:
                skipped.append("Kristálycukor")
        else:
            skipped.append("Kristálycukor")
    except Exception as e:
        print(f"  [hiba] cukor: {e}")
        skipped.append("Kristálycukor")

    return items, skipped


# --------------------------------------------------------------------------- #
# Forintosítási alap: friss termelői ár (utolsó 4 jegyzett hét átlaga) × hivatalos
# EUR/HUF árfolyam. Ezt használja a predict_live a termelési érték számításához,
# hogy az 1. oldal forintja és a 4. oldal árai UGYANARRA az árszintre épüljenek.
# --------------------------------------------------------------------------- #
VALUATION_WEEKS = 4
_HU_MONTH_ADJ = ["", "januári", "februári", "márciusi", "áprilisi", "májusi",
                 "júniusi", "júliusi", "augusztusi", "szeptemberi", "októberi",
                 "novemberi", "decemberi"]

# termény -> (végpont, névmező, terméknevek, plauzibilis EUR/t sáv, alap leírása)
_VALUATION_SPEC = {
    "wheat": ("cereal/prices", "productName",
              ("Milling wheat", "Breadmaking common wheat", "Feed wheat"),
              80, 600, "étkezési és takarmánybúza termelői árainak átlaga"),
    "corn": ("cereal/prices", "productName", ("Feed maize",),
             80, 600, "takarmánykukorica termelői ára"),
    "barley": ("cereal/prices", "productName", ("Feed barley",),
               80, 600, "takarmányárpa termelői ára"),
    "sunflower": ("oilseeds/prices", "product", ("Sunflower seed",),
                  200, 900, "hagyományos napraforgómag termelői ára"),
    "rapeseed": ("oilseeds/prices", "product", ("Rapeseed",),
                 250, 900, "repcemag termelői ára"),
}


def _weekly_series(rows: list, name_key: str, names: tuple) -> dict:
    """hét kezdőnapja -> heti ár: országos átlag, annak híján a heti sorok
    (régiók, ill. terméknevek) egyszerű átlaga."""
    by_week: dict = {}
    for r in rows:
        if r.get(name_key) in names and r.get("beginDate"):
            by_week.setdefault(_d(r["beginDate"]), []).append(r)
    out = {}
    for wk, rs in by_week.items():
        nat = [r for r in rs if r.get("marketName") == "National Average"]
        use = nat or rs
        out[wk] = sum(_num(r["price"]) for r in use) / len(use)
    return out


def _fx_rate() -> dict | None:
    """Hivatalos EUR/HUF: MNB középárfolyam, tartalékként EKB referencia-árfolyam.
    Plauzibilitási kapu: 300–500 Ft/EUR."""
    try:
        import html as _html
        body = ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap='
                '"http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
                '<GetCurrentExchangeRates xmlns="http://www.mnb.hu/webservices/" />'
                '</soap:Body></soap:Envelope>')
        r = requests.post(
            "http://www.mnb.hu/arfolyamok.asmx", data=body, timeout=TIMEOUT,
            headers={"Content-Type": "text/xml; charset=utf-8",
                     "SOAPAction": '"http://www.mnb.hu/webservices/'
                                   'MNBArfolyamServiceSoap/GetCurrentExchangeRates"'})
        r.raise_for_status()
        t = _html.unescape(r.text)
        day = re.search(r'Day date="([^"]+)"', t)
        eur = re.search(r'curr="EUR">([^<]+)<', t)
        rate = float(eur.group(1).replace(",", "."))
        if 300 <= rate <= 500:
            return {"rate": round(rate, 2), "source": "MNB hivatalos középárfolyam",
                    "date": day.group(1)}
    except Exception as e:
        print(f"  [info] MNB-árfolyam nem elérhető ({e}) — EKB-tartalék")
    try:
        r = requests.get("https://data-api.ecb.europa.eu/service/data/EXR/"
                         "D.HUF.EUR.SP00.A", timeout=TIMEOUT,
                         params={"lastNObservations": 1, "format": "csvdata"})
        r.raise_for_status()
        lines = [ln for ln in r.text.strip().splitlines() if ln]
        head, last = lines[0].split(","), lines[-1].split(",")
        rate = float(last[head.index("OBS_VALUE")])
        if 300 <= rate <= 500:
            return {"rate": round(rate, 2), "source": "EKB referencia-árfolyam",
                    "date": last[head.index("TIME_PERIOD")]}
    except Exception as e:
        print(f"  [hiba] EKB-árfolyam sem elérhető: {e}")
    return None


def collect_valuation(today: date, fx: dict | None) -> dict | None:
    """Terményenkénti friss forintosítási ár. Ha az árfolyam vagy egy termény
    ára nem megbízható, az a termény kimarad (a predict_live ilyenkor az éves
    Eurostat-átlagárra esik vissza) — elavult vagy kilógó árral nem forintosítunk."""
    if fx is None:
        return None
    my = f"{today.year - 1}/{today.year}" if today.month < 7 else f"{today.year}/{today.year + 1}"
    my_prev = f"{int(my[:4])-1}/{int(my[:4])}"
    cache: dict = {}
    crops = {}
    for crop, (path, key, names, lo, hi, basis) in _VALUATION_SPEC.items():
        try:
            if path not in cache:
                cache[path] = _get(path, {"memberStateCodes": "HU",
                                          "marketingYears": f"{my_prev},{my}"})
            src_rows = cache[path]
            if crop == "sunflower":  # csak a hagyományos típus (a HO felára torzítana)
                src_rows = [r for r in src_rows if r.get("productType") == "Standard"]
            series = _weekly_series(src_rows, key, names)
            if not series:
                continue
            weeks = sorted(series)[-VALUATION_WEEKS:]
            newest_end = weeks[-1] + timedelta(days=6)
            if (today - newest_end).days > STALE_DAYS_WEEKLY:
                print(f"  [érték] {crop}: elavult ársor ({weeks[-1]}) — éves árra esik vissza")
                continue
            vals = [series[w] for w in weeks]
            if not all(lo <= v <= hi for v in vals):
                print(f"  [érték] {crop}: ár a plauzibilis sávon kívül {vals}")
                continue
            eur = sum(vals) / len(vals)
            crops[crop] = {
                "eur_per_t": round(eur, 2),
                "huf_per_t": int(round(eur * fx["rate"], -1)),
                "weeks": len(weeks),
                "period": f"{weeks[0].isoformat()} – {newest_end.isoformat()}",
                "basis": basis,
                "phrase": f"{newest_end.year}. {_HU_MONTH_ADJ[newest_end.month]} termelői áron",
            }
        except Exception as e:
            print(f"  [érték] {crop}: {e}")
    if not crops:
        return None
    return {"fx": fx, "crops": crops}


# --------------------------------------------------------------------------- #
# Hivatalos EU-termésbecslés (DG AGRI, tagállami adatokból) — VISZONYÍTÁSI PONT a
# saját becslésünk mellé. Aratás után ez tartalmazza a betakarítási jelentéseket,
# amelyeket egy időjárás-modell nem láthat (2026 tanulsága). BECSLÉSKÉNT címkézzük,
# nem tényként: havonta frissül, és nem mindenhol azonos a végleges KSH-adattal.
# Az őszi árpára nincs összemérhető sor (a forrás csak az ÖSSZES árpát közli).
# --------------------------------------------------------------------------- #
_OFFICIAL_SPEC = {
    "wheat": ("cereal/production", ("Soft wheat", "Durum wheat"), (2.0, 9.0)),
    "corn": ("cereal/production", ("Maize",), (1.5, 12.0)),
    "sunflower": ("oilseeds/production", ("Sunflower seed",), (0.8, 4.5)),
    "rapeseed": ("oilseeds/production", ("Rapeseed",), (1.0, 5.5)),
}


def collect_official(today: date) -> dict:
    """termény -> {year, yield_t_ha, production_kt, area_kha}; csak plauzibilis,
    a folyó termésévre vonatkozó sorok. Hibánál üres dict (a megjelenítés kimarad)."""
    out: dict = {}
    cache: dict = {}
    for crop, (path, names, (lo, hi)) in _OFFICIAL_SPEC.items():
        try:
            if path not in cache:
                cache[path] = _get(path, {"memberStateCodes": "HU",
                                          "years": str(today.year)})
            rs = [r for r in cache[path] if r.get("crop") in names
                  and r.get("year") == today.year]
            if len(rs) != len(names):
                continue
            area = sum(float(r["area"]) for r in rs)
            prod = sum(float(r["grossProduction"]) for r in rs)
            if area <= 0:
                continue
            y = prod / area
            if not (lo <= y <= hi):
                print(f"  [hivatalos] {crop}: hozam a plauzibilis sávon kívül ({y:.2f})")
                continue
            out[crop] = {"year": today.year, "yield_t_ha": round(y, 2),
                         "production_kt": round(prod, 1), "area_kha": round(area, 1)}
        except Exception as e:
            print(f"  [hivatalos] {crop}: {e}")
    return out


# gépi forrásból NEM elérhető kérések — tudatosan nem közöljük
NOT_AVAILABLE = [
    "Bioetanol (nincs nyilvános hivatalos jegyzés)",
    "Izocukor (nincs nyilvános jegyzés)",
    "Keményítő (nincs nyilvános jegyzés)",
    "Takarmánykeverékek (AKI-kiadványban létezik, gépi forrás nincs)",
    "Szójadara (hazai jegyzés nincs; külföldi átlagot nem közlünk)",
    "Vágópulyka / pulykahús (nincs a nyilvános API-ban)",
    "Tenyészállat (nincs hivatalos árjegyzés)",
    "Víz (szabályozott díj, nincs piaci árjegyzés)",
]


def main() -> None:
    today = date.today()
    print(f"Piaci árjegyzések letöltése (EU agrifood API), ma: {today}")
    items, skipped = collect(today)
    items = _filter_regular(items, skipped, today)
    fx = _fx_rate()
    _attach_context(items, fx)
    if len(items) < 8:
        # a blokk értelmét veszti, ha a tételek zöme hiányzik — inkább nem frissítünk
        sys.exit(f"HIBA: csak {len(items)} tétel jött össze (várt >= 8) — nem írjuk felül "
                 f"a meglévő árakat. Kihagyva: {skipped}")
    payload = {
        "updated_at": today.isoformat(),
        "source": ("Európai Bizottság (DG AGRI) agrifood API; a magyar adatok a "
                   "tagállami jelentésből (AKI PÁIR) származnak"),
        "note": ("Hivatalos HETI jegyzések (a cukor havi) — napi hivatalos "
                 "árjegyzés nem létezik; a jelentés naponta frissül, a "
                 "referencia-időszak tételenként jelölve."),
        "items": items,
        "valuation": collect_valuation(today, fx),
        "regional": collect_regional(today),
        "skipped_today": skipped,
        "not_available": NOT_AVAILABLE,
    }
    official = collect_official(today)
    if official:  # sikertelen letöltésnél a meglévő fájl marad (nem írjuk felül üressel)
        (config.WEB_DATA / "official_estimates.json").write_text(json.dumps({
            "updated_at": today.isoformat(),
            "source": ("Európai Bizottság (DG AGRI) tagállami termésbecslése; havonta "
                       "frissül, aratás után a betakarítási jelentéseket is tartalmazza"),
            "crops": official}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[ok] official_estimates.json: {', '.join(official)}")
    out = config.WEB_DATA / "market_prices.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ok] {out.name}: {len(items)} tétel"
          + (f", kihagyva ma: {', '.join(skipped)}" if skipped else ""))


if __name__ == "__main__":
    main()
