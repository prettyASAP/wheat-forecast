"""Napi vezetői PDF-jelentés — lényeg + tendencia (hatékonysági felülvizsgálat).

A napi delta-elemek (VÁLTOZÁS TEGNAP ÓTA sáv, kártyánkénti TEGNAP ÓTA blokk)
tudatosan KIKERÜLTEK: napi jelentésben a tegnaphoz mért századtonnás mozgás
zaj, nem információ. Ami maradt: a lényegi számok és a tendencia-ábrák.

1. oldal — Vezetői összefoglaló: három termény-oszlop (becslés, szokásos,
  tavaly, eltérés, 30 napos trendábra a szokásos-referenciavonallal, érték)
  → MA A LÉNYEG összesítő.
2. oldal — Területi kép: térképek + fókusz-vármegyék táblája.
3+. oldal — "Szezonközi kilátás": minden futó szezonú terményre:
  teljes headline → forgatókönyvek t/ha + TONNA + FORINT táblával →
  szezon-trend P10–P90 sávval → fókusz-vármegyék kilátása/időjárása →
  módszertani lábléc (csak a legutolsó oldalon).

Elvek: minden betű >= 12 pt; dinamikus y-folyás minden változó szöveg után;
szín soha nem egyetlen jelzés (előjel + szó mindig); semmi hardcode (ár,
terület, fókuszlista a JSON-ból/configból).

Kimenet: web/data/jelentes/jelentes_YYYY-MM-DD.pdf + web/data/jelentes_latest.pdf
Futtatás:  python -m src.report_pdf
"""
from __future__ import annotations

import json
from datetime import date, datetime

import matplotlib
matplotlib.use("Agg")
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import FancyBboxPatch, Rectangle

from src import config

# ---------------------------------------------------------------------------- #
# Stílus — a web színpalettája
# ---------------------------------------------------------------------------- #
INK = "#2c3e50"
MUTED = "#5a6a75"
LIGHT = "#6e7f8b"
RED = "#c0392b"
GREEN = "#17693a"
BLUE = "#2874a6"
BAND = "#7fb3d5"
BORDER = "#dfe4e8"
CARD_BG = "#fbfcfd"

FS = 12          # minimum betűméret — SEMMI nem lehet kisebb
FS_MID = 14
FS_BIG = 16
FS_KPI = 19
FS_TITLE = 21

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": FS,
    "text.color": INK,
    "axes.edgecolor": BORDER,
})

JELENTES_DIR = config.WEB_DATA / "jelentes"
M = 0.12          # oldalmargó: 25 mm-es szabvány A4-margó (25/210)
M_TOP = 0.916     # tartalom teteje: 25 mm felül (1 - 25/297)
M_BOT = 0.084     # tartalom alja: 25 mm alul (25/297)
GAP_SECTION = 0.034   # fő blokkok közti térköz
GAP_ELEM = 0.012      # elemek közti térköz blokkon belül
LINE = 0.0235         # 12 pt-s sor levegővel
CARD_PAD = 0.018      # kártya belső margó


def hu(v: float, d: int = 2) -> str:
    return f"{v:.{d}f}".replace(".", ",").replace("-", "\u2212")


def signed(v: float, d: int = 2, unit: str = "") -> str:
    """Előjeles magyar szám; a -0,0 elkerülésével."""
    if abs(v) < 0.5 * 10 ** (-d):
        return f"0{',' + '0' * d if d else ''}{unit}"
    return f"{'+' if v > 0 else '−'}{hu(abs(v), d)}{unit}"


# ---------------------------------------------------------------------------- #
# Adatbetöltés
# ---------------------------------------------------------------------------- #
def load_fc(crop: str) -> dict:
    return json.loads((config.WEB_DATA / f"forecast_{crop}.json").read_text(encoding="utf-8"))


def history_series(crop: str, max_days: int = 14) -> list[dict]:
    """Napi snapshotok idősora: [{date, pred, p10, p90, value_bn}] (hiányzót kihagyja)."""
    hdir = config.WEB_DATA / "history" / crop
    out = []
    for p in sorted(hdir.glob("????-??-??.json"))[-max_days:]:
        d = json.loads(p.read_text(encoding="utf-8"))
        nat = d.get("national") or {}
        if nat.get("predicted_yield_t_ha") is None:
            continue
        sc = (d.get("scenarios") or {}).get("national") or {}
        out.append({
            "date": p.stem,
            "pred": nat["predicted_yield_t_ha"],
            "p10": sc.get("p10"),
            "p90": sc.get("p90"),
            "value_bn": (nat.get("value") or {}).get("production_value_bn_huf"),
        })
    return out


