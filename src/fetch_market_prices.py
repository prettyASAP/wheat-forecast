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


def collect(today: date) -> tuple[list, list]:
    """(tételek csoportosítva, kihagyások listája)"""
    items, skipped = [], []
    my = f"{today.year - 1}/{today.year}" if today.month < 7 else f"{today.year}/{today.year + 1}"
    my_prev = f"{int(my[:4])-1}/{int(my[:4])}"

    # -- Gabona / takarmány (HU, heti) --------------------------------------- #
    try:
        rows = _get("cereal/prices", {"memberStateCodes": "HU",
                                      "marketingYears": f"{my_prev},{my}"})
        by_prod = {}
        for r in rows:
            if r.get("marketName") == "National Average":
                by_prod.setdefault(r["productName"], []).append(r)
        for prod, label in [("Breadmaking common wheat", "Étkezési búza"),
                            ("Feed wheat", "Takarmánybúza"),
                            ("Feed maize", "Takarmánykukorica"),
                            ("Feed barley", "Takarmányárpa")]:
            it = _weekly_item(by_prod.get(prod, []), label, "HU, országos átlag",
                              80, 600, "EUR/t", today)
            if it:
                it["group"] = "Gabona és takarmány"
                items.append(it)
            else:
                skipped.append(label)
    except Exception as e:
        print(f"  [hiba] gabona: {e}")
        skipped += ["Étkezési búza", "Takarmánybúza", "Takarmánykukorica", "Takarmányárpa"]

    # -- Olajos magvak, darák, olaj (HU heti; szójadara: EU-tagállamok) ------ #
    try:
        rows = _get("oilseeds/prices", {"memberStateCodes": "HU",
                                        "marketingYears": f"{my_prev},{my}"})
        by_prod = {}
        for r in rows:
            by_prod.setdefault(r.get("product"), []).append(r)
        for prod, label, lo, hi in [("Sunflower seed", "Napraforgómag", 200, 900),
                                    ("Rapeseed", "Repcemag", 250, 900),
                                    ("Sunflower seed meal", "Napraforgódara", 100, 600),
                                    ("Rapeseed meal", "Repcedara", 100, 600),
                                    ("Crude sunflower oil", "Napraforgóolaj (nyers)", 500, 2500)]:
            it = _weekly_item(by_prod.get(prod, []), label, "HU", lo, hi, "EUR/t", today)
            if it:
                it["group"] = "Olajos termékek"
                items.append(it)
            else:
                skipped.append(label)
    except Exception as e:
        print(f"  [hiba] olajos: {e}")
        skipped += ["Napraforgómag", "Repcemag", "Napraforgódara", "Repcedara",
                    "Napraforgóolaj (nyers)"]

    try:
        rows = _get("oilseeds/prices", {"products": "soya meal",
                                        "marketingYears": my})
        # nincs HU-jegyzés: a jelentő tagállamok legfrissebb hetének átlaga
        latest_by_ms = {}
        for r in rows:
            ms = r.get("memberStateCode")
            if not r.get("beginDate"):
                continue
            cur = latest_by_ms.get(ms)
            if cur is None or _d(r["beginDate"]) > _d(cur["beginDate"]):
                latest_by_ms[ms] = r
        if latest_by_ms:
            newest = max(_d(r["beginDate"]) for r in latest_by_ms.values())
            week = [r for r in latest_by_ms.values() if _d(r["beginDate"]) == newest]
            prices = [_num(r["price"]) for r in week]
            avg = sum(prices) / len(prices)
            end = max(_d(r["endDate"]) for r in week)
            if 150 <= avg <= 900 and (today - end).days <= STALE_DAYS_WEEKLY:
                items.append({
                    "label": "Szójadara", "group": "Olajos termékek",
                    "scope": f"EU-átlag ({len(week)} tagállam; HU-jegyzés nincs)",
                    "freq": "heti", "price": round(avg, 2), "unit": "EUR/t",
                    "period": f"{newest.isoformat()} – {end.isoformat()}",
                })
            else:
                skipped.append("Szójadara")
        else:
            skipped.append("Szójadara")
    except Exception as e:
        print(f"  [hiba] szójadara: {e}")
        skipped.append("Szójadara")

    # -- Sertés (HU, heti, hasított S és E osztály) -------------------------- #
    try:
        rows = _get("pigmeat/prices", {"memberStateCodes": "HU",
                                       "years": f"{today.year - 1},{today.year}"})
        for cls, label in [("S", "Vágósertés (hasított, S oszt.)"),
                           ("E", "Vágósertés (hasított, E oszt.)")]:
            sub = [r for r in rows if r.get("pigClass") == cls]
            it = _weekly_item(sub, label, "HU", 80, 400, "EUR/100 kg", today)
            if it:
                it["group"] = "Sertés"
                items.append(it)
            else:
                skipped.append(label)
    except Exception as e:
        print(f"  [hiba] sertés: {e}")
        skipped += ["Vágósertés (hasított, S oszt.)", "Vágósertés (hasított, E oszt.)"]

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
            it = _weekly_item(by_prod.get(prod, []), label, "HU", lo, hi,
                              "EUR/100 kg", today)
            if it:
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
                    "scope": "EU-átlag (HU-bontás nincs)", "freq": "havi",
                    "price": round(price, 2), "unit": "EUR/t",
                    "period": f"{y}. {m:02d}. hó",
                })
            else:
                skipped.append("Kristálycukor")
        else:
            skipped.append("Kristálycukor")
    except Exception as e:
        print(f"  [hiba] cukor: {e}")
        skipped.append("Kristálycukor")

    return items, skipped


# gépi forrásból NEM elérhető kérések — tudatosan nem közöljük
NOT_AVAILABLE = [
    "Bioetanol (nincs nyilvános hivatalos jegyzés)",
    "Izocukor (nincs nyilvános jegyzés)",
    "Keményítő (nincs nyilvános jegyzés)",
    "Takarmánykeverékek (AKI-kiadványban létezik, gépi forrás nincs)",
    "Malac (nincs a nyilvános API-ban)",
    "Vágópulyka / pulykahús (nincs a nyilvános API-ban)",
    "Tenyészállat (nincs hivatalos árjegyzés)",
    "Víz (szabályozott díj, nincs piaci árjegyzés)",
]


def main() -> None:
    today = date.today()
    print(f"Piaci árjegyzések letöltése (EU agrifood API), ma: {today}")
    items, skipped = collect(today)
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
        "skipped_today": skipped,
        "not_available": NOT_AVAILABLE,
    }
    out = config.WEB_DATA / "market_prices.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ok] {out.name}: {len(items)} tétel"
          + (f", kihagyva ma: {', '.join(skipped)}" if skipped else ""))


if __name__ == "__main__":
    main()
