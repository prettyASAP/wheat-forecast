"""Napi vezetői PDF-jelentés — HTML→PDF (Claude Design-alapú szedés).

A vizuális tervet a felhasználó a Claude Designban készítette
(„Napi jelentés.dc.html"); ez a modul azt reprodukálja ÖNÁLLÓ, szabványos
nyomtatási HTML-ként, ÉLŐ adatra kötve, és headless Chromiummal (Playwright)
rendereli A4 PDF-fé. Ez váltja le a korábbi matplotlib-generátort.

Kimenet:
  web/jelentes/assets/map_{crop}.png        (élő adatból rajzolt choropleth-ek)
  web/jelentes/jelentes_YYYY-MM-DD.html + jelentes_latest.html
  web/jelentes/jelentes_YYYY-MM-DD.pdf  + ../jelentes_latest.pdf (a webes link)

Futtatás:  python -m src.report_html            (HTML + PDF)
           python -m src.report_html --no-pdf   (csak HTML, Playwright nélkül)
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize

from src import config

# ---------------------------------------------------------------------------- #
# Adat + segédek (a matplotlib-generátorral közös nyelvezet)
# ---------------------------------------------------------------------------- #
JELENTES_DIR = config.WEB_DATA / "jelentes"
ASSETS_DIR = JELENTES_DIR / "assets"
FOCUS = config.REPORT_FOCUS_COUNTIES

RUST = "#b0533a"   # elmaradás (a design piros-árnyalata)
GREEN = "#3f7d5c"  # többlet

# a webes/PDF-es choropleth-tel azonos, CVD-biztos piros–kék paletta
ANOM_COLORS = ["#b03a2e", "#e67e22", "#f5e8c8", "#7fb3d5", "#2874a6"]
ANOM_CMAP = LinearSegmentedColormap.from_list("anom", ANOM_COLORS)
ANOM_NORM = Normalize(vmin=-20, vmax=20)
NO_DATA = "#d5d8dc"


def load_fc(crop: str) -> dict:
    return json.loads((config.WEB_DATA / f"forecast_{crop}.json").read_text(encoding="utf-8"))


def hu(v: float, d: int = 2) -> str:
    return f"{v:.{d}f}".replace(".", ",").replace("-", "−")


def signed(v: float, d: int = 2) -> str:
    if abs(v) < 0.5 * 10 ** (-d):
        return "0" + ("," + "0" * d if d else "")
    return ("+" if v > 0 else "−") + hu(abs(v), d)


def crop_key(fc: dict) -> str:
    for key, spec in config.CROPS.items():
        if spec["label"] == fc["crop"]:
            return key
    raise KeyError(fc["crop"])


def history_series(crop: str, max_days: int = 30) -> list[dict]:
    hdir = config.WEB_DATA / "history" / crop
    out = []
    for p in sorted(hdir.glob("????-??-??.json"))[-max_days:]:
        d = json.loads(p.read_text(encoding="utf-8"))
        nat = d.get("national") or {}
        if nat.get("predicted_yield_t_ha") is None:
            continue
        sc = (d.get("scenarios") or {}).get("national") or {}
        out.append({"date": p.stem, "pred": nat["predicted_yield_t_ha"],
                    "p10": sc.get("p10"), "p90": sc.get("p90")})
    return out


# ---------------------------------------------------------------------------- #
# Élő térkép-PNG-k (vármegyei anomália-choropleth, fókusz-vármegye vastag kerettel)
# ---------------------------------------------------------------------------- #
def save_crop_map(fc: dict, gdf, out_path: Path) -> None:
    anoms = {c["nuts_id"]: (c["anomaly_pct"] if c["predicted_yield_t_ha"] is not None
                            else None) for c in fc["counties"]}
    g = gdf.copy()
    g["anom"] = g["NUTS_ID"].map(anoms)
    fig, ax = plt.subplots(figsize=(4.2, 2.7))
    g[g["anom"].isna()].plot(ax=ax, color=NO_DATA, edgecolor="#8a8f94", linewidth=0.4)
    g[g["anom"].notna()].plot(ax=ax, column="anom", cmap=ANOM_CMAP, norm=ANOM_NORM,
                              edgecolor="#8a8f94", linewidth=0.4)
    focus_ids = {c["nuts_id"] for c in fc["counties"] if c["county_name"] in FOCUS}
    sel = g[g["NUTS_ID"].isin(focus_ids)]
    if len(sel):
        sel.plot(ax=ax, facecolor="none", edgecolor="#1d1f20", linewidth=1.6)
    ax.set_axis_off()
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(out_path, dpi=200, transparent=True, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


# ---------------------------------------------------------------------------- #
# SVG-diagramok (a design hand-coded SVG-inek adat-vezérelt párjai)
# ---------------------------------------------------------------------------- #
def scenario_bar_svg(p10: float, p50: float, p90: float, pred: float) -> str:
    span = max(p90 - p10, 1e-6)
    fx = 10 + (pred - p10) / span * 280
    fx = min(max(fx, 12), 288)
    return f"""<svg width="100%" viewBox="0 0 300 60" style="display:block;overflow:visible">
  <rect x="10" y="26" width="280" height="10" fill="var(--color-accent-200)"></rect>
  <text x="10" y="52" font-family="Barlow" font-size="11" fill="#8a8a8d">kedvezőtlen</text>
  <text x="10" y="20" font-family="Barlow" font-size="12" font-weight="700" fill="#1d1f20">{hu(p10)}</text>
  <text x="290" y="52" text-anchor="end" font-family="Barlow" font-size="11" fill="#8a8a8d">kedvező</text>
  <text x="290" y="20" text-anchor="end" font-family="Barlow" font-size="12" font-weight="700" fill="#1d1f20">{hu(p90)}</text>
  <line x1="{fx:.1f}" y1="20" x2="{fx:.1f}" y2="42" stroke="var(--color-accent-800)" stroke-width="2"></line>
  <path d="M{fx:.1f} 20 l-5 -7 l10 0 Z" fill="var(--color-accent-800)"></path>
  <text x="{fx:.1f}" y="10" text-anchor="middle" font-family="Barlow Condensed" font-size="13" font-weight="600" fill="var(--color-accent-800)">becslés {hu(pred)}</text>
