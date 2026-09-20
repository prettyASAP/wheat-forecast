"""Napi vezetői PDF-jelentés – HTML→PDF (Claude Design-alapú szedés).

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
import re
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
        f'<text x="{X(i):.1f}" y="120">{_HU_MONTHS[int(hs[i]["date"][5:7])]} {int(hs[i]["date"][8:10])}.</text>'
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
   padding-segédek voltak – a kész jelentésen NEM látszanak. */
.blueprint > .corner{display:none}
.tag{display:inline-flex;align-items:center;font-size:11px;letter-spacing:0.02em;padding:3px 10px;border-radius:0}
.tag-accent{background:var(--color-accent-100);color:var(--color-accent-800)}
.tag-outline{border:1px solid var(--color-accent);color:var(--color-accent)}
.table{width:100%;border-collapse:collapse;font-size:13.5px}
.table th{text-align:left;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;
  color:color-mix(in srgb,var(--color-text) 60%,transparent);padding:6.8px;
  border-bottom:1px solid var(--color-divider)}
.table td{padding:6.8px;border-bottom:1px solid color-mix(in srgb,var(--color-text) 8%,transparent)}
.price-table td{padding:3.2px 6.8px;white-space:nowrap}
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
    oe = n.get("official_estimate")
    official_row = (f'<div class="rep-stat-row" style="border-top:1px dotted var(--color-divider);'
                    f'margin-top:3px;padding-top:3px"><span>EU-becslés</span>'
                    f'<span style="font-weight:600">{hu(oe["yield_t_ha"])}</span></div>' if oe else "")
    band_row = (f'<div class="rep-stat-row"><span>80%-os sáv</span><span>{hu(lo)}–{hu(hi)}</span></div>'
                if lo is not None else "")
    live_box = ""
    if live:
        sn = fc["scenarios"]["national"]
        live_box = (f'<div style="background:var(--color-accent-100);border:1px solid var(--color-accent-200);'
                    f'padding:5px 8px;font-size:11px;color:var(--color-accent-800)">'
                    f'Az időjárástól függően: <strong>{hu(sn["p10"])}–{hu(sn["p90"])} t/ha</strong></div>')
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
  <h3 style="margin:0;font-size:22px">{fc['crop'].capitalize()} <span style="font-family:var(--font-body);font-weight:400;font-size:12px;color:color-mix(in srgb,var(--color-text) 50%,transparent)">{fc['crop_year']}</span></h3>
  <div>{tag}<div style="font-size:11px;color:color-mix(in srgb,var(--color-text) 50%,transparent);margin-top:5px">{status}</div></div>
  <div style="margin-top:2px"><span style="font-family:var(--font-heading);font-weight:600;font-size:38px;line-height:0.9">{hu(n['predicted_yield_t_ha'])}</span> <span style="font-size:15px;color:color-mix(in srgb,var(--color-text) 55%,transparent)">t/ha</span></div>
  <div style="display:flex;align-items:baseline;gap:8px"><span style="font-family:var(--font-heading);font-weight:600;font-size:24px;color:{a_col};line-height:1">{signed(a,1)}%</span><span style="font-size:11px;color:color-mix(in srgb,var(--color-text) 55%,transparent)">a szokásoshoz</span></div>
  {live_box}
  <div style="border-top:1px solid var(--color-divider);padding-top:8px;margin-top:2px">
    {band_row}
    <div class="rep-stat-row"><span>szokásos</span><span>{hu(n['trend_t_ha'])}</span></div>
    <div class="rep-stat-row"><span>{n['prev_year']}. évi tény</span><span>{hu(n['prev_year_yield_t_ha'])}</span></div>
    {official_row}
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


def price_phrase(v: dict, cap: bool = False) -> str:
    """A forintosítás árának mondatba illeszthető alakja (friss heti ár vagy
    tartalékként az éves átlagár) – a JSON mondja meg, melyikkel számoltunk."""
    s = v.get("price_phrase") or f"a {v['price_year']}. évi átlagáron"
    return s[0].upper() + s[1:] if cap else s


def drivers_strip(fcs: dict) -> str:
    """'Mi húzza a becslést?' – a három kártya alatt, oszlopra igazítva: a becslés
    időjárási részének pontos bontása (a meglévő lineáris modell tagjai
    csoportosítva), plusz szélsőség-jelzés, ha az idei szezon a modell
    tapasztalatán kívül esik. A tendencia MEGÉRTÉSÉT szolgálja, nem napi zaj."""
    cols = []
    any_data = False
    # KÖZÖS skála a három terményre: a sávhosszak oszlopok között is összemérhetők
    mx = max([1.0] + [abs(g["pct"]) for fc in fcs.values()
                      for g in (fc["national"].get("drivers") or {}).get("groups", [])])
    for fc in fcs.values():
        n = fc["national"]
        d = n.get("drivers")
        if not d or not d.get("groups"):
            cols.append("<div></div>")
            continue
        any_data = True
        rows = "".join(
            f'<div style="display:grid;grid-template-columns:1fr 64px 38px;gap:6px;'
            f'align-items:center;font-size:11px;margin-top:4px">'
            f'<span>{g.get("short") or g["label"]}</span>'
            f'<span style="height:6px;background:color-mix(in srgb,var(--color-text) 7%,transparent)">'
            f'<i style="display:block;height:100%;width:{abs(g["pct"])/mx*100:.0f}%;'
            f'background:{RUST if g["pct"] < 0 else GREEN}"></i></span>'
            f'<span style="text-align:right;font-weight:600;font-variant-numeric:tabular-nums">'
            f'{signed(g["pct"], 1)}</span></div>'
            for g in d["groups"])
        env = n.get("envelope") or []
        warn = ""
        if env:
            worst = env[-1] if len(env) == 1 else max(
                env, key=lambda e: abs(e["value"] - e["hist_extreme"]) / (abs(e["hist_extreme"]) or 1))
            warn = (f'<p style="font-size:10px;line-height:1.4;margin:6px 0 0;color:{RUST}">'
                    f'<strong>Szélsőség:</strong> {worst["label"]} kívül esik a 2000 óta mért tartományon '
                    f'(eddigi szélsőérték: {worst["hist_extreme_year"]}).</p>')
        cols.append(f'<div style="padding:0 15px">{rows}{warn}</div>')
    if not any_data:
        return ""
    return (
        '<div style="margin-top:14px;break-inside:avoid">'
        '<p style="font-family:var(--font-heading);font-weight:600;font-size:11px;'
        'letter-spacing:0.14em;text-transform:uppercase;color:var(--color-accent);'
        'margin:0 0 2px">Mi húzza a becslést? <span style="font-weight:400;'
        'letter-spacing:0;text-transform:none;font-size:10px;color:color-mix(in srgb,'
        'var(--color-text) 48%,transparent)">az időjárás hatása százalékpontban, a modell '
        'átlagos időjárás mellett várt szintjéhez mérve</span></p>'
        '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px">'
        + "".join(cols) + '</div></div>')


def lead_sentence(fc: dict) -> str:
    """A 3. oldal vezetőmondata ELŐJELFÜGGŐEN (elmaradás / többlet / szokásos szint),
    a webes renderHeadline logikájával azonosan – hogy a futó termény váltásakor
    (pl. októbertől a búza) se állítson hamisat."""
    n, v = fc["national"], fc["national"]["value"]
    a, yoy = n["anomaly_pct"], n["yoy_pct"]
    art = "Az" if fc["crop"][0].lower() in "aáeéiíoóöőuúüű" else "A"
    est = f"<strong>{art} {fc['crop']} termése {hu(n['predicted_yield_t_ha'])} t/ha körül várható</strong>"
    if a <= -3:
        main = (f"{est}, ami {hu(abs(a), 1)}%-kal marad el a sokéves szokásos szinttől. "
                f"{price_phrase(v, cap=True)} számolva ez kb. <strong>"
                f"{abs(v['trend_gap_bn_huf']):.0f} mrd Ft</strong> kiesést jelent.")
    elif a >= 3:
        main = (f"{est}, {hu(a, 1)}%-kal a sokéves szokásos szint felett. "
                f"{price_phrase(v, cap=True)} számolva ez kb. <strong>"
                f"{v['trend_gap_bn_huf']:.0f} mrd Ft</strong> többletet jelent.")
    else:
        main = (f"{est}, a sokéves szokásos szint közelében ({signed(a, 1)}%); érdemi "
                f"kiesés vagy többlet egyelőre nem látszik.")
    clause = ""
    if a < 0 and yoy >= 3:
        clause = (f" A {n['prev_year']}. évi terméshez képest ez <span style=\"color:{GREEN};"
                  f"font-weight:600\">{signed(yoy, 1)}%-os javulás</span>, a megszokott "
                  f"szinttől azonban elmarad.")
    elif a > 0 and yoy <= -3:
        clause = (f" A {n['prev_year']}. évi terméshez képest ez <span style=\"color:{RUST};"
                  f"font-weight:600\">{signed(yoy, 1)}%-os visszaesés</span>, a termés azonban "
                  f"így is a megszokott szint felett alakul.")
    return f'<p style="font-size:15px;line-height:1.6;margin:0 0 10px">{main}{clause}</p>'


def trend_strip(trend_fcs: list) -> str:
    """Kompakt, ALÁRENDELT csík a trend-alapú terményekhez (napraforgó, repce).
    Tudatosan a konfidencia-hierarchia legalján, kis súllyal: 15 px-es értékek a
    fő kártyák 38 px-éhez képest, halvány szín, a lap alján – a gyengébb
    bizonyosságnak arányos vizuális dominancia, a 3-kártyás design felborítása
    nélkül. Nincs időjárás-anomália-állítás, csak becslés + tipikus tévedés."""
    if not trend_fcs:
        return ""
    items = []
    for fc in trend_fcs:
        n = fc["national"]
        v = n.get("value")
        val = (f'<span style="color:color-mix(in srgb,var(--color-text) 52%,transparent);'
               f'margin-left:auto;font-variant-numeric:tabular-nums">'
               f'~{v["production_value_bn_huf"]:.0f} mrd Ft</span>' if v else "")
        oe = n.get("official_estimate")
        eu_line = (f'<div style="font-size:10.5px;color:color-mix(in srgb,var(--color-text) 50%,'
                   f'transparent);margin-top:1px">EU-becslés: {hu(oe["yield_t_ha"])} t/ha</div>'
                   if oe else "")
        items.append(
            f'<div><div style="display:flex;align-items:baseline;gap:8px;font-size:12px;'
            f'white-space:nowrap">'
            f'<span style="font-family:var(--font-heading);font-weight:600;font-size:15px">'
            f'{fc["crop"].capitalize()}</span>'
            f'<span style="color:color-mix(in srgb,var(--color-text) 50%,transparent)">{fc["crop_year"]}</span>'
            f'<span style="font-family:var(--font-heading);font-weight:600;font-size:15px;'
            f'font-variant-numeric:tabular-nums">{hu(n["predicted_yield_t_ha"])} t/ha</span>'
            f'<span style="color:color-mix(in srgb,var(--color-text) 50%,transparent)">'
            f'±{hu(n["model_error_pct"], 1)}%</span>{val}</div>{eu_line}</div>')
    return (
        '<div style="margin-top:14px;border-top:1px solid var(--color-divider);'
        'padding-top:10px;break-inside:avoid">'
        '<p style="font-family:var(--font-heading);font-weight:600;font-size:11px;'
        'letter-spacing:0.14em;text-transform:uppercase;color:color-mix(in srgb,'
        'var(--color-text) 45%,transparent);margin:0 0 7px">Trendalapú termények '
        '<span style="font-weight:400;letter-spacing:0;text-transform:none;font-size:10px'
        ';color:color-mix(in srgb,var(--color-text) 42%,transparent)">– a sokéves trend, '
        'az idei időjárás figyelembevétele nélkül</span></p>'
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 18px">'
        + "".join(items) + '</div></div>')


_HU_MONTHS = ["", "jan.", "febr.", "márc.", "ápr.", "máj.", "jún.", "júl.",
              "aug.", "szept.", "okt.", "nov.", "dec."]


def _period_hu(period: str) -> str:
    """'2026-07-13 – 2026-07-19' -> 'júl. 13–19.'; hónap-átnyúlásnál
    'jún. 29. – júl. 5.'; a havi '2026. 05. hó' -> '2026. máj.'"""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2}) – (\d{4})-(\d{2})-(\d{2})", period)
    if m:
        y1, mo1, d1, y2, mo2, d2 = m.groups()
        if mo1 == mo2:
            return f"{_HU_MONTHS[int(mo1)]} {int(d1)}–{int(d2)}."
        return f"{_HU_MONTHS[int(mo1)]} {int(d1)}. – {_HU_MONTHS[int(mo2)]} {int(d2)}."
    m = re.match(r"(\d{4})\. (\d{2})\. hó", period)
    if m:
        return f"{m.group(1)}. {_HU_MONTHS[int(m.group(2))]}"
    return period


def _range52_svg(it: dict) -> str:
    """Az ár helye az utolsó 52 hét min–max sávjában (bal = mélypont, jobb = csúcs)."""
    if it.get("pos52") is None:
        return ""
    x = 3 + it["pos52"] * 70
    return (f'<svg width="76" height="10" viewBox="0 0 76 10" style="display:block">'
            f'<line x1="3" y1="5" x2="73" y2="5" stroke="var(--color-accent-200)" stroke-width="3"></line>'
            f'<circle cx="{x:.1f}" cy="5" r="3.2" fill="var(--color-accent-800)"></circle></svg>')


def market_price_page(market: dict, page_no: int, total: int, footer) -> str:
    """4. oldal: hivatalos piaci árjegyzések (EU agrifood API ← AKI PÁIR).
    Csak validált, friss tételek; a referencia-időszak tételenként jelölve.
    Napi hivatalos ár nem létezik – ezt a lap őszintén kimondja. Az árkontextus
    (forint, éves változás, 52 hetes sáv) heti trend-mutató, nem napi zaj."""
    groups: dict[str, list] = {}
    for it in market["items"]:
        groups.setdefault(it["group"], []).append(it)
    muted = "color:color-mix(in srgb,var(--color-text) 55%,transparent)"
    # a leggyakoribb jegyzett hét a fejlécbe kerül; soronként csak az ELTÉRŐ időszak
    periods = [it["period"] for it in market["items"] if it["freq"] == "heti"]
    common = max(set(periods), key=periods.count) if periods else None

    def scope_cell(it: dict) -> str:
        sc = (it["scope"].replace(" (hazai jegyzés nincs)", "").replace(" (hazai bontás nincs)", "")
              .replace(" (", ", ").replace(")", ""))
        return sc if it["period"] == common else f'{_period_hu(it["period"])} · {sc}'

    rows = []
    for gname in ("Gabona és takarmány", "Olajos termékek", "Sertés", "Baromfi",
                  "Feldolgozóipari termékek"):
        if gname not in groups:
            continue
        rows.append(f'<tr><td colspan="6" style="font-family:var(--font-heading);'
                    f'font-weight:600;font-size:15px;background:color-mix(in srgb,'
                    f'var(--color-text) 4%,transparent);padding-top:4px;'
                    f'padding-bottom:4px">{gname}</td></tr>')
        for it in groups[gname]:
            price = f"{it['price']:,.2f}".replace(",", " ").replace(".", ",")
            if it.get("huf") is not None:
                huf = (f"{it['huf']:,.0f}".replace(",", " ") if it["huf_unit"] == "Ft/t"
                       else hu(it["huf"], 0)) + f' <span style="{muted}">{it["huf_unit"]}</span>'
            else:
                huf = ""
            yoy = (signed(it["yoy_pct"], 1) + "%") if it.get("yoy_pct") is not None else ""
            rows.append(
                f'<tr><td>{it["label"]}</td>'
                f'<td style="text-align:right;font-variant-numeric:tabular-nums"><strong>{price}</strong> '
                f'<span style="{muted}">{it["unit"]}</span></td>'
                f'<td style="text-align:right;font-variant-numeric:tabular-nums">{huf}</td>'
                f'<td style="text-align:right;font-variant-numeric:tabular-nums">{yoy}</td>'
                f'<td>{_range52_svg(it)}</td>'
                f'<td style="{muted};font-size:11px">{scope_cell(it)}</td></tr>')
    fx = (market.get("valuation") or {}).get("fx")
    fx_note = (f" Forintérték: {hu(fx['rate'], 2)} Ft/EUR ({fx['source']}, "
               f"{fx['date'].replace('-', '. ')}.)." if fx else "")
    return f"""<section class="page">
  <div style="display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--color-text);padding-bottom:8px;margin-bottom:12px">
    <div><p class="rep-kicker">Piaci árjegyzések</p>
      <h2 style="margin:0;font-size:30px;line-height:1">Hivatalos heti jegyzések</h2></div>
    <div style="font-size:11px;color:color-mix(in srgb,var(--color-text) 50%,transparent);text-align:right;white-space:nowrap">jegyzett hét: {_period_hu(common) if common else "n. a."} · {page_no} / {total}</div>
  </div>
  <table class="table price-table" style="font-size:12px">
    <thead><tr><th style="width:25%">Termék</th><th style="width:19%;text-align:right">Jegyzés</th><th style="width:13%;text-align:right">Forintban</th><th style="width:9%;text-align:right;white-space:nowrap">Egy év</th><th style="width:12%;white-space:nowrap">52 hetes sáv</th><th style="width:22%">Piac</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p style="font-size:10px;line-height:1.5;text-align:justify;color:color-mix(in srgb,var(--color-text) 52%,transparent);margin:10px 0 0;border-top:1px solid var(--color-divider);padding-top:7px"><strong>A jegyzésekről.</strong> Hazai heti termelői és feldolgozói árak; forrás: az Európai Bizottság (DG AGRI) adatszolgáltatása, a magyar adat az AKI PÁIR jelentése. Hivatalos napi árjegyzés nem létezik, minden sor a legutolsó kiadott hétről való.{fx_note} Az 52 hetes sáv pontja az ár helye az elmúlt év mélypontja (bal) és csúcsa (jobb) között. Részletek: <a href="https://prettyasap.github.io/wheat-forecast/magyarazat.html" style="color:var(--color-accent);text-decoration:underline;text-underline-offset:2px">prettyasap.github.io/wheat-forecast/magyarazat.html</a>.</p>
  {footer(page_no, total)}