# ---------------------------------------------------------------------------- #
# Headline — a web renderHeadline() portja (teljes változat, 2. oldalra)
# ---------------------------------------------------------------------------- #
def headline_text(fc: dict) -> tuple[str, str, str]:
    n = fc["national"]
    a = n["anomaly_pct"]
    v = n.get("value")
    sc = fc.get("scenarios")
    crop = fc["crop"]

    if a <= -3:
        main = (f"A {crop} idei termése {hu(n['predicted_yield_t_ha'])} t/ha körül "
                f"várható, ami {hu(abs(a), 1)}%-kal marad el a sokéves szokásos "
                "szinttől")
        main += (f" — {v['price_year']}-es árakon számolva ez kb. "
                 f"{abs(v['trend_gap_bn_huf']):.0f} mrd Ft kiesést jelent." if v else ".")
    elif a >= 3:
        main = (f"A {crop} idei termése {hu(n['predicted_yield_t_ha'])} t/ha körül "
                f"várható, {hu(a, 1)}%-kal a sokéves szokásos szint felett")
        main += (f" — {v['price_year']}-es árakon számolva ez kb. "
                 f"{v['trend_gap_bn_huf']:.0f} mrd Ft többletet jelent." if v else ".")
    else:
        main = (f"A {crop} idei termése a sokéves szokásos szint közelében, "
                f"{hu(n['predicted_yield_t_ha'])} t/ha körül várható "
                f"({signed(a, 1)}%) — érdemi kiesés vagy többlet egyelőre "
                "nem látszik.")

    if a < 0 and n["yoy_pct"] >= 3:
        main += (f" Tavalyhoz ({n['prev_year']}) képest ez "
                 f"+{hu(n['yoy_pct'], 1)}%-os javulás, a megszokott szinttől "
                 "azonban elmarad.")
    elif a > 0 and n["yoy_pct"] <= -3:
        main += (f" Tavalyhoz ({n['prev_year']}) képest {hu(n['yoy_pct'], 1)}% "
                 "a visszaesés, a termés azonban így is a megszokott szint "
                 "felett alakul.")
    if n["rank_from_worst"] <= 5:
        main += (f" Ha így marad, a {n['rank_total']} év "
                 f"{n['rank_from_worst']}. leggyengébb éve lenne.")
    elif n["rank_from_worst"] >= n["rank_total"] - 4:
        main += (f" Ha így marad, a {n['rank_total']} év "
                 f"{n['rank_total'] - n['rank_from_worst'] + 1}. legerősebb éve lenne.")

    if sc:
        badge = f"MÉG VÁLTOZHAT · {sc['remaining_days']} nap"
        cert = (f"A szezonból még {sc['remaining_days']} nap van hátra; a végeredmény "
                f"az időjárástól függően "
                f"{hu(sc['national']['p10'])}–{hu(sc['national']['p90'])} t/ha "
                "között alakulhat.")
    else:
        badge = "VÉGLEGES KÖZELI"
        cert = ("A szezon időjárása már teljes egészében ismert, a becslés érdemben "
                "nem változik.")
    if n.get("model_error_pct"):
        cert += (" A becslés tipikus tévedése a múltbeli visszamérések alapján "
                 f"±{hu(n['model_error_pct'], 1)}%.")
    return main, badge, cert


def short_headline(fc: dict) -> str:
    """Rövid, 1. oldali headline (UX-spec 2/8. pont)."""
    n = fc["national"]
    a = n["anomaly_pct"]
    v = n.get("value")
    if abs(a) < 3:
        return "a szokásos szint közelében."
    gap = abs(v["trend_gap_bn_huf"]) if v else None
    word = "kiesés" if a < 0 else "többlet"
    txt = f"{signed(a, 1)}% a szokásoshoz"
    if gap is not None:
        txt += f" — kb. {gap:.0f} mrd Ft {word}."
    return txt



# ---------------------------------------------------------------------------- #
# Sorkizárt szöveg (matplotlib-ben nincs natív) — mért szélességű szedés
# ---------------------------------------------------------------------------- #
import re as _re
from matplotlib.font_manager import FontProperties as _FP
from matplotlib.textpath import TextPath as _TP

_PAGE_W_PT = 8.27 * 72
_measure_cache: dict = {}


def _measure(text: str, fontsize: float, bold: bool = False,
             italic: bool = False) -> float:
    """Szöveg szélessége figure-frakcióban (TextPath — renderer nélkül)."""
    key = (text, fontsize, bold, italic)
    if key not in _measure_cache:
        fp = _FP(family="DejaVu Sans",
                 weight="bold" if bold else "normal",
                 style="italic" if italic else "normal")
        if not text.strip():
            # a csak-szóköz TextPath a matplotlibben hibázik -> különbségként mérjük
            w = (_TP((0, 0), f"a{text}a", size=fontsize, prop=fp).get_extents().width
                 - _TP((0, 0), "aa", size=fontsize, prop=fp).get_extents().width)
        else:
            w = _TP((0, 0), text, size=fontsize, prop=fp).get_extents().width
        _measure_cache[key] = w / _PAGE_W_PT
    return _measure_cache[key]


def _bind_units(text: str) -> str:
    """Nem törhető kötés a szám+mértékegység és hasonló párokba (NBSP)."""
    t = text
    t = _re.sub(r"(\d) (t/ha|mrd Ft|mrd|ezer ha|Ft/t|mm|nap|év|M tonna)",
                "\\1\u00a0\\2", t)
    t = _re.sub(r"(mrd|ezer) (Ft|ha|t)", "\\1\u00a0\\2", t)
    t = t.replace("kb. ", "kb.\u00a0")
    return t