</svg>"""


def anom_bar_svg(a: float) -> str:
    """±20% skálájú sáv a 0-vonással (a fókusz-tábla „eltérés" oszlopa)."""
    unit = 118 / 40  # 1% = 2.95 px
    mag = min(abs(a), 20) * unit
    if a < 0:
        x, w, col = 59 - mag, mag, RUST
    else:
        x, w, col = 59, mag, GREEN
    return (f'<svg width="118" height="12" viewBox="0 0 118 12" style="overflow:visible;display:block">'
            f'<line x1="59" y1="0" x2="59" y2="12" stroke="var(--color-neutral-400)" stroke-width="1"></line>'
            f'<rect x="{x:.1f}" y="3" width="{w:.1f}" height="6" fill="{col}" opacity="0.85"></rect></svg>')


def fan_chart_svg(hs: list[dict]) -> str:
    """A becslés szezonközi alakulása a várható sávval, adatból számolt koordinátákkal."""
    xs0, xs1, y_top, y_bot = 34, 580, 10, 104
    preds = [h["pred"] for h in hs]
    has_band = all(h["p10"] is not None and h["p90"] is not None for h in hs)
    lows = [h["p10"] for h in hs] if has_band else preds
    highs = [h["p90"] for h in hs] if has_band else preds
    vmin, vmax = min(lows), max(highs)
    pad = (vmax - vmin) * 0.15 or 0.3
    vmin, vmax = vmin - pad, vmax + pad
    n = len(hs)

    def X(i):
        return xs0 + (xs1 - xs0) * (i / (n - 1) if n > 1 else 0)

    def Y(v):
        return y_top + (vmax - v) / (vmax - vmin) * (y_bot - y_top)

    # két „szép" gridvonal a tartományon belül
    g_hi = round(vmax - pad, 1)
    g_lo = round(vmin + pad, 1)
    grid = "".join(
        f'<line x1="34" y1="{Y(gv):.1f}" x2="590" y2="{Y(gv):.1f}" stroke="var(--color-divider)" '
        f'stroke-width="1" stroke-dasharray="3 3"></line>'
        f'<text x="28" y="{Y(gv)+3.5:.1f}" text-anchor="end" font-family="Barlow" font-size="11" fill="#8a8a8d">{hu(gv,1)}</text>'
        for gv in (g_hi, g_lo))
    band = ""
    if has_band:
        top = " ".join(f"{X(i):.1f} {Y(highs[i]):.1f}" for i in range(n))
        bot = " ".join(f"{X(i):.1f} {Y(lows[i]):.1f}" for i in range(n - 1, -1, -1))
        band = (f'<path d="M{top.replace(" ", " L", n-1) if False else top}" '
                f'fill="none"></path>')
        pts = "M" + " L".join(f"{X(i):.1f} {Y(highs[i]):.1f}" for i in range(n)) + \
              " L" + " L".join(f"{X(i):.1f} {Y(lows[i]):.1f}" for i in range(n - 1, -1, -1)) + " Z"
        band = f'<path d="{pts}" fill="var(--color-accent-200)" opacity="0.75"></path>'
    line = " ".join(f"{X(i):.1f},{Y(preds[i]):.1f}" for i in range(n))
    dots = "".join(f'<circle cx="{X(i):.1f}" cy="{Y(preds[i]):.1f}" r="{4.2 if i==n-1 else 3.4}"></circle>'
                   for i in range(n))
    labels = "".join(
        f'<text x="{X(i):.1f}" y="120">{hs[i]["date"][5:]}</text>'
        for i in range(0, n, max(1, n // 6)))
    return f"""<svg width="100%" viewBox="0 0 620 132" style="display:block">
  {grid}
  {band}
  <polyline points="{line}" fill="none" stroke="var(--color-accent)" stroke-width="2.2"></polyline>
  <g fill="var(--color-accent-700)">{dots}</g>
  <text x="580" y="{Y(preds[-1])-11:.1f}" text-anchor="end" font-family="Barlow Condensed" font-size="16" font-weight="600" fill="var(--color-accent-800)">{hu(preds[-1])}</text>
  <g font-family="Barlow" font-size="11" fill="#8a8a8d" text-anchor="middle">{labels}</g>
</svg>"""


# ---------------------------------------------------------------------------- #
# CSS (a design-tokenek + a jelentéshez használt osztályok, önállóan)
# ---------------------------------------------------------------------------- #
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;700&family=Barlow+Condensed:wght@400;600&display=swap');
:root{
  --color-bg:#ffffff;--color-text:#1d1f20;--color-accent:#5980a6;
  --color-divider:color-mix(in srgb,#1d1f20 16%,transparent);
  --color-neutral-400:#b7b7ba;--color-neutral-500:#98989b;
  --color-accent-100:#eef6ff;--color-accent-200:#d6ebff;--color-accent-300:#b5d9fd;
  --color-accent-700:#416180;--color-accent-800:#2c455d;--color-accent-900:#1d2d3d;
  --font-heading:"Barlow Condensed",system-ui,sans-serif;
  --font-body:"Barlow",system-ui,sans-serif;
}
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0}
body{background:#fff;color:var(--color-text);font-family:var(--font-body);font-size:15px;line-height:1.55}
h1,h2,h3,h4{font-family:var(--font-heading);font-weight:600;line-height:1.12;letter-spacing:-0.015em;margin:0 0 6px}
p{margin:0 0 10px}
img{display:block;max-width:100%}
figure{margin:0}
/* A4 lap: a doc-page komponens helyett szabványos nyomtatási oldal */
@page{size:A4;margin:0}
.page{width:210mm;height:297mm;overflow:hidden;padding:18mm 17.8mm 14mm;position:relative;background:#fff}
.page + .page{page-break-before:always}
.page-footer{position:absolute;left:17.8mm;right:17.8mm;bottom:9mm;display:flex;
  justify-content:space-between;align-items:center;font-size:9.5px;letter-spacing:0.04em;
  color:color-mix(in srgb,var(--color-text) 45%,transparent);
  border-top:1px solid var(--color-divider);padding-top:5px}
.rep-kicker{font-family:var(--font-heading);font-weight:600;font-size:11px;letter-spacing:0.14em;
  text-transform:uppercase;color:var(--color-accent);margin:0 0 4px}
.rep-stat-row{display:flex;justify-content:space-between;gap:8px;font-size:12px;line-height:1.5}
.rep-stat-row > span:first-child{color:color-mix(in srgb,var(--color-text) 55%,transparent)}
.rep-stat-row > span:last-child{font-variant-numeric:tabular-nums;font-weight:500}
.blueprint{position:relative;border:1px solid var(--color-divider);border-radius:0}
/* A sarok-regisztrációs jelek a Claude Design szerkesztőjében csak igazítási/
   padding-segédek voltak — a kész jelentésen NEM látszanak. */
.blueprint > .corner{display:none}
.tag{display:inline-flex;align-items:center;font-size:11px;letter-spacing:0.02em;padding:3px 10px;border-radius:0}
.tag-accent{background:var(--color-accent-100);color:var(--color-accent-800)}
.tag-outline{border:1px solid var(--color-accent);color:var(--color-accent)}
.table{width:100%;border-collapse:collapse;font-size:13.5px}
.table th{text-align:left;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;
  color:color-mix(in srgb,var(--color-text) 60%,transparent);padding:6.8px;
  border-bottom:1px solid var(--color-divider)}
.table td{padding:6.8px;border-bottom:1px solid color-mix(in srgb,var(--color-text) 8%,transparent)}
"""


# ---------------------------------------------------------------------------- #
# HTML összeállítás
# ---------------------------------------------------------------------------- #
def crop_card(fc: dict) -> str:
    n = fc["national"]
    v = n.get("value")
    live = fc.get("scenarios") is not None
    tag = ('<span class="tag tag-accent">Még változhat</span>' if live
           else '<span class="tag tag-outline">Végleges közeli</span>')
    status = (f"{fc['scenarios']['remaining_days']} nap van hátra" if live
              else "a szezon lezárult")
    a = n["anomaly_pct"]
    a_col = RUST if a < -0.05 else GREEN if a > 0.05 else "var(--color-text)"
    lo, hi = n.get("pred_low_t_ha"), n.get("pred_high_t_ha")
    band_row = (f'<div class="rep-stat-row"><span>80%-os sáv</span><span>{hu(lo)}–{hu(hi)}</span></div>'
                if lo is not None else "")
    live_box = ""
    if live:
        sn = fc["scenarios"]["national"]
        live_box = (f'<div style="background:var(--color-accent-100);border:1px solid var(--color-accent-200);'
                    f'padding:5px 8px;font-size:11px;color:var(--color-accent-800)">'
                    f'Időjárástól még: <strong>{hu(sn["p10"])}–{hu(sn["p90"])} t/ha</strong></div>')
    val_block = ""
    if v:
        val_block = (
            f'<div style="border-top:1px solid var(--color-divider);padding-top:8px;margin-top:auto">'
            f'<div style="font-family:var(--font-heading);font-weight:600;font-size:19px;line-height:1">'
            f'{v["production_value_bn_huf"]:.0f} <span style="font-size:12px;font-weight:400;'
            f'color:color-mix(in srgb,var(--color-text) 55%,transparent)">mrd Ft</span> '
            f'<span style="font-size:13px;color:{RUST}">({signed(v["trend_gap_bn_huf"], 0)})</span></div>'
            f'<div style="font-size:10.5px;color:color-mix(in srgb,var(--color-text) 50%,transparent);'
            f'margin-top:1px">termelési érték · eltérés</div></div>')
    return f"""<div class="blueprint" style="break-inside:avoid;padding:15px 14px 13px;display:flex;flex-direction:column;gap:9px">
  <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
  <h3 style="margin:0;font-size:22px">{fc['crop'].capitalize()}</h3>
  <div>{tag}<div style="font-size:11px;color:color-mix(in srgb,var(--color-text) 50%,transparent);margin-top:5px">{status}</div></div>
  <div style="margin-top:2px"><span style="font-family:var(--font-heading);font-weight:600;font-size:38px;line-height:0.9">{hu(n['predicted_yield_t_ha'])}</span> <span style="font-size:15px;color:color-mix(in srgb,var(--color-text) 55%,transparent)">t/ha</span></div>
  <div style="display:flex;align-items:baseline;gap:8px"><span style="font-family:var(--font-heading);font-weight:600;font-size:24px;color:{a_col};line-height:1">{signed(a,1)}%</span><span style="font-size:11px;color:color-mix(in srgb,var(--color-text) 55%,transparent)">a szokásoshoz</span></div>
  {live_box}
  <div style="border-top:1px solid var(--color-divider);padding-top:8px;margin-top:2px">
    {band_row}
    <div class="rep-stat-row"><span>szokásos</span><span>{hu(n['trend_t_ha'])}</span></div>
    <div class="rep-stat-row"><span>tavaly</span><span>{hu(n['prev_year_yield_t_ha'])}</span></div>
  </div>
  {val_block}
</div>"""


def focus_bar_rows(fcs: dict) -> str:
    rows = []
    for county in FOCUS:
        rows.append(f'<tr><td colspan="5" style="font-family:var(--font-heading);font-weight:600;'
                    f'font-size:15px;background:color-mix(in srgb,var(--color-text) 4%,transparent);'
                    f'padding-top:7px;padding-bottom:7px">{county}</td></tr>')
        for crop, fc in fcs.items():
            rec = next((c for c in fc["counties"] if c["county_name"] == county), None)
            nat = fc["national"]["predicted_yield_t_ha"]
            if rec is None or rec["predicted_yield_t_ha"] is None:
                rows.append(f'<tr><td>{fc["crop"]}</td><td colspan="4" style="color:#8a8a8d">nincs becslés</td></tr>')
                continue
            a = rec["anomaly_pct"]
            dv = rec["predicted_yield_t_ha"] - nat
            dv_col = GREEN if dv > 0.005 else RUST if dv < -0.005 else "var(--color-neutral-500)"
            rows.append(
                f'<tr><td>{fc["crop"]}</td>'
                f'<td style="text-align:right;font-weight:600;font-variant-numeric:tabular-nums">{hu(rec["predicted_yield_t_ha"])} t/ha</td>'
                f'<td style="text-align:right;color:{RUST if a<0 else GREEN};font-variant-numeric:tabular-nums">{signed(a,1)}%</td>'
                f'<td style="text-align:right;color:{dv_col};font-variant-numeric:tabular-nums">{signed(dv)}</td>'
                f'<td>{anom_bar_svg(a)}</td></tr>')
    return "\n".join(rows)


def build_html(fcs: dict, today: str, stamp: str) -> str:
    vals = [fc["national"].get("value") for fc in fcs.values()]
    total_val = sum(v["production_value_bn_huf"] for v in vals if v)
    total_gap = sum(v["trend_gap_bn_huf"] for v in vals if v)
    y, m, d = today.split("-")
    cards = "\n".join(crop_card(fc) for fc in fcs.values())

    # oldal-láblécek
    def footer(page_no, total):
        return (f'<div class="page-footer"><span>Terméshozam-előrejelző · statisztikai modell</span>'
                f'<span>{stamp} · {page_no} / {total} · nem hivatalos, indikatív adat</span></div>')

    # 2. oldal — térképek
    map_figs = "".join(
        f'<figure class="blueprint" style="padding:12px;margin:0;break-inside:avoid">'
        f'<i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>'
        f'<div style="font-family:var(--font-heading);font-weight:600;font-size:15px;margin-bottom:8px">'
        f'{fc["crop"]} · országos: <span style="color:{RUST if fc["national"]["anomaly_pct"]<0 else GREEN}">'
        f'{signed(fc["national"]["anomaly_pct"],1)}%</span></div>'
        f'<img src="assets/map_{crop_key(fc)}.png" alt="{fc["crop"]} térkép" style="width:100%;max-height:120px;object-fit:contain"></figure>'
        for fc in fcs.values())

    focus_rows = focus_bar_rows(fcs)

    # 3. oldal — futó szezonú termény (kukorica)
    live_fc = next((fc for fc in fcs.values() if fc.get("scenarios")), None)
    page3 = ""
    if live_fc:
        n = live_fc["national"]
        v = n["value"]
        sc = live_fc["scenarios"]["national"]
        rem = live_fc["scenarios"]["remaining_days"]
        area, price = v["area_ha"], v["price_huf_per_t"]
        price_fmt = f"{price:,.0f}".replace(",", " ")

        def money(t):
            return f'{t*area*price/1e9:.0f}'

        def tonnes(t):
            return hu(t * area / 1e6, 2)
        risk = (sc["p90"] - sc["p10"]) * area * price / 1e9
        hs = history_series(crop_key(live_fc), 30)
        fan = fan_chart_svg(hs) if len(hs) >= 2 else ""
        # fókusz-vármegyék időjárás-tábla
        wrows = []
        sc_c = live_fc["scenarios"].get("counties") or {}
        for county in FOCUS:
            rec = next((c for c in live_fc["counties"] if c["county_name"] == county), None)
            if not rec or rec["predicted_yield_t_ha"] is None:
                continue
            scc = sc_c.get(rec["nuts_id"])
            wx = rec["weather_todate"]
            rng = f'{hu(scc["p10"])}–{hu(scc["p90"])}' if scc else "–"
            wrows.append(
                f'<tr><td style="font-weight:600">{county}</td>'
                f'<td style="text-align:right;font-weight:600;font-variant-numeric:tabular-nums">{hu(rec["predicted_yield_t_ha"])} t/ha</td>'
                f'<td style="text-align:right;font-variant-numeric:tabular-nums">{rng}</td>'
                f'<td style="text-align:right;font-variant-numeric:tabular-nums">{wx["heat_days"]} nap</td>'
                f'<td style="text-align:right;color:{RUST};font-variant-numeric:tabular-nums">{hu(wx["wb_total_mm"],0)} mm</td>'
                f'<td style="text-align:right;font-variant-numeric:tabular-nums">{hu(wx["prec_total_mm"],0)} mm</td></tr>')
        errs = [f["national"].get("model_error_pct") for f in fcs.values()
                if f["national"].get("model_error_pct")]
        err_range = f"±{hu(min(errs),0)}–{hu(max(errs),0)}%" if errs else "±7–19%"
        me = {crop_key(f): f["national"].get("model_error_pct") for f in fcs.values()}
        # Tömör, sorkizárt (10 px) módszertan a lap alján: definiálja a sávot, a
        # modellt és a validációt, a ± és a sáv viszonyát, a feltevéseket, és
        # kimondja, hogy a forint volumen-indikátor, nem bevétel. Kötőjel/
        # gondolatjel a prózában szándékosan nincs.
        methodology = (
            "<strong>Módszertan.</strong> Vármegyei panel lineáris regresszió a KSH "
            "2000 óta mért hozamaira és az ERA5 időjárásra: a fajta és technológiai "
            "fejlődést közös trend, a vármegyei adottságokat rögzített hatás kezeli, "
            "öntözést, talajtípust és fajtaszerkezetet nem. A 80%-os sáv a becslés "
            "predikciós intervalluma, a modell múltból jósló, tesztéven kívüli "
            "tévedéseinek eloszlásából (visszamérés 2011 és 2025 között); a tipikus "
            "tévedés ennek szokásos nagysága a trendszinthez mérve (búza "
            f"{hu(me.get('wheat',0),1)}%, kukorica {hu(me.get('corn',0),1)}%, árpa "
            f"{hu(me.get('barley',0),1)}%), a sáv ennél mintegy 1,3-szer szélesebb. "
            "A termelési érték a hozam, a terület és a 2024-es ár szorzata, volumen "
            "alapú indikátor, nem bevételi előrejelzés. Nem hivatalos adat. Részletes "
            "leírás és visszamérés: prettyasap.github.io/wheat-forecast/magyarazat."
            "html. Források: KSH, Open-Meteo (ERA5), Eurostat."
        )
        page3 = f"""<section class="page">
  <div style="display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--color-text);padding-bottom:8px;margin-bottom:16px">
    <div><p class="rep-kicker">Szezonközi kilátás — {live_fc['crop']}</p>
      <h2 style="margin:0;font-size:30px;line-height:1">Még {rem} nap van hátra</h2></div>
    <div style="font-size:11px;color:color-mix(in srgb,var(--color-text) 50%,transparent);text-align:right;white-space:nowrap">a becslés még változhat · 3 / 3</div>
  </div>
  <div style="display:grid;grid-template-columns:1.35fr 1fr;gap:22px;align-items:start">
    <div>
      <p style="font-size:15px;line-height:1.6;margin:0 0 10px"><strong>A {live_fc['crop']} idei termése {hu(n['predicted_yield_t_ha'])} t/ha körül várható</strong>, ami {hu(abs(n['anomaly_pct']),1)}%-kal marad el a sokéves szokásos szinttől. {v['price_year']}-es árakon számolva ez kb. <strong>{abs(v['trend_gap_bn_huf']):.0f} mrd Ft</strong> kiesést jelent. Tavalyhoz ({n['prev_year']}) képest ez <span style="color:{GREEN};font-weight:600">{signed(n['yoy_pct'],1)}%-os javulás</span>, a megszokott szinttől azonban elmarad.</p>
      <p style="font-size:13px;line-height:1.6;color:color-mix(in srgb,var(--color-text) 62%,transparent);margin:0">A szezonból még {rem} nap van hátra; a végeredmény az időjárástól függően <strong>{hu(sc['p10'])}–{hu(sc['p90'])} t/ha</strong> között alakulhat. A becslés tipikus tévedése a múltbeli visszamérések alapján ±{hu(n['model_error_pct'],1)}%.</p>
    </div>
    <div class="blueprint" style="padding:14px;margin:0;break-inside:avoid">
      <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
      <div style="font-family:var(--font-heading);font-weight:600;font-size:13px;margin-bottom:12px">Forgatókönyvek (t/ha)</div>
      {scenario_bar_svg(sc['p10'], sc['p50'], sc['p90'], n['predicted_yield_t_ha'])}
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:22px;align-items:start;margin-top:12px">
    <div style="break-inside:avoid">
      <p class="rep-kicker" style="margin-bottom:6px">Terményben és forintban</p>
      <table class="table">
        <thead><tr><th>Forgatókönyv</th><th style="text-align:right">t/ha</th><th style="text-align:right">M tonna</th><th style="text-align:right">mrd Ft*</th></tr></thead>
        <tbody>
          <tr><td>Kedvezőtlen</td><td style="text-align:right;font-variant-numeric:tabular-nums">{hu(sc['p10'])}</td><td style="text-align:right;font-variant-numeric:tabular-nums">{tonnes(sc['p10'])}</td><td style="text-align:right;font-variant-numeric:tabular-nums">{money(sc['p10'])}</td></tr>
          <tr style="background:var(--color-accent-100)"><td style="font-weight:700">Középső</td><td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums">{hu(sc['p50'])}</td><td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums">{tonnes(sc['p50'])}</td><td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums">{money(sc['p50'])}</td></tr>
          <tr><td>Kedvező</td><td style="text-align:right;font-variant-numeric:tabular-nums">{hu(sc['p90'])}</td><td style="text-align:right;font-variant-numeric:tabular-nums">{tonnes(sc['p90'])}</td><td style="text-align:right;font-variant-numeric:tabular-nums">{money(sc['p90'])}</td></tr>
        </tbody>
      </table>
      <p style="font-size:11px;color:color-mix(in srgb,var(--color-text) 48%,transparent);margin:7px 0 0">*{v['price_year']}-es termelői átlagáron ({price_fmt} Ft/t) és a legutóbbi lezárt évi vetésterülettel ({area/1e3:.0f} ezer ha) számolva — indikatív.</p>
    </div>
    <div style="align-self:center;border-left:3px solid var(--color-accent);padding:6px 0 6px 16px">
      <div style="font-family:var(--font-heading);font-weight:600;font-size:20px;line-height:1.15">A két szélső kimenet között kb. <span style="color:var(--color-accent-700)">{risk:.0f} mrd Ft</span> a különbség.</div>
    </div>
  </div>
  <figure class="blueprint" style="padding:12px 14px 8px;margin:14px 0 0;break-inside:avoid">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div style="font-family:var(--font-heading);font-weight:600;font-size:14px;margin-bottom:8px">A becslés alakulása a szezonban <span style="font-weight:400;color:color-mix(in srgb,var(--color-text) 55%,transparent);font-size:12px">(várható sávval, t/ha)</span></div>
    {fan}
  </figure>
  <div style="margin-top:14px;break-inside:avoid">
    <p class="rep-kicker" style="margin-bottom:6px">Fókusz-vármegyék — kilátás és időjárás</p>
    <table class="table">
      <thead><tr><th>Vármegye</th><th style="text-align:right">Becslés</th><th style="text-align:right">várható sáv</th><th style="text-align:right">Hőstressz</th><th style="text-align:right">Vízmérleg</th><th style="text-align:right">Csapadék</th></tr></thead>
      <tbody>{''.join(wrows)}</tbody>
    </table>
    <p style="font-size:11px;color:color-mix(in srgb,var(--color-text) 48%,transparent);margin:8px 0 0"><strong>Vízmérleg:</strong> a csapadék és a párolgás egyenlege a szezon eddigi részében — minél negatívabb, annál erősebb az aszálynyomás.</p>
  </div>
  <p style="font-size:10px;line-height:1.5;text-align:justify;color:color-mix(in srgb,var(--color-text) 52%,transparent);margin:10px 0 0;border-top:1px solid var(--color-divider);padding-top:8px">{methodology}</p>
  {footer(3, 3)}
</section>"""

    return f"""<!DOCTYPE html>
<html lang="hu"><head><meta charset="utf-8">
<title>Napi vezetői jelentés — {today}</title>
<style>{CSS}</style></head><body>
<section class="page">
  <div style="border-bottom:2px solid var(--color-text);padding-bottom:10px;margin-bottom:4px">
    <div style="display:flex;justify-content:space-between;align-items:baseline;gap:16px">
      <h1 style="font-size:40px;margin:0;line-height:0.98">Terméshozam-előrejelzés</h1>
      <div style="flex:none;font-family:var(--font-heading);font-weight:600;font-size:26px;line-height:1;white-space:nowrap">{y}. {m}. {d}.</div>
    </div>
  </div>
  <div style="break-inside:avoid;background:var(--color-accent-900);color:#eef4fb;padding:18px 20px;margin:16px 0 22px;display:flex;gap:22px;align-items:center">
    <div style="flex:1">
      <p style="font-family:var(--font-heading);font-weight:600;font-size:11px;letter-spacing:0.16em;text-transform:uppercase;color:var(--color-accent-300);margin:0 0 6px">Ma a lényeg</p>
      <p style="margin:0;font-family:var(--font-heading);font-weight:600;font-size:22px;line-height:1.15">A három termény együtt <span style="color:#fff">~{total_val:.0f} mrd Ft</span> termelési értéket ígér — <span style="color:#f0b7a5">{signed(total_gap,0)} mrd Ft</span> a szokásoshoz képest.</p>
      <p style="margin:8px 0 0;font-size:12px;color:var(--color-accent-300)">A legutolsó hivatalos (2024-es) termelői árakon számolva — indikatív becslés.</p>
    </div>
    <div style="flex:none;text-align:right;border-left:1px solid color-mix(in srgb,#fff 22%,transparent);padding-left:22px">
      <div style="font-family:var(--font-heading);font-weight:600;font-size:44px;line-height:0.9;color:#fff">{total_val:.0f}</div>
      <div style="font-size:11px;letter-spacing:0.08em;color:var(--color-accent-300);margin-top:2px">mrd Ft · termelési érték</div>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px">{cards}</div>
  <p style="font-size:11px;color:color-mix(in srgb,var(--color-text) 48%,transparent);margin:14px 0 0">A hozamok vármegyei statisztikai modellből (KSH 2000-től + ERA5 időjárás) származnak. A búza és őszi árpa szezonja gyakorlatilag lezárult; a kukorica becslése a hátralévő napokban még változhat.</p>
  {footer(1, 3)}
</section>
<section class="page">
  <div style="display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--color-text);padding-bottom:8px;margin-bottom:16px">
    <div><p class="rep-kicker">Területi kép</p><h2 style="margin:0;font-size:30px;line-height:1">Eltérés a szokásos hozamtól</h2></div>
    <div style="font-size:11px;color:color-mix(in srgb,var(--color-text) 50%,transparent);text-align:right">vármegyénként · vastag keret:<br>fókusz-vármegye · 2 / 3</div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px 18px;align-items:start">
    {map_figs}
    <div class="blueprint" style="align-self:start;padding:14px 16px;margin:0">
      <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
      <div style="font-family:var(--font-heading);font-weight:600;font-size:13px;margin-bottom:10px">Jelmagyarázat</div>
      <svg width="100%" viewBox="0 0 300 46" style="display:block"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#b03a2e"></stop><stop offset="0.25" stop-color="#e67e22"></stop><stop offset="0.5" stop-color="#f5e8c8"></stop><stop offset="0.75" stop-color="#7fb3d5"></stop><stop offset="1" stop-color="#2874a6"></stop>
      </linearGradient></defs><rect x="30" y="6" width="240" height="12" fill="url(#lg)"></rect>
      <g font-family="Barlow" font-size="12" fill="#6a6a6d" text-anchor="middle"><text x="30" y="38">−20%</text><text x="90" y="38">−10%</text><text x="150" y="38">0</text><text x="210" y="38">+10%</text><text x="270" y="38">+20%</text></g></svg>
      <div style="font-size:12px;line-height:1.6;color:color-mix(in srgb,var(--color-text) 62%,transparent);margin-top:12px"><span style="color:{RUST};font-weight:600">Piros</span>: elmaradás a szokásostól · <span style="color:var(--color-accent);font-weight:600">kék</span>: többlet · <span style="color:var(--color-neutral-500);font-weight:600">szürke</span>: nincs becslés (Budapest).</div>
    </div>
  </div>
  <div style="margin-top:14px;break-inside:avoid">
    <p class="rep-kicker" style="margin-bottom:8px">Fókusz-vármegyék — {' · '.join(FOCUS)}</p>
    <table class="table"><thead><tr><th style="width:24%">Termény</th><th style="width:16%;text-align:right">Becslés</th><th style="width:16%;text-align:right">vs. szokásos</th><th style="width:16%;text-align:right">vs. országos</th><th style="width:28%">Eltérés a szokásostól</th></tr></thead>
    <tbody>{focus_rows}</tbody></table>
    <p style="font-size:11px;color:color-mix(in srgb,var(--color-text) 48%,transparent);margin:8px 0 0">A bar a szokásos hozamtól való eltérést mutatja (skála ±20%); a függőleges vonás a 0%. „vs. országos" oszlop: t/ha eltérés az országos becsléshez képest.</p>
  </div>
  {footer(2, 3)}
</section>
{page3}
</body></html>"""


# ---------------------------------------------------------------------------- #
# PDF renderelés (headless Chromium / Playwright)
# ---------------------------------------------------------------------------- #
def render_pdf(html_path: Path, pdf_path: Path) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        page.pdf(path=str(pdf_path), prefer_css_page_size=True, print_background=True)
        browser.close()


def main(make_pdf: bool = True) -> None:
    today = date.today().isoformat()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    JELENTES_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    fcs = {crop: load_fc(crop) for crop in config.CROPS}
    gdf = gpd.read_file(config.WEB_DATA / "nuts3_hu.geojson")
    for crop, fc in fcs.items():
        save_crop_map(fc, gdf, ASSETS_DIR / f"map_{crop}.png")

    html = build_html(fcs, today, stamp)
    html_out = JELENTES_DIR / f"jelentes_{today}.html"
    html_out.write_text(html, encoding="utf-8")
    (JELENTES_DIR / "jelentes_latest.html").write_text(html, encoding="utf-8")
    print(f"[ok] {html_out.name} + jelentes_latest.html")

    if make_pdf:
        pdf_out = JELENTES_DIR / f"jelentes_{today}.pdf"
        render_pdf(html_out, pdf_out)
        (config.WEB_DATA / "jelentes_latest.pdf").write_bytes(pdf_out.read_bytes())
        print(f"[ok] {pdf_out.name} ({pdf_out.stat().st_size // 1024} KB) + jelentes_latest.pdf")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true", help="csak HTML, Playwright nélkül")
    a = ap.parse_args()
    main(make_pdf=not a.no_pdf)