</section>"""


def build_html(fcs: dict, today: str, stamp: str, trend_fcs: list | None = None,
               market: dict | None = None) -> str:
    vals = [fc["national"].get("value") for fc in fcs.values()]
    total_val = sum(v["production_value_bn_huf"] for v in vals if v)
    total_gap = sum(v["trend_gap_bn_huf"] for v in vals if v)
    # a fejléc-sáv árfelirata: friss heti ár vagy (tartalékként) éves átlagár
    # ősszel a búza/árpa már az új termésévben jár, a kukorica még a régiben:
    # az összeg ilyenkor eltérő termésévekből áll — ezt kimondjuk
    years_note = (" Az összeg eltérő termésévek becsléseiből áll (az évszám a nevek mellett)."
                  if len({f["crop_year"] for f in fcs.values()}) > 1 else "")
    has_official = any(f["national"].get("official_estimate")
                       for f in list(fcs.values()) + list(trend_fcs or []))
    official_note = (" EU-becslés: az Európai Bizottság agrárpiaci adatportáljának havonta "
                     "frissülő termésadata (t/ha); a modellünktől független szám, ezért "
                     "eltérhet tőle." if has_official else "")

    def _names(fs):
        ns = [f["crop"] for f in fs]
        return ns[0] if len(ns) == 1 else ", ".join(ns[:-1]) + " és " + ns[-1]
    closed = [f for f in fcs.values() if not f.get("scenarios")]
    running = [f for f in fcs.values() if f.get("scenarios")]
    if closed and running:
        season_note = (f"Lezárult szezon: {_names(closed)}. Még változhat: {_names(running)}.")
    elif running:
        season_note = "Mindhárom termény szezonja tart, a becslések még változhatnak."
    else:
        season_note = "Mindhárom termény szezonja lezárult."
    banner_price = next((price_phrase(v, cap=True) for v in vals if v),
                        "A legutolsó hivatalos termelői áron")
    y, m, d = today.split("-")
    cards = "\n".join(crop_card(fc) for fc in fcs.values())

    # oldal-láblécek
    def footer(page_no, total):
        return (f'<div class="page-footer"><span>Terméshozam-előrejelzés · statisztikai modell</span>'
                f'<span>{stamp} · {page_no} / {total} · nem hivatalos, tájékoztató adat</span></div>')

    # 2. oldal – térképek
    map_figs = "".join(
        f'<figure class="blueprint" style="padding:12px;margin:0;break-inside:avoid">'
        f'<i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>'
        f'<div style="font-family:var(--font-heading);font-weight:600;font-size:15px;margin-bottom:8px">'
        f'{fc["crop"]} · országos: <span style="color:{RUST if fc["national"]["anomaly_pct"]<0 else GREEN}">'
        f'{signed(fc["national"]["anomaly_pct"],1)}%</span></div>'
        f'<img src="assets/map_{crop_key(fc)}.png" alt="{fc["crop"]} térkép" style="width:100%;max-height:120px;object-fit:contain"></figure>'
        for fc in fcs.values())

    focus_rows = focus_bar_rows(fcs)

    # 3. oldal – futó szezonú termény (kukorica); 4. oldal – piaci árjegyzések
    live_fc = next((fc for fc in fcs.values() if fc.get("scenarios")), None)
    has_market = bool(market and market.get("items"))
    total = 2 + (1 if live_fc else 0) + (1 if has_market else 0)
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
        # a táblában kijelzett, kerekített értékek különbsége (hogy a két szám egyezzen)
        risk = round(sc["p90"] * area * price / 1e9) - round(sc["p10"] * area * price / 1e9)
        an = live_fc["scenarios"].get("analogs")
        analog_line = (
            f'<p style="font-size:10.5px;line-height:1.45;margin:8px 0 0;color:color-mix(in srgb,'
            f'var(--color-text) 55%,transparent)">Ha a hátralévő {rem} nap időjárása olyan lesz, mint '
            f'<strong>{an["worst"][0]["year"]}</strong> azonos időszakában: '
            f'{hu(an["worst"][0]["t_ha"])} t/ha; mint <strong>{an["best"][0]["year"]}</strong> '
            f'azonos időszakában: {hu(an["best"][0]["t_ha"])} t/ha.</p>'
            if an else "")
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
            # a 80%-os sáv (modellhiba + hátralévő időjárás) – UGYANAZ a fogalom,
            # mint az 1. oldalon; a csak-időjárás sáv itt hamis pontosságot sugallna
            rng = f'{hu(rec["low"])}–{hu(rec["high"])}'
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
            "<strong>Módszertan.</strong> Vármegyei statisztikai modell a KSH 2000 óta "
            "mért hozamaiból és az ERA5 időjárásból. A 80%-os sáv és a tipikus tévedés "
            f"(búza {hu(me.get('wheat',0),1)}%, kukorica {hu(me.get('corn',0),1)}%, árpa "
            f"{hu(me.get('barley',0),1)}%) a modell 2011 és 2025 közötti visszaméréséből "
            "származik. A termelési érték tájékoztató mutató, nem bevételi előrejelzés. "
            "Nem hivatalos adat. Részletek: <a href=\"https://prettyasap.github.io/"
            "wheat-forecast/magyarazat.html\" style=\"color:var(--color-accent);"
            "text-decoration:underline;text-underline-offset:2px\">prettyasap."
            "github.io/wheat-forecast/magyarazat.html</a>. Források: KSH, Open-Meteo "
            "(ERA5), Európai Bizottság (DG AGRI), MNB."
        )
        page3 = f"""<section class="page">
  <div style="display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--color-text);padding-bottom:8px;margin-bottom:16px">
    <div><p class="rep-kicker">Szezonközi kilátás – {live_fc['crop']}</p>
      <h2 style="margin:0;font-size:30px;line-height:1">Még {rem} nap van hátra</h2></div>
    <div style="font-size:11px;color:color-mix(in srgb,var(--color-text) 50%,transparent);text-align:right;white-space:nowrap">a becslés még változhat · 3 / {total}</div>
  </div>
  <div style="display:grid;grid-template-columns:1.35fr 1fr;gap:22px;align-items:start">
    <div>
      {lead_sentence(live_fc)}
      <p style="font-size:13px;line-height:1.6;color:color-mix(in srgb,var(--color-text) 62%,transparent);margin:0">A szezonból még {rem} nap van hátra; a végeredmény az időjárástól függően <strong>{hu(sc['p10'])}–{hu(sc['p90'])} t/ha</strong> között alakulhat.</p>
    </div>
    <div class="blueprint" style="padding:14px;margin:0;break-inside:avoid">
      <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
      <div style="font-family:var(--font-heading);font-weight:600;font-size:13px;margin-bottom:12px">Forgatókönyvek (t/ha)</div>
      {scenario_bar_svg(sc['p10'], sc['p50'], sc['p90'], n['predicted_yield_t_ha'])}
      {analog_line}
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:22px;align-items:start;margin-top:12px">
    <div style="break-inside:avoid">
      <p class="rep-kicker" style="margin-bottom:6px">Terményben és forintban</p>
      <table class="table">
        <thead><tr><th>Forgatókönyv</th><th style="text-align:right">t/ha</th><th style="text-align:right">millió t</th><th style="text-align:right">mrd Ft*</th></tr></thead>
        <tbody>
          <tr><td>Kedvezőtlen</td><td style="text-align:right;font-variant-numeric:tabular-nums">{hu(sc['p10'])}</td><td style="text-align:right;font-variant-numeric:tabular-nums">{tonnes(sc['p10'])}</td><td style="text-align:right;font-variant-numeric:tabular-nums">{money(sc['p10'])}</td></tr>
          <tr style="background:var(--color-accent-100)"><td style="font-weight:700">Becslés</td><td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums">{hu(n['predicted_yield_t_ha'])}</td><td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums">{tonnes(n['predicted_yield_t_ha'])}</td><td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums">{money(n['predicted_yield_t_ha'])}</td></tr>
          <tr><td>Kedvező</td><td style="text-align:right;font-variant-numeric:tabular-nums">{hu(sc['p90'])}</td><td style="text-align:right;font-variant-numeric:tabular-nums">{tonnes(sc['p90'])}</td><td style="text-align:right;font-variant-numeric:tabular-nums">{money(sc['p90'])}</td></tr>
        </tbody>
      </table>
      <p style="font-size:11px;color:color-mix(in srgb,var(--color-text) 48%,transparent);margin:7px 0 0">*{price_phrase(v, cap=True)} ({price_fmt} Ft/t) és a legutóbbi lezárt évi betakarított területtel ({area/1e3:.0f} ezer ha) számolva; tájékoztató adat.</p>
    </div>
    <div style="align-self:center;border-left:3px solid var(--color-accent);padding:6px 0 6px 16px">
      <div style="font-family:var(--font-heading);font-weight:600;font-size:20px;line-height:1.15">A két szélső kimenet között kb. <span style="color:var(--color-accent-700)">{risk:.0f} mrd Ft</span> a különbség.</div>
    </div>
  </div>
  <figure class="blueprint" style="padding:12px 14px 8px;margin:14px 0 0;break-inside:avoid">
    <i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i>
    <div style="font-family:var(--font-heading);font-weight:600;font-size:14px;margin-bottom:8px">A becslés alakulása a szezonban <span style="font-weight:400;color:color-mix(in srgb,var(--color-text) 55%,transparent);font-size:12px">(az időjárástól függő sávval, t/ha)</span></div>
    {fan}
  </figure>
  <div style="margin-top:14px;break-inside:avoid">
    <p class="rep-kicker" style="margin-bottom:6px">Fókusz-vármegyék – kilátás és időjárás</p>
    <table class="table">
      <thead><tr><th>Vármegye</th><th style="text-align:right">Becslés</th><th style="text-align:right">80%-os sáv</th><th style="text-align:right">Hőstressz</th><th style="text-align:right">Vízmérleg</th><th style="text-align:right">Csapadék</th></tr></thead>
      <tbody>{''.join(wrows)}</tbody>
    </table>
    <p style="font-size:11px;color:color-mix(in srgb,var(--color-text) 48%,transparent);margin:8px 0 0"><strong>Vízmérleg:</strong> a csapadék és a párolgás egyenlege a szezon eddigi részében; minél negatívabb, annál súlyosabb az aszály.</p>
  </div>
  <p style="font-size:10px;line-height:1.5;text-align:justify;color:color-mix(in srgb,var(--color-text) 52%,transparent);margin:10px 0 0;border-top:1px solid var(--color-divider);padding-top:8px">{methodology}</p>
  {footer(3, total)}