def draw_para(page, x: float, y_top: float, width: float, text: str,
              fontsize: float = FS, color: str = INK, bold: bool = False,
              italic: bool = False, justify: bool = True,
              line_h: float | None = None) -> float:
    """Bekezdés szedése: mért tördelés + sorkizárás (az utolsó sor balra zárt).
    Visszaadja a bekezdés alatti y-t."""
    if line_h is None:
        line_h = fontsize / 12 * 0.0235 * 1.02
    words = _bind_units(text).split()
    space_w = _measure(" ", fontsize, bold, italic)
    # mért sortörés
    lines: list[list[str]] = [[]]
    used = 0.0
    for w_ in words:
        ww = _measure(w_.replace("\u00a0", " "), fontsize, bold, italic)
        add = ww if not lines[-1] else ww + space_w
        if lines[-1] and used + add > width:
            lines.append([w_]); used = ww
        else:
            lines[-1].append(w_); used += add
    y = y_top
    for i, line in enumerate(lines):
        last = (i == len(lines) - 1)
        widths = [_measure(w_.replace("\u00a0", " "), fontsize, bold, italic)
                  for w_ in line]
        if justify and not last and len(line) > 1:
            gap = (width - sum(widths)) / (len(line) - 1)
            cx = x
            for w_, ww in zip(line, widths):
                page.text(cx, y, w_.replace("\u00a0", " "), fontsize=fontsize,
                          color=color, va="top",
                          fontweight="bold" if bold else "normal",
                          style="italic" if italic else "normal")
                cx += ww + gap
        else:
            page.text(x, y, " ".join(line).replace("\u00a0", " "),
                      fontsize=fontsize, color=color, va="top",
                      fontweight="bold" if bold else "normal",
                      style="italic" if italic else "normal")
        y -= line_h
    return y


# ---------------------------------------------------------------------------- #
# Rajzoló segédek
# ---------------------------------------------------------------------------- #
def card(page_ax, x, y, w, h, fc_color=CARD_BG):
    page_ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.008",
        facecolor=fc_color, edgecolor=BORDER, linewidth=0.9,
        transform=page_ax.transAxes, zorder=1))


ANOM_COLORS = ["#b03a2e", "#e67e22", "#f5e8c8", "#7fb3d5", "#2874a6"]
ANOM_CMAP = LinearSegmentedColormap.from_list("anom", ANOM_COLORS)
ANOM_NORM = Normalize(vmin=-20, vmax=20)
NO_DATA = "#d5d8dc"


def draw_anom_map(fig, rect, fc: dict, gdf, focus_outline: bool = True):
    """Anomália-choropleth (piros-kék, Budapest szürke); a fókusz-vármegyék
    vastagabb körvonalat kapnak — vizuális kapocs a fókusz-táblához."""
    ax = fig.add_axes(rect)
    anoms = {c["nuts_id"]: (c["anomaly_pct"] if c["predicted_yield_t_ha"] is not None
                            else None) for c in fc["counties"]}
    g = gdf.copy()
    g["anom"] = g["NUTS_ID"].map(anoms)
    g[g["anom"].isna()].plot(ax=ax, color=NO_DATA, edgecolor="#7f8c8d", linewidth=0.4)
    g[g["anom"].notna()].plot(ax=ax, column="anom", cmap=ANOM_CMAP, norm=ANOM_NORM,
                              edgecolor="#7f8c8d", linewidth=0.4)
    if focus_outline:
        focus_ids = {c["nuts_id"] for c in fc["counties"]
                     if c["county_name"] in config.REPORT_FOCUS_COUNTIES}
        sel = g[g["NUTS_ID"].isin(focus_ids)]
        if len(sel):
            sel.plot(ax=ax, facecolor="none", edgecolor=INK, linewidth=1.3)
    ax.set_axis_off()
    return ax


def draw_anom_colorbar(fig, rect):
    cax = fig.add_axes(rect)
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=ANOM_NORM, cmap=ANOM_CMAP),
                      cax=cax, orientation="horizontal")
    cb.set_ticks([-20, -10, 0, 10, 20])
    cb.ax.set_xticklabels(["−20%", "−10%", "0", "+10%", "+20%"], fontsize=FS)
    cb.ax.tick_params(length=2, colors=MUTED)
    cb.outline.set_edgecolor(BORDER)


def pill(page_ax, x, y, text, live: bool, ha="left"):
    col, bg = ("#9a6a12", "#fdf3e0") if live else (GREEN, "#e8f6ee")
    page_ax.text(x, y, f" {text} ", fontsize=FS, color=col, fontweight="bold",
                 ha=ha, va="top", transform=page_ax.transAxes,
                 bbox=dict(boxstyle="round,pad=0.32", fc=bg, ec=col, lw=0.9))


