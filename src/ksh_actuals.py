"""A KSH vármegyei termésadata (tény) a lezárt termésévekre.

Ha a KSH egy termésévre már közölte a vármegyei termésátlagot, a kimenetben
(PDF, web) a pontos szám áll, nem a modell becslése. A modell záró becslése
mellékes adatként megmarad (national.actual.model_yield_t_ha, a vármegyei
sorokban model_yield_t_ha).

A modell tanítóadatához (data/raw/ksh, a panel) ez a modul NEM nyúl: a friss
KSH-táblákat külön fájlba olvassa (web/data/ksh_actuals.json). Új év betanítása
modellváltozás, az csak a mérési kapun át mehet.

Futtatás:  python -m src.ksh_actuals        (letöltés + web/data/ksh_actuals.json)
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

import requests

from src import config
from src.build_panel import parse_ksh_csv
from src.export_history import ksh_national_official, yield_history_json
from src.fetch_ksh import find_download_links

OUT = config.WEB_DATA / "ksh_actuals.json"
YEARS_KEPT = 3        # a legutóbbi ennyi KSH-évet tároljuk
MIN_COUNTIES = 15     # ennyi vármegyei adat nélkül az évet nem tekintjük közzétettnek
TIMEOUT = 60


def _crop_key(fc: dict) -> str | None:
    return next((k for k, s in config.CROPS.items() if s["label"] == fc.get("crop")), None)


def parse_file(crop: str, path: Path) -> dict[int, dict]:
    """Egy KSH csv-ből a közzétett évek: {év: {"national": {...}, "counties": {nuts: {...}}}}."""
    wide = parse_ksh_csv(crop, path)
    nat = ksh_national_official(crop, path)
    out: dict[int, dict] = {}
    for year, y in nat["yield_t_ha"].items():
        area, prod = nat["area_ha"].get(year), nat["production_t"].get(year)
        if area is None or prod is None or not 0.3 <= y <= 20:
            continue
        rows = wide[(wide["crop_year"] == year) & wide["yield_t_ha"].notna()]
        counties = {r.nuts_id: {"yield_t_ha": round(float(r.yield_t_ha), 3),
                                "area_ha": None if r.area_ha != r.area_ha else float(r.area_ha),
                                "production_t": None if r.production_t != r.production_t else float(r.production_t)}
                    for r in rows.itertuples()}
        if len(counties) < MIN_COUNTIES:
            continue  # részleges közlés: nem tekintjük ténynek
        out[int(year)] = {"national": {"yield_t_ha": round(y, 3), "area_ha": area, "production_t": prod},
                          "counties": counties}
    return out


def fetch_crop(crop: str) -> dict:
    spec = config.CROPS[crop]
    page_url = spec["ksh_page"]
    page = requests.get(page_url, timeout=TIMEOUT, headers={"User-Agent": "wheat-forecast/1.0"})
    page.raise_for_status()
    html = page.content.decode(config.KSH_ENCODING, errors="replace")
    links = find_download_links(html, page_url)
    if "csv" not in links:
        raise RuntimeError(f"nincs csv-link: {page_url}")
    raw = requests.get(links["csv"], timeout=TIMEOUT, headers={"User-Agent": "wheat-forecast/1.0"})
    raw.raise_for_status()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / f"{spec['ksh_slug']}.csv"
        path.write_bytes(raw.content)
        years = parse_file(crop, path)
        if not years:  # pl. újabb szintjelölés-váltás: ne írja felül csendben a jó adatot
            raise RuntimeError("a táblából egy év sem olvasható ki (formátumváltás?)")
        first = raw.content.decode(config.KSH_ENCODING, errors="replace").splitlines()[0]
    table = (re.match(r"\s*(\d+(?:\.\d+)+\.)", first) or [None, ""])[1]
    m = re.search(r"Utolsó frissítés:\s*(\d{4}\.\s*\w+\s+\d{1,2}\.)", html)
    keep = sorted(years)[-YEARS_KEPT:]
    # ha a frissítés napja nem olvasható ki, a letöltés napja áll helyette (ne "None" kerüljön ki)
    updated = m.group(1).strip() if m else f"letöltve: {date.today():%Y. %m. %d.}"
    return {"table": table, "updated": updated, "page": page_url,
            "years": {str(y): years[y] for y in keep}}


def load() -> dict:
    if not OUT.exists():
        return {}
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main() -> int:
    old = load().get("crops", {})
    crops, failed = {}, []
    for crop, spec in config.CROPS.items():
        if not spec.get("ksh_page"):
            continue
        try:
            crops[crop] = fetch_crop(crop)
            ys = ", ".join(f"{y}: {v['national']['yield_t_ha']:.2f}" for y, v in crops[crop]["years"].items())
            print(f"[ok] {crop}: {ys} t/ha (KSH {crops[crop]['table']}, frissítve: {crops[crop]['updated']})")
        except (Exception, SystemExit) as e:  # hálózat / formátum (a parser sys.exit-tel áll meg): a korábbi adat marad
            failed.append(crop)
            if crop in old:
                crops[crop] = old[crop]
            print(f"::warning::KSH {crop}: {e}")
    OUT.write_text(json.dumps({"fetched": date.today().isoformat(), "crops": crops},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if failed and len(failed) == len(crops) else 0


# --------------------------------------------------------------------------- #
# A tény ráhelyezése egy forecast-objektumra (PDF és web közös forrása)
# --------------------------------------------------------------------------- #
def _ranks(crop: str, crop_year: int, anomaly_pct: float) -> tuple[int, int] | None:
    p = yield_history_json(crop)
    if not p.exists():
        return None
    nat = json.loads(p.read_text(encoding="utf-8"))["national"]
    anoms = []
    for y, v in zip(nat["years"], nat["yields"]):
        if y == crop_year:
            continue
        tr = nat["trend_intercept"] + nat["trend_slope"] * (y - nat["trend_base_year"])
        anoms.append(100 * (v - tr) / tr)
    return sum(1 for a in anoms if a < anomaly_pct) + 1, len(anoms) + 1


def apply(fc: dict, data: dict | None = None) -> bool:
    """A KSH-tényt teszi a modell száma helyére, ahol a KSH már közölte.
    Idempotens: ismételt hívás a megőrzött modellszámból indul. True, ha a
    termésév tényadatot kapott."""
    data = load() if data is None else data
    crop = _crop_key(fc)
    entry = (data.get("crops") or {}).get(crop) or {}
    years = entry.get("years") or {}
    n = fc.get("national") or {}
    if not years or not n:
        return False
    src = {"source": "KSH", "table": entry.get("table"), "updated": entry.get("updated")}

    # előző évi tény: ha a KSH a termésév előtti évet már közölte
    prev = years.get(str(fc["crop_year"] - 1))
    if prev and n.get("prev_year") is not None and n["prev_year"] < fc["crop_year"] - 1:
        n["prev_year"] = fc["crop_year"] - 1
        n["prev_year_yield_t_ha"] = round(prev["national"]["yield_t_ha"], 2)
        n["prev_year_source"] = src
        if n.get("predicted_yield_t_ha") is not None:
            n["yoy_pct"] = round(100 * (n["predicted_yield_t_ha"] - n["prev_year_yield_t_ha"])
                                 / n["prev_year_yield_t_ha"], 1)

    act = years.get(str(fc["crop_year"]))
    if not act or n.get("predicted_yield_t_ha") is None:
        return False
    model = (n.get("actual") or {}).get("model_yield_t_ha", n["predicted_yield_t_ha"])
    y, trend = act["national"]["yield_t_ha"], n["trend_t_ha"]
    n["actual"] = {**src, "model_yield_t_ha": model}
    n["predicted_yield_t_ha"] = round(y, 2)
    n["anomaly_pct"] = round(100 * (y - trend) / trend, 1)
    n["pred_low_t_ha"] = n["pred_high_t_ha"] = None
    n.pop("official_estimate", None)  # a tény mellett a becslés nem kell
    n["weights"] = "KSH, Ország összesen"
    if n.get("prev_year_yield_t_ha"):
        n["yoy_pct"] = round(100 * (y - n["prev_year_yield_t_ha"]) / n["prev_year_yield_t_ha"], 1)
    rk = _ranks(crop, fc["crop_year"], n["anomaly_pct"])
    if rk:
        n["rank_from_worst"], n["rank_total"] = rk
    v = n.get("value")
    if v:
        price, area, prod = v["price_huf_per_t"], act["national"]["area_ha"], act["national"]["production_t"]
        v.update({
            "area_ha": round(area), "area_year": fc["crop_year"],
            "production_mt": round(prod / 1e6, 2),
            "production_value_bn_huf": round(prod * price / 1e9, 1),
            "trend_gap_bn_huf": round((y - trend) * area * price / 1e9, 1),
            "note": (v.get("note", "").split("; terület:")[0].split("; termés és terület:")[0]
                     + f"; termés és terület: a KSH {fc['crop_year']}. évi adata"),
        })
    price = v["price_huf_per_t"] if v else None
    for r in fc.get("counties", []):
        if r.get("predicted_yield_t_ha") is None and "model_yield_t_ha" not in r:
            continue  # a modellben sem szerepel (Budapest)
        m = r.get("model_yield_t_ha", r["predicted_yield_t_ha"])
        ma = r.get("model_anomaly_pct", r["anomaly_pct"])
        base = m / (1 + ma / 100)  # a vármegye szokásos szintje, amihez a modell mért
        r["model_yield_t_ha"], r["model_anomaly_pct"] = m, ma
        r["low"] = r["high"] = None
        k = act["counties"].get(r["nuts_id"])
        if not k:  # a KSH erre a vármegyére nem közöl adatot: modellszámot nem keverünk be
            r.update({"predicted_yield_t_ha": None, "anomaly_pct": None,
                      "note": "a KSH erre a vármegyére nem közöl adatot"})
            r.pop("value_bn_huf", None); r.pop("trend_gap_bn_huf", None)
            continue
        r["predicted_yield_t_ha"] = round(k["yield_t_ha"], 2)
        r["anomaly_pct"] = round(100 * (k["yield_t_ha"] - base) / base, 1)
        if price and "value_bn_huf" in r and k.get("production_t") and k.get("area_ha"):
            r["value_bn_huf"] = round(k["production_t"] * price / 1e9, 1)
            r["trend_gap_bn_huf"] = round((k["yield_t_ha"] - base) * k["area_ha"] * price / 1e9, 1)
    return True


if __name__ == "__main__":
    sys.exit(main())