</section>"""

    page4 = market_price_page(market, total, total, footer) if has_market else ""

    return f"""<!DOCTYPE html>
<html lang="hu"><head><meta charset="utf-8">
<title>Terméshozam-előrejelzés – {today}</title>
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
      <p style="margin:0;font-family:var(--font-heading);font-weight:600;font-size:22px;line-height:1.15">A három termény együtt <span style="color:#fff">~{total_val:.0f} mrd Ft</span> termelési értéket ígér, ez <span style="color:#f0b7a5">{abs(total_gap):.0f} mrd Ft-tal {"kevesebb" if total_gap < 0 else "több"}</span> a szokásosnál.</p>
      <p style="margin:8px 0 0;font-size:12px;color:var(--color-accent-300)">{banner_price} és a legutóbbi lezárt évi területtel számolva; tájékoztató becslés.{years_note}</p>
    </div>
    <div style="flex:none;text-align:right;border-left:1px solid color-mix(in srgb,#fff 22%,transparent);padding-left:22px">
      <div style="font-family:var(--font-heading);font-weight:600;font-size:44px;line-height:0.9;color:#fff">{total_val:.0f}</div>
      <div style="font-size:11px;letter-spacing:0.08em;color:var(--color-accent-300);margin-top:2px">mrd Ft · termelési érték</div>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px">{cards}</div>
  <p style="font-size:11px;color:color-mix(in srgb,var(--color-text) 48%,transparent);margin:14px 0 0">A hozamok vármegyei statisztikai modellből (KSH 2000-től + ERA5 időjárás) származnak. {season_note}{official_note}</p>
  {drivers_strip(fcs)}
  {trend_strip(trend_fcs or [])}
  {footer(1, total)}