def draw_trend(fig, rect, hs: list[dict], trend_val: float | None = None):
    """Tendencia-ábra: a becslés alakulása a szezonban, a „szokásos" szint
    szaggatott referenciavonalával — a lemaradás/felzárkózás IRÁNYA látszik,
    nem a napi zörej. Y-tengely nem nullától; a szélső értékek kiírva."""
    ax = fig.add_axes(rect)
    ys = [h["pred"] for h in hs]
    xs = range(len(ys))
    lo, hi = min(ys), max(ys)
    if trend_val is not None:
        lo, hi = min(lo, trend_val), max(hi, trend_val)
    pad = (hi - lo) * 0.18 or 0.05
    if trend_val is not None:
        ax.axhline(trend_val, color=MUTED, lw=0.9, ls=(0, (4, 3)), zorder=1)
        ax.annotate("szokásos", (0, trend_val), xytext=(0, 3),
                    textcoords="offset points", fontsize=FS, color=LIGHT,
                    ha="left", va="bottom")
    ax.plot(xs, ys, color=BLUE, lw=1.6, zorder=2)
    ax.scatter([len(ys) - 1], [ys[-1]], s=42, color=INK, zorder=4)
    ax.set_ylim(lo - pad, hi + pad * 2.6)  # felül hely a „szokásos" feliratnak
    ax.set_xlim(-0.5, len(ys) - 0.5)
    ax.set_axis_off()
    # szélső értékek számmal (a levágott tengely ne "hazudjon")
    ax.annotate(hu(ys[0]), (0, ys[0]), xytext=(0, -7), textcoords="offset points",
                fontsize=FS, color=MUTED, ha="left", va="top")
    ax.annotate(hu(ys[-1]), (len(ys) - 1, ys[-1]), xytext=(2, -7),
                textcoords="offset points", fontsize=FS, color=INK,
                ha="right", va="top", fontweight="bold")


def crop_column(fig, page, x, w, top, height, fc: dict):
    """Egy termény-oszlop az 1. oldalon — a tartalomhoz zárt kártyával."""
    n = fc["national"]
    v = n.get("value")
    live = fc.get("scenarios") is not None
    card(page, x, top - height, w, height)
    cx = x + CARD_PAD
    y = top - CARD_PAD

    page.text(cx, y, fc["crop"].capitalize(), fontsize=16, fontweight="bold",
              color=INK, va="top")
    y -= 0.030
    pill(page, cx, y, "MÉG VÁLTOZHAT" if live else "VÉGLEGES KÖZELI", live)
    y -= 0.028
    # státusz-részletsor (minden kártyán — a hármas rács együtt marad)
    page.text(cx, y, f"{fc['scenarios']['remaining_days']} nap van hátra"
              if live else "a szezon lezárult", fontsize=FS, color=MUTED, va="top")
    y -= LINE + GAP_ELEM
    page.text(cx, y, f"{hu(n['predicted_yield_t_ha'])} t/ha", fontsize=FS_KPI,
              fontweight="bold", color=INK, va="top")
    y -= 0.032
    page.text(cx, y, f"szokásos: {hu(n['trend_t_ha'])}", fontsize=FS,
              color=MUTED, va="top")
    y -= LINE
    page.text(cx, y, f"tavaly: {hu(n['prev_year_yield_t_ha'])}", fontsize=FS,
              color=MUTED, va="top")
    y -= LINE + GAP_ELEM
    a = n["anomaly_pct"]
    a_col = RED if a < -0.05 else GREEN if a > 0.05 else INK
    page.text(cx, y, f"{signed(a, 1)}%", fontsize=FS_BIG, fontweight="bold",
              color=a_col, va="top")
    y -= 0.030
    page.text(cx, y, "a szokásoshoz képest", fontsize=FS, color=MUTED, va="top")
    y -= LINE + GAP_ELEM
    # tendencia-ábra: 30 napos becslés-alakulás a „szokásos" referenciavonallal
    hs = history_series(fc_crop_key(fc), max_days=30)
    if len(hs) >= 2:
        draw_trend(fig, [x + CARD_PAD, y - 0.082, w - 2 * CARD_PAD, 0.072],
                   hs, trend_val=n.get("trend_t_ha"))
        y -= 0.098
        page.text(cx, y, f"tendencia — {len(hs)} nap", fontsize=FS,
                  color=LIGHT, va="top")
        y -= LINE + 0.004
    if v:
        gap = v["trend_gap_bn_huf"]
        page.text(cx, y, f"{v['production_value_bn_huf']:.0f} mrd Ft",
                  fontsize=FS_MID, fontweight="bold", color=INK, va="top")
        y -= 0.026
        # AD: piros-redukció — a "vs szokásos" semleges sötétszürke
        page.text(cx, y, f"a szokásostól: {signed(gap, 0)} mrd",
                  fontsize=FS, color="#444444", va="top")
        y -= LINE
        page.text(cx, y, f"{v['price_year']}-es áron, indikatív",
                  fontsize=FS, color=LIGHT, va="top", style="italic")


def fc_crop_key(fc: dict) -> str:
    for key, spec in config.CROPS.items():
        if spec["label"] == fc["crop"]:
            return key
    raise KeyError(fc["crop"])


def page_frame(fig, title: str, subtitle: str, updated: str):
    """Közös oldalkeret: cím + dátum + alcím + elválasztó. Visszaadja (page, y)."""
    page = fig.add_axes([0, 0, 1, 1]); page.set_axis_off()
    page.set_xlim(0, 1); page.set_ylim(0, 1)
    page.text(M, M_TOP, title, fontsize=FS_TITLE, fontweight="bold",
              color=INK, va="top")
    page.text(1 - M, M_TOP, updated, fontsize=FS_MID, color=MUTED,
              ha="right", va="top")
    page.text(M, M_TOP - 0.029, subtitle, fontsize=FS, color=MUTED, va="top")
    page.plot([M, 1 - M], [M_TOP - 0.047, M_TOP - 0.047], color=BORDER, lw=0.9,
              transform=page.transAxes)
    return page, M_TOP - 0.047 - 0.026