</section>
<section class="page">
  <div style="display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--color-text);padding-bottom:8px;margin-bottom:16px">
    <div><p class="rep-kicker">Területi kép</p><h2 style="margin:0;font-size:30px;line-height:1">Eltérés a szokásos hozamtól</h2></div>
    <div style="font-size:11px;color:color-mix(in srgb,var(--color-text) 50%,transparent);text-align:right">vármegyénként · vastag keret:<br>fókusz-vármegye · 2 / {total}</div>
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
    <p class="rep-kicker" style="margin-bottom:8px">Fókusz-vármegyék – {' · '.join(FOCUS)}</p>
    <table class="table"><thead><tr><th style="width:24%">Termény</th><th style="width:16%;text-align:right">Becslés</th><th style="width:16%;text-align:right">Eltérés (%)</th><th style="width:16%;text-align:right">Országostól (t/ha)</th><th style="width:28%">Eltérés a szokásostól</th></tr></thead>
    <tbody>{focus_rows}</tbody></table>
    <p style="font-size:11px;color:color-mix(in srgb,var(--color-text) 48%,transparent);margin:8px 0 0">A sáv a szokásostól való eltérést mutatja ±20%-os skálán, a függőleges vonás a 0%. „Országostól”: eltérés az országos becsléstől.</p>
  </div>
  {footer(2, total)}