def page_footer(page, page_no: int, total: int, note: str = ""):
    txt = (f"{page_no}/{total} · Terméshozam-előrejelző · generálva: "
           f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if note:
        txt += f" · {note}"
    # az oldalszám-sor a hagyomány szerint az alsó margósávban ül, a tartalom alatt
    page.text(M, 0.036, txt, fontsize=FS, color=LIGHT, va="bottom")


# ---------------------------------------------------------------------------- #
# 1. oldal — vezetői összefoglaló (CSAK a napi sáv + három oszlop)
# ---------------------------------------------------------------------------- #
def draw_summary_page(pdf: PdfPages, fcs: dict[str, dict],
                      total_pages: int) -> None:
    fig = plt.figure(figsize=(8.27, 11.69))
    any_fc = next(iter(fcs.values()))
    page, y = page_frame(
        fig, "Napi vezetői jelentés",
        f"{any_fc['crop_year']}-es termésév · búza, kukorica, őszi árpa · "
        "vármegyei statisztikai modell", any_fc["updated_at"])

    # három termény-oszlop — a lényeg + a tendencia; napi delta tudatosan nincs
    # (hatékonysági felülvizsgálat: a tegnaphoz mért mozgás zaj, nem információ)
    y -= 0.010
    col_h = 0.47
    col_w = (1 - 2 * M - 2 * 0.018) / 3
    for i, (crop, fc) in enumerate(fcs.items()):
        crop_column(fig, page, M + i * (col_w + 0.018), col_w, y, col_h, fc)
    y -= col_h + 0.036

    # MA A LÉNYEG — a három termény együtt (AD #4: a felszabadult sáv tartalma)
    vals = [fc["national"].get("value") for fc in fcs.values()]
    if all(vals):
        total_val = sum(v["production_value_bn_huf"] for v in vals)
        total_gap = sum(v["trend_gap_bn_huf"] for v in vals)
        page.plot([M, 1 - M], [y, y], color=BORDER, lw=0.9,
                  transform=page.transAxes)
        y -= 0.018
        page.text(M, y, "MA A LÉNYEG", fontsize=FS, fontweight="bold",
                  color=LIGHT, va="top")
        y -= 0.025
        page.text(M, y, f"A három termény együtt ~{total_val:.0f} mrd Ft "
                  "termelési értéket ígér,", fontsize=FS_MID, fontweight="bold",
                  color=INK, va="top")
        y -= 0.025
        page.text(M, y, f"ami {signed(total_gap, 0)} mrd Ft a szokásoshoz képest",
                  fontsize=FS_MID, fontweight="bold", color=INK, va="top")
        y -= 0.025
        page.text(M, y, "a legutolsó hivatalos (2024-es) termelői árakon — indikatív",
                  fontsize=FS, color=LIGHT, va="top", style="italic")

    page_footer(page, 1, total_pages, "Módszertan: utolsó oldal.")
    pdf.savefig(fig)
    plt.close(fig)


# ---------------------------------------------------------------------------- #
# 2. oldal — területi kép (nagy térképek) + fókusz-vármegyék
# ---------------------------------------------------------------------------- #
def draw_focus_table(page, y_top: float, fcs: dict[str, dict]) -> float:
    names = " · ".join(config.REPORT_FOCUS_COUNTIES)
    page.text(M, y_top, f"FÓKUSZ-VÁRMEGYÉK — {names}", fontsize=13,
              fontweight="bold", color=LIGHT, va="top")
    y = y_top - 0.030

    scales = {}
    for crop, fc in fcs.items():
        lows, highs = [fc["national"]["predicted_yield_t_ha"]], []
        for name in config.REPORT_FOCUS_COUNTIES:
            rec = next((c for c in fc["counties"] if c["county_name"] == name), None)
            if rec and rec["predicted_yield_t_ha"] is not None:
                lows.append(rec["low"]); highs.append(rec["high"])
        lo, hi = min(lows), max(highs + lows)
        pad = (hi - lo) * 0.06 or 0.3
        scales[fc["crop"]] = (lo - pad, hi + pad)

    x_name, x_pred, x_anom, x_vs = M + 0.012, M + 0.22, M + 0.36, M + 0.50
    bar_x0, bar_x1 = M + 0.53, 1 - M - 0.012

    # fejléc-sor: a cellákban így csak számok maradnak (tipográfiai tisztaság)
    for hx_, htxt in [(x_pred, "becslés"), (x_anom, "szokásostól"),
                      (x_vs, "országostól")]:
        page.text(hx_, y, htxt, fontsize=FS, color=LIGHT, va="top", ha="right")
    page.text(bar_x0, y, "várható tartomány", fontsize=FS, color=LIGHT, va="top")
    y -= LINE + 0.004

    for county, per_crop in focus_rows(fcs):
        page.text(M, y, county, fontsize=13, fontweight="bold", color=INK, va="top")
        y -= LINE
        for label, rec, fc in per_crop:
            page.text(x_name, y, label, fontsize=FS, color=MUTED, va="top")
            if rec is None or rec["predicted_yield_t_ha"] is None:
                page.text(x_pred, y, "nincs becslés", fontsize=FS, color=MUTED,
                          va="top")
                y -= LINE
                continue
            nat_pred = fc["national"]["predicted_yield_t_ha"]
            page.text(x_pred, y, f"{hu(rec['predicted_yield_t_ha'])} t/ha",
                      fontsize=FS, fontweight="bold", color=INK, va="top", ha="right")
            a = rec["anomaly_pct"]
            a_col = RED if a < -0.05 else GREEN if a > 0.05 else INK
            page.text(x_anom, y, f"{signed(a, 1)}%", fontsize=FS, color=a_col,
                      va="top", ha="right")
            dv = rec["predicted_yield_t_ha"] - nat_pred
            vs = "≈ 0" if abs(dv) < 0.05 else signed(dv, 2)
            vs_col = MUTED if abs(dv) < 0.05 else (GREEN if dv > 0 else RED)
            page.text(x_vs, y, vs, fontsize=FS, color=vs_col, va="top", ha="right")
            lo_s, hi_s = scales[fc["crop"]]
            def X(v):
                return bar_x0 + (v - lo_s) / (hi_s - lo_s) * (bar_x1 - bar_x0)
            bar_y = y - 0.013
            page.add_patch(Rectangle((X(rec["low"]), bar_y - 0.004),
                                     X(rec["high"]) - X(rec["low"]), 0.008,
                                     facecolor=BAND, alpha=0.5, edgecolor="none",
                                     transform=page.transAxes, zorder=2))
            page.plot([X(nat_pred), X(nat_pred)], [bar_y - 0.008, bar_y + 0.008],
                      color=MUTED, lw=1.8, transform=page.transAxes, zorder=3)
            page.scatter([X(rec["predicted_yield_t_ha"])], [bar_y], s=34,
                         color=INK, transform=page.transAxes, zorder=4)
            y -= LINE
        y -= GAP_ELEM
    page.text(M, y, "sáv: 80%-os tartomány · pont: becslés · vonás: országos",
              fontsize=FS, color=LIGHT, va="top")
    return y - LINE


def draw_map_page(pdf: PdfPages, fcs: dict[str, dict], gdf,
                  page_no: int, total_pages: int) -> None:
    fig = plt.figure(figsize=(8.27, 11.69))
    any_fc = next(iter(fcs.values()))
    page, y = page_frame(
        fig, "Területi kép",
        "eltérés a szokásos hozamtól vármegyénként · vastag keret: fókusz-vármegye",
        any_fc["updated_at"])

    # három NAGY térkép egymás alatt kettő+egy elrendezés helyett: 2 felül, 1 alul balra,
    # jobbra alul a jelmagyarázat — mindegyik térkép nagy és olvasható
    map_w, map_h = 0.37, 0.148
    positions = [(M, y - map_h), (1 - M - map_w, y - map_h),
                 (M, y - 2 * map_h - 0.042)]
    for (mx, my), (crop, fc) in zip(positions, fcs.items()):
        draw_anom_map(fig, [mx, my, map_w, map_h], fc, gdf)
        a = fc["national"]["anomaly_pct"]
        page.text(mx + map_w / 2, my + map_h + 0.004,
                  f"{fc['crop']} · országos: {signed(a, 1)}%",
                  fontsize=13, fontweight="bold", color=INK, va="bottom",
                  ha="center")

    # jelmagyarázat a jobb alsó negyedben
    lx = 1 - M - map_w + 0.03
    ly = y - 2 * map_h - 0.042 + map_h - 0.050
    draw_anom_colorbar(fig, [lx, ly, map_w - 0.08, 0.010])
    draw_para(page, lx, ly - 0.030, map_w - 0.08,
              "piros: elmaradás a szokásostól · kék: többlet · szürke: nincs "
              "becslés (Budapest)", fontsize=FS, color=MUTED, line_h=0.0245,
              justify=False)

    # fókusz-vármegyék táblája az alsó harmadban
    draw_focus_table(page, y - 2 * map_h - 0.042 - 0.026, fcs)

    page_footer(page, page_no, total_pages)
    pdf.savefig(fig)
    plt.close(fig)


def focus_rows(fcs: dict[str, dict]):
    out = []
    for name in config.REPORT_FOCUS_COUNTIES:
        per_crop = []
        for crop, fc in fcs.items():
            rec = next((c for c in fc["counties"] if c["county_name"] == name), None)
            per_crop.append((fc["crop"], rec, fc))
        out.append((name, per_crop))
    return out


# ---------------------------------------------------------------------------- #
# 3+. oldal — "Ami még él" (futó szezonú termény), levegős elrendezés
# ---------------------------------------------------------------------------- #
def draw_live_page(pdf: PdfPages, fc: dict, page_no: int, total_pages: int,
                   is_last: bool, err_range: str = "±9–22%") -> None:
    crop = fc_crop_key(fc)
    n = fc["national"]
    v = n.get("value")
    sc = fc["scenarios"]
    main, badge, cert = headline_text(fc)

    fig = plt.figure(figsize=(8.27, 11.69))
    page, y = page_frame(
        fig, f"Szezonközi kilátás — {fc['crop']}",
        f"még {sc['remaining_days']} nap van hátra a szezonból — a becslés változhat",
        fc["updated_at"])
    # teljes headline — sorkizárt szedéssel (a MÉG VÁLTOZHAT jelzést az alcím
    # hordozza, külön pill már nem kell — nem takarhat szöveget)
    y = draw_para(page, M, y, 1 - 2 * M, main, fontsize=13, color=INK,
                  bold=True, line_h=0.0235)
    y -= GAP_ELEM
    y = draw_para(page, M, y, 1 - 2 * M, cert, fontsize=FS, color=MUTED,
                  line_h=0.0205)
    y -= 0.012

    # forgatókönyv-blokk: sáv balra, tonna/forint tábla jobbra
    fsn = sc["national"]
    area = v["area_ha"] if v else None
    price = v["price_huf_per_t"] if v else None
    p10, p50, p90 = fsn["p10"], fsn["p50"], fsn["p90"]
    point = n["predicted_yield_t_ha"]

    ax = fig.add_axes([M + 0.015, y - 0.112, 0.34, 0.082])
    lo, hi = min(p10, point), max(p90, point)
    pad = (hi - lo) * 0.2 or 0.5
    ax.barh(0, p90 - p10, left=p10, height=0.34, color=BAND, alpha=0.5, zorder=2)
    ax.plot([p50, p50], [-0.3, 0.3], color=BLUE, lw=2.5, zorder=3)
    ax.scatter([point], [0], marker="v", s=130, color=INK, zorder=4)
    ax.annotate(f"kedvezőtlen\n{hu(p10)}", (p10, 0), xytext=(0, -38),
                textcoords="offset points", ha="center", fontsize=FS,
                color=MUTED, linespacing=1.45)
    ax.annotate(f"kedvező\n{hu(p90)}", (p90, 0), xytext=(0, -38),
                textcoords="offset points", ha="center", fontsize=FS,
                color=MUTED, linespacing=1.45)
    ax.annotate(f"becslés {hu(point)}", (point, 0), xytext=(0, 15),
                textcoords="offset points", ha="center", fontsize=FS,
                color=INK, fontweight="bold")
    ax.set_xlim(lo - pad, hi + pad); ax.set_ylim(-1.5, 1.0)
    ax.set_axis_off()
    ax.set_title("Forgatókönyvek (t/ha)", fontsize=13, color=INK, pad=4, loc="left")

    tx = 0.52
    page.text(tx, y, "Terményben és forintban*", fontsize=13,
              fontweight="bold", color=INK, va="top")
    ty = y - 0.030
    cols_x = [tx, tx + 0.18, tx + 0.27, tx + 0.36]
    for cx_, htxt in zip(cols_x, ["", "t/ha", "M tonna", "mrd Ft*"]):
        if htxt:
            page.text(cx_, ty, htxt, fontsize=FS, color=LIGHT, va="top", ha="right")
    ty -= LINE
    for label, pv, bold in [("Kedvezőtlen", p10, False), ("Középső", p50, True),
                            ("Kedvező", p90, False)]:
        weight = "bold" if bold else "normal"
        page.text(tx, ty, label, fontsize=FS, color=INK, va="top", fontweight=weight)
        page.text(cols_x[1], ty, hu(pv), fontsize=FS, color=INK, va="top",
                  ha="right", fontweight=weight)
        if area and price:
            tons = pv * area
            page.text(cols_x[2], ty, hu(tons / 1e6), fontsize=FS, color=INK,
                      va="top", ha="right", fontweight=weight)
            page.text(cols_x[3], ty, f"{tons * price / 1e9:.0f}", fontsize=FS,
                      color=INK, va="top", ha="right", fontweight=weight)
        ty -= LINE
    y = min(y - 0.122, ty - GAP_ELEM)

    if area and price:
        risk = (p90 - p10) * area * price / 1e9
        y = draw_para(page, M, y, 1 - 2 * M,
                      f"A két szélső kimenet között kb. {risk:.0f} mrd Ft "
                      "a különbség.", fontsize=FS_MID, bold=True, color=INK,
                      line_h=0.028)
        y -= 0.002
        note = (f"*{v['price_year']}-es termelői átlagáron "
                f"({price:,.0f} Ft/t".replace(",", " ") +
                f") és a legutóbbi lezárt évi vetésterülettel "
                f"({area / 1e3:.0f} ezer ha) számolva — indikatív.")
        y = draw_para(page, M, y, 1 - 2 * M, note, fontsize=FS, color=LIGHT,
                      italic=True, line_h=0.0215)
        y -= 0.008

    # trend-részlet
    hs = history_series(crop, max_days=30)
    if len(hs) >= 2:
        ax2 = fig.add_axes([M + 0.045, y - 0.102, 1 - 2 * M - 0.065, 0.072])
        xs = range(len(hs))
        preds = [h["pred"] for h in hs]
        if all(h["p10"] is not None for h in hs):
            ax2.fill_between(xs, [h["p10"] for h in hs], [h["p90"] for h in hs],
                             color=BAND, alpha=0.25, zorder=1)
        ax2.plot(xs, preds, color=BLUE, lw=1.8, zorder=3)
        ax2.scatter(xs, preds, s=28, color=BLUE, zorder=4)
        ax2.annotate(hu(preds[-1]), (len(hs) - 1, preds[-1]), xytext=(0, 9),
                     textcoords="offset points", ha="center", fontsize=FS,
                     color=INK, fontweight="bold")
        step = max(1, len(hs) // 6)
        ax2.set_xticks(list(xs)[::step])
        ax2.set_xticklabels([hs[i]["date"][5:] for i in list(xs)[::step]],
                            fontsize=FS)
        ax2.tick_params(axis="y", labelsize=FS, colors=MUTED)
        ax2.set_yticklabels([hu(t, 1) for t in ax2.get_yticks()])
        ax2.spines[["top", "right"]].set_visible(False)
        ax2.set_title("A becslés alakulása a szezonban (P10–P90 sávval, t/ha)",
                      fontsize=13, color=INK, loc="left", pad=6)
        y -= 0.102 + 0.026

    # fókusz-vármegyék kilátása + időjárása
    page.text(M, y, "FÓKUSZ-VÁRMEGYÉK — kilátás és időjárás", fontsize=13,
              fontweight="bold", color=LIGHT, va="top")
    y -= 0.022
    heads = ["", "Becslés", "P10–P90", "Hőstressz", "Vízmérleg", "Csapadék"]
    hx = [M, M + 0.24, M + 0.42, M + 0.545, M + 0.655, M + 0.76]
    for cx_, htxt in zip(hx[1:], heads[1:]):
        page.text(cx_, y, htxt, fontsize=FS, color=LIGHT, va="top", ha="right")
    y -= LINE
    sc_counties = sc.get("counties") or {}
    for name in config.REPORT_FOCUS_COUNTIES:
        rec = next((c for c in fc["counties"] if c["county_name"] == name), None)
        if rec is None:
            continue
        page.text(M, y, name, fontsize=FS, color=INK, va="top")
        if rec["predicted_yield_t_ha"] is None:
            page.text(hx[1], y, "nincs becslés", fontsize=FS, color=MUTED,
                      va="top", ha="right")
            y -= LINE
            continue
        page.text(hx[1], y, f"{hu(rec['predicted_yield_t_ha'])} t/ha", fontsize=FS,
                  fontweight="bold", color=INK, va="top", ha="right")
        scc = sc_counties.get(rec["nuts_id"])
        page.text(hx[2], y, f"{hu(scc['p10'])}–{hu(scc['p90'])}" if scc else "–",
                  fontsize=FS, color=MUTED, va="top", ha="right")
        wx = rec["weather_todate"]
        heat = wx["heat_days"]
        page.text(hx[3], y, f"{heat} nap", fontsize=FS,
                  color=RED if heat > 5 else INK, va="top", ha="right")
        wb = wx["wb_total_mm"]
        page.text(hx[4], y, f"{hu(wb, 0)} mm", fontsize=FS,
                  color=RED if wb < -300 else INK, va="top", ha="right")
        page.text(hx[5], y, f"{hu(wx['prec_total_mm'], 0)} mm", fontsize=FS,
                  color=INK, va="top", ha="right")
        y -= LINE
    y = draw_para(page, M, y - 0.002, 1 - 2 * M,
                  "Vízmérleg: a csapadék és a párolgás egyenlege a szezon eddigi "
                  "részében — minél negatívabb, annál erősebb az aszálynyomás.",
                  fontsize=FS, color=MUTED, line_h=0.0215, justify=False)

    # módszertani lábléc — csak a legutolsó oldalon, az értelmező sor ALÁ folyatva
    if is_last:
        div_y = y - 0.006
        page.plot([M, 1 - M], [div_y, div_y], color=BORDER, lw=0.9,
                  transform=page.transAxes)
        foot = (
            "Módszertan: statisztikai modell (KSH hozamok 2000-től + ERA5); "
            f"tipikus tévedés {err_range}. Nem hivatalos adat. Részletek: "
            "prettyasap.github.io/wheat-forecast · KSH, Open-Meteo/ERA5, Eurostat."
        )
        draw_para(page, M, div_y - 0.012, 1 - 2 * M, foot, fontsize=FS,
                  color=LIGHT, line_h=0.0205, justify=False)
    page_footer(page, page_no, total_pages)
    pdf.savefig(fig)
    plt.close(fig)


def main() -> None:
    today = date.today().isoformat()
    JELENTES_DIR.mkdir(parents=True, exist_ok=True)

    fcs = {crop: load_fc(crop) for crop in config.CROPS}
    gdf = gpd.read_file(config.WEB_DATA / "nuts3_hu.geojson")
    live_crops = [c for c, fc in fcs.items() if fc.get("scenarios")]
    total_pages = 2 + len(live_crops)
    errs = [fc["national"]["model_error_pct"] for fc in fcs.values()
            if fc.get("national", {}).get("model_error_pct")]
    err_range = (f"±{hu(min(errs), 0)}–{hu(max(errs), 0)}%"
                 if errs else "±9–22%")

    out = JELENTES_DIR / f"jelentes_{today}.pdf"
    with PdfPages(out) as pdf:
        draw_summary_page(pdf, fcs, total_pages)
        draw_map_page(pdf, fcs, gdf, 2, total_pages)
        for i, crop in enumerate(live_crops):
            draw_live_page(pdf, fcs[crop], 3 + i, total_pages,
                           is_last=(i == len(live_crops) - 1),
                           err_range=err_range)
        info = pdf.infodict()
        info["Title"] = f"Napi vezetői jelentés — {today}"
        info["Author"] = "Terméshozam-előrejelző (statisztikai modell)"
    latest = config.WEB_DATA / "jelentes_latest.pdf"
    latest.write_bytes(out.read_bytes())
    print(f"[ok] {out} ({out.stat().st_size // 1024} KB, {total_pages} oldal) "
          f"+ jelentes_latest.pdf")


if __name__ == "__main__":
    main()