</section>
{page3}
{page4}
</body></html>"""


# ---------------------------------------------------------------------------- #
# PDF renderelés (headless Chromium / Playwright)
# ---------------------------------------------------------------------------- #
def _neutral_metadata(pdf_path: Path) -> None:
    """A dokumentum-tulajdonságok semlegesítése: a renderelő motor neve és a
    másodpercre pontos létrehozási idő helyett cím + a nap (éjfél) szerepel."""
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        print("  [info] pypdf nincs telepítve: a PDF-tulajdonságok változatlanok")
        return
    today = date.today()
    w = PdfWriter(clone_from=PdfReader(str(pdf_path)))
    stamp = today.strftime("D:%Y%m%d000000")
    w.add_metadata({"/Title": f"Terméshozam-előrejelzés {today.strftime('%Y. %m. %d.')}",
                    "/Creator": "", "/Producer": "", "/Author": "",
                    "/CreationDate": stamp, "/ModDate": stamp})
    with open(pdf_path, "wb") as fh:
        w.write(fh)


def render_pdf(html_path: Path, pdf_path: Path) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        # Túlcsordulás-őr: az oldalak FIX magasságúak (overflow:hidden), ezért a
        # láblécre ráfutó tartalom a PDF-ben csendben takarásba kerülne. Minden
        # generáláskor lemérjük a tartalom alja és a lábléc teteje közti hézagot.
        gaps = page.evaluate("""() => [...document.querySelectorAll('.page')].map(pg => {
            const f = pg.querySelector('.page-footer');
            const kids = [...pg.children].filter(e => e !== f);
            const bottom = Math.max(...kids.map(e => e.getBoundingClientRect().bottom));
            return Math.round(f.getBoundingClientRect().top - bottom);
        })""")
        for i, g in enumerate(gaps, 1):
            if g < 0:
                # a GitHub Actions felületén figyelmeztetésként jelenik meg
                print(f"::warning::PDF {i}. oldal: a tartalom {-g} px-lel a láblécre fut")
        print(f"  oldal-hézagok a lábléc fölött (px): {gaps}")
        page.pdf(path=str(pdf_path), prefer_css_page_size=True, print_background=True)
        browser.close()
    _neutral_metadata(pdf_path)


def main(make_pdf: bool = True, out_path: str | Path | None = None) -> Path | None:
    """A napi jelentés legenerálása. Visszaadja a kész PDF útvonalát (vagy None,
    ha --no-pdf). Külső PDF-pipeline-hoz: add meg az `out_path`-t (tetszőleges
    cél .pdf), és a kész PDF oda is odamásolódik – lásd INTEGRACIO.md."""
    today = date.today().isoformat()
    stamp = datetime.now().strftime("%Y. %m. %d.")  # csak a nap, óra-perc nélkül
    JELENTES_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # A napi PDF az időjárás-informált fő terményeket szedi (a trend-alapú
    # napraforgó/repce a webes előrejelzésben szerepel) – lásd config.REPORT_CROPS.
    fcs = {crop: load_fc(crop) for crop in config.REPORT_CROPS}
    gdf = gpd.read_file(config.WEB_DATA / "nuts3_hu.geojson")
    for crop, fc in fcs.items():
        save_crop_map(fc, gdf, ASSETS_DIR / f"map_{crop}.png")

    # trend-alapú termények (napraforgó, repce) – a page 1 alján, alárendelt
    # csíkban (a konfidencia-hierarchia legalja). Csak akkor, ha van forecastjuk.
    trend_fcs = []
    for crop, spec in config.CROPS.items():
        if spec.get("method") == "trend" and \
                (config.WEB_DATA / f"forecast_{crop}.json").exists():
            trend_fcs.append(load_fc(crop))

    # piaci árjegyzések (4. oldal) – csak ha a fájl létezik és 7 napnál frissebb
    # (elavult árakat nem közlünk; a blokk ilyenkor egyszerűen kimarad)
    market = None
    mp = config.WEB_DATA / "market_prices.json"
    if mp.exists():
        m = json.loads(mp.read_text(encoding="utf-8"))
        age = (date.today() - date.fromisoformat(m.get("updated_at", "2000-01-01"))).days
        if age <= 7 and m.get("items"):
            market = m
        else:
            print(f"  [info] market_prices.json elavult ({age} nap) – a 4. oldal kimarad")

    html = build_html(fcs, today, stamp, trend_fcs=trend_fcs, market=market)
    html_out = JELENTES_DIR / f"jelentes_{today}.html"
    html_out.write_text(html, encoding="utf-8")
    (JELENTES_DIR / "jelentes_latest.html").write_text(html, encoding="utf-8")
    print(f"[ok] {html_out.name} + jelentes_latest.html")

    if not make_pdf:
        return None
    pdf_out = JELENTES_DIR / f"jelentes_{today}.pdf"
    render_pdf(html_out, pdf_out)
    (config.WEB_DATA / "jelentes_latest.pdf").write_bytes(pdf_out.read_bytes())
    print(f"[ok] {pdf_out.name} ({pdf_out.stat().st_size // 1024} KB) + jelentes_latest.pdf")
    if out_path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(pdf_out.read_bytes())
        print(f"[ok] külső cél: {out_path}")
    return pdf_out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true", help="csak HTML, Playwright nélkül")
    ap.add_argument("--out", default=None,
                    help="a kész PDF-et erre a (tetszőleges) útvonalra is kiírja")
    a = ap.parse_args()
    main(make_pdf=not a.no_pdf, out_path=a.out)
