"""Napi vezetői PDF-jelentés — 2 oldalas szerkezet (ügyfél-igényfelmérés + UX-spec).

1. oldal — Vezetői összefoglaló: NAPI VÁLTOZÁS sáv (a vezető fő kérdése) →
  három termény-oszlop (becslés, eltérés, tegnap óta, sparkline, érték) →
  fókusz-vármegyék táblája sáv-grafikával.
2+. oldal — "Ami még él": minden futó szezonú terményre (most: kukorica):
  teljes headline → forgatókönyvek t/ha + TONNA + FORINT táblával →
  napi becslés-trend P10–P90 sávval → fókusz-vármegyék kilátása/időjárása →
  módszertani lábléc (csak a legutolsó oldalon).

Elvek: minden betű >= 12 pt; dinamikus y-folyás minden változó szöveg után;
szín soha nem egyetlen jelzés (előjel + szó mindig); semmi hardcode (ár,
terület, fókuszlista a JSON-ból/configból).

Kimenet: web/data/jelentes/jelentes_YYYY-MM-DD.pdf + web/data/jelentes_latest.pdf
Futtatás:  python -m src.report_pdf
"""
from __future__ import annotations

import json
import textwrap
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
INNER_BG = "#f2f5f7"

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
M = 0.06  # oldalmargó


def hu(v: float, d: int = 2) -> str:
    return f"{v:.{d}f}".replace(".", ",")


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


def daily_delta(crop: str) -> dict | None:
    """Δ tegnaphoz: {d_tha, d_bn, prev_date} vagy None (első nap / nem összevethető)."""
    hs = history_series(crop, max_days=30)
    if len(hs) < 2:
        return None
    prev, cur = hs[-2], hs[-1]
    d = {
        "d_tha": cur["pred"] - prev["pred"],
        "prev_date": prev["date"],
        "cur_date": cur["date"],
    }
    if cur.get("value_bn") is not None and prev.get("value_bn") is not None:
        d["d_bn"] = cur["value_bn"] - prev["value_bn"]
    return d


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
                f"várható, {hu(abs(a), 1)}%-kal a sokéves szokásos alatt")
        main += (f" — ez a {v['price_year']}-es árakon kb. "
                 f"{abs(v['trend_gap_bn_huf']):.0f} mrd Ft kiesés." if v else ".")
    elif a >= 3:
        main = (f"A {crop} termése {hu(a, 1)}%-kal a szokásos felett várható "
                f"({hu(n['predicted_yield_t_ha'])} t/ha)")
        main += (f" — kb. {v['trend_gap_bn_huf']:.0f} mrd Ft többlet "
                 f"a {v['price_year']}-es árakon." if v else ".")
    else:
        main = (f"A {crop} termése a szokásos szint közelében alakul "
                f"({hu(n['predicted_yield_t_ha'])} t/ha, {signed(a, 1)}%) — érdemi "
                "kiesés vagy többlet egyelőre nem látszik.")

    if a < 0 and n["yoy_pct"] >= 3:
        main += (f" Jóval jobb, mint a tavalyi gyenge év (+{hu(n['yoy_pct'], 1)}% "
                 f"vs {n['prev_year']}), de a megszokott szinttől elmarad.")
    elif a > 0 and n["yoy_pct"] <= -3:
        main += (f" A tavalyi kiugró évnél gyengébb ({hu(n['yoy_pct'], 1)}% "
                 f"vs {n['prev_year']}), de a megszokott szint felett.")
    if n["rank_from_worst"] <= 5:
        main += (f" Ha így marad, a {n['rank_total']} év "
                 f"{n['rank_from_worst']}. leggyengébb éve lenne.")
    elif n["rank_from_worst"] >= n["rank_total"] - 4:
        main += (f" Ha így marad, a {n['rank_total']} év "
                 f"{n['rank_total'] - n['rank_from_worst'] + 1}. legerősebb éve lenne.")

    if sc:
        badge = f"MÉG VÁLTOZHAT · {sc['remaining_days']} nap"
        cert = (f"A szezonból {sc['remaining_days']} nap van hátra — az időjárástól "
                f"függően {hu(sc['national']['p10'])}–{hu(sc['national']['p90'])} t/ha "
                "között alakulhat.")
    else:
        badge = "VÉGLEGES KÖZELI"
        cert = ("A szezon időjárása teljes egészében ismert — a becslés már érdemben "
                "nem változik.")
    if n.get("model_error_pct"):
        cert += f" A becslés tipikus tévedése a múltban ±{hu(n['model_error_pct'], 1)}% volt."
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
                 bbox=dict(boxstyle="round,pad=0.32", fc=bg, ec=col, lw=0.8))


def delta_band_str(d: dict | None) -> tuple[str, str]:
    """A felső sáv cellájába: t/ha + mrd Ft, szó nélkül (12pt-vel elfér)."""
    if d is None:
        return "első jelentési nap", MUTED
    if abs(d["d_tha"]) < 0.005:
        return "— változatlan", MUTED
    arrow = "▲" if d["d_tha"] > 0 else "▼"
    txt = f"{arrow} {signed(d['d_tha'], 2)} t/ha"
    if d.get("d_bn") is not None and abs(d["d_bn"]) >= 0.05:
        txt += f" · {signed(d['d_bn'], 1)} mrd"
    return txt, (GREEN if d["d_tha"] > 0 else RED)


def delta_str(d: dict | None, compact: bool = False) -> tuple[str, str]:
    """(szöveg, szín) a napi változáshoz — előjel ÉS szó, sosem csak szín.
    compact=True: rövid forma a szűk cellákba (a szó a 2. sorba kerül)."""
    if d is None:
        return "első jelentési nap", MUTED
    if abs(d["d_tha"]) < 0.005:
        return "— változatlan", MUTED
    arrow = "▲" if d["d_tha"] > 0 else "▼"
    word = "javulás" if d["d_tha"] > 0 else "romlás"
    txt = f"{arrow} {signed(d['d_tha'], 2)} t/ha"
    if compact:
        return txt + f" ({word})", (GREEN if d["d_tha"] > 0 else RED)
    if d.get("d_bn") is not None and abs(d["d_bn"]) >= 0.05:
        txt += f" · {signed(d['d_bn'], 1)} mrd Ft"
    txt += f" ({word})"
    return txt, (GREEN if d["d_tha"] > 0 else RED)


def draw_sparkline(fig, rect, hs: list[dict]):
    """Mini idősor: napi becslések; y-tengely nem nullától, szélső értékek kiírva."""
    ax = fig.add_axes(rect)
    ys = [h["pred"] for h in hs]
    xs = range(len(ys))
    ax.plot(xs, ys, color=BLUE, lw=1.6, zorder=2)
    ax.scatter(xs, ys, s=22, color=BLUE, zorder=3)
    ax.scatter([len(ys) - 1], [ys[-1]], s=48, color=INK, zorder=4)
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.25 or 0.05
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-0.5, len(ys) - 0.5)
    ax.set_axis_off()
    # szélső értékek számmal (a levágott tengely ne "hazudjon")
    ax.annotate(hu(ys[0]), (0, ys[0]), xytext=(-2, -14), textcoords="offset points",
                fontsize=FS, color=MUTED, ha="left")
    ax.annotate(hu(ys[-1]), (len(ys) - 1, ys[-1]), xytext=(2, 6),
                textcoords="offset points", fontsize=FS, color=INK,
                ha="right", fontweight="bold")


def crop_column(fig, page, x, w, fc: dict, d: dict | None):
    """Egy termény-oszlop az 1. oldalon. A kártya fix magas, a tartalom folyik."""
    n = fc["national"]
    v = n.get("value")
    live = fc.get("scenarios") is not None
    top, height = 0.838, 0.335
    card(page, x, top - height, w, height)
    pad = 0.014
    cx = x + pad
    y = top - 0.010

    # 1. terménynév
    page.text(cx, y, fc["crop"].capitalize(), fontsize=15, fontweight="bold",
              color=INK, va="top")
    y -= 0.023
    # 2. státusz-pill
    pill(page, cx, y, "MÉG VÁLTOZHAT" if live else "VÉGLEGES KÖZELI", live)
    if live:
        page.text(cx + 0.208, y - 0.003, f"{fc['scenarios']['remaining_days']} nap",
                  fontsize=FS, color=MUTED, va="top")
    y -= 0.030
    # 3. becslés
    page.text(cx, y, f"{hu(n['predicted_yield_t_ha'])} t/ha", fontsize=FS_KPI,
              fontweight="bold", color=INK, va="top")
    y -= 0.031
    page.text(cx, y, f"szokásos: {hu(n['trend_t_ha'])} · tavaly: "
              f"{hu(n['prev_year_yield_t_ha'])}", fontsize=FS, color=MUTED, va="top")
    y -= 0.022
    # 4. eltérés
    a = n["anomaly_pct"]
    a_col = RED if a < -0.05 else GREEN if a > 0.05 else INK
    page.text(cx, y, f"{signed(a, 1)}%", fontsize=FS_BIG, fontweight="bold",
              color=a_col, va="top")
    page.text(cx + 0.112, y - 0.004, "a szokásoshoz", fontsize=FS, color=MUTED, va="top")
    y -= 0.030
    # 5. TEGNAP ÓTA — hangsúlyos belső sáv
    band_h = 0.040
    page.add_patch(Rectangle((cx - 0.004, y - band_h), w - 2 * pad + 0.008, band_h,
                             facecolor=INNER_BG, edgecolor="none",
                             transform=page.transAxes, zorder=2))
    page.text(cx, y - 0.006, "TEGNAP ÓTA", fontsize=FS, color=LIGHT, va="top", zorder=3)
    dtxt, dcol = delta_str(d, compact=True)
    page.text(cx, y - 0.023, dtxt, fontsize=FS_MID, fontweight="bold",
              color=dcol, va="top", zorder=3)
    y -= band_h + 0.012
    # 6. sparkline
    hs = history_series(fc_crop_key(fc))
    if len(hs) >= 2:
        draw_sparkline(fig, [x + pad, y - 0.048, w - 2 * pad, 0.042], hs)
        y -= 0.056
        page.text(cx, y, f"napi becslések ({len(hs)} nap)", fontsize=FS,
                  color=LIGHT, va="top")
        y -= 0.020
    # 7. érték-hatás
    if v:
        gap = v["trend_gap_bn_huf"]
        page.text(cx, y, f"{v['production_value_bn_huf']:.0f} mrd Ft",
                  fontsize=FS_MID, fontweight="bold", color=INK, va="top")
        y -= 0.021
        g_col = RED if gap < -0.05 else GREEN if gap > 0.05 else INK
        page.text(cx, y, f"vs szokásos: {signed(gap, 0)} mrd Ft",
                  fontsize=FS, color=g_col, va="top")
        y -= 0.019
        page.text(cx, y, f"{v['price_year']}-es áron — indikatív",
                  fontsize=FS, color=LIGHT, va="top", style="italic")
        y -= 0.022
    # (a rövid headline kikerült: számai — eltérés % és mrd Ft — fent szerepelnek;
    # a felszabaduló hely a térkép-soré)


def fc_crop_key(fc: dict) -> str:
    """crop kulcs (wheat/corn/barley) visszakeresése a label alapján."""
    for key, spec in config.CROPS.items():
        if spec["label"] == fc["crop"]:
            return key
    raise KeyError(fc["crop"])


def focus_rows(fcs: dict[str, dict]) -> list[tuple[str, list[tuple[str, dict]]]]:
    """[(vármegyenév, [(crop_label, county_rec vagy None), ...]), ...]"""
    out = []
    for name in config.REPORT_FOCUS_COUNTIES:
        per_crop = []
        for crop, fc in fcs.items():
            rec = next((c for c in fc["counties"] if c["county_name"] == name), None)
            per_crop.append((fc["crop"], rec, fc))
        out.append((name, per_crop))
    return out


def draw_focus_table(page, y_top: float, fcs: dict[str, dict]) -> float:
    """Fókusz-vármegyék blokkja az 1. oldalon. Visszaadja az alsó y-t."""
    names = " · ".join(config.REPORT_FOCUS_COUNTIES)
    page.text(M, y_top, f"FÓKUSZ-VÁRMEGYÉK — {names}", fontsize=13,
              fontweight="bold", color=LIGHT, va="top")
    y = y_top - 0.026

    # terményenkénti közös sáv-skála (fókuszmegyék + országos)
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

    line_h = 0.019
    # oszlop-x pozíciók (jobbra zárt számok)
    x_name, x_pred, x_anom, x_vs = M + 0.012, M + 0.28, M + 0.40, M + 0.585
    bar_x0, bar_x1 = M + 0.615, 1 - M - 0.012

    for county, per_crop in focus_rows(fcs):
        page.text(M, y, county, fontsize=13, fontweight="bold", color=INK, va="top")
        y -= line_h
        for label, rec, fc in per_crop:
            page.text(x_name, y, label, fontsize=FS, color=MUTED, va="top")
            if rec is None or rec["predicted_yield_t_ha"] is None:
                page.text(x_pred, y, "nincs becslés (kevés termőterület)",
                          fontsize=FS, color=MUTED, va="top")
                y -= line_h
                continue
            nat_pred = fc["national"]["predicted_yield_t_ha"]
            page.text(x_pred, y, f"{hu(rec['predicted_yield_t_ha'])} t/ha",
                      fontsize=FS, fontweight="bold", color=INK, va="top", ha="right")
            a = rec["anomaly_pct"]
            a_col = RED if a < -0.05 else GREEN if a > 0.05 else INK
            page.text(x_anom, y, f"{signed(a, 1)}%", fontsize=FS, color=a_col,
                      va="top", ha="right")
            dv = rec["predicted_yield_t_ha"] - nat_pred
            vs = "≈ orsz." if abs(dv) < 0.05 else f"{signed(dv, 2)} vs orsz."
            vs_col = MUTED if abs(dv) < 0.05 else (GREEN if dv > 0 else RED)
            page.text(x_vs, y, vs, fontsize=FS, color=vs_col, va="top", ha="right")
            # sáv-grafika: low–high téglalap + becslés-pont + országos vonás
            lo_s, hi_s = scales[fc["crop"]]
            def X(v):
                return bar_x0 + (v - lo_s) / (hi_s - lo_s) * (bar_x1 - bar_x0)
            bar_y = y - 0.012
            page.add_patch(Rectangle((X(rec["low"]), bar_y - 0.004),
                                     X(rec["high"]) - X(rec["low"]), 0.008,
                                     facecolor=BAND, alpha=0.5, edgecolor="none",
                                     transform=page.transAxes, zorder=2))
            page.plot([X(nat_pred), X(nat_pred)], [bar_y - 0.007, bar_y + 0.007],
                      color=MUTED, lw=1.8, transform=page.transAxes, zorder=3)
            page.scatter([X(rec["predicted_yield_t_ha"])], [bar_y], s=32,
                         color=INK, transform=page.transAxes, zorder=4)
            y -= line_h
        y -= 0.004
    page.text(M, y, "sáv: várható tartomány (80%), terményenként közös skálán · "
              "pont: becslés · vonás: országos", fontsize=FS, color=LIGHT, va="top")
    return y - line_h


# ---------------------------------------------------------------------------- #
# 1. oldal — vezetői összefoglaló
# ---------------------------------------------------------------------------- #
def draw_summary_page(pdf: PdfPages, fcs: dict[str, dict], deltas: dict,
                      total_pages: int, gdf=None) -> None:
    fig = plt.figure(figsize=(8.27, 11.69))
    page = fig.add_axes([0, 0, 1, 1]); page.set_axis_off()
    page.set_xlim(0, 1); page.set_ylim(0, 1)

    any_fc = next(iter(fcs.values()))
    # fejléc
    page.text(M, 0.972, "Napi vezetői jelentés",
              fontsize=FS_TITLE, fontweight="bold", color=INK, va="top")
    page.text(1 - M, 0.972, any_fc["updated_at"], fontsize=FS_MID, color=MUTED,
              ha="right", va="top")
    page.text(M, 0.944, f"{any_fc['crop_year']}-es termésév · búza, kukorica, "
              "őszi árpa · vármegyei statisztikai modell",
              fontsize=FS, color=MUTED, va="top")
    page.plot([M, 1 - M], [0.930, 0.930], color=BORDER, lw=1, transform=page.transAxes)

    # NAPI VÁLTOZÁS SÁV
    band_top, band_h = 0.920, 0.078
    card(page, M, band_top - band_h, 1 - 2 * M, band_h)
    d0 = next((d for d in deltas.values() if d), None)
    rng = (f" ({d0['prev_date']} → {d0['cur_date']})" if d0 else "")
    page.text(M + 0.012, band_top - 0.010, f"VÁLTOZÁS TEGNAP ÓTA{rng}",
              fontsize=FS, fontweight="bold", color=LIGHT, va="top")
    all_flat = all(d is None or abs(d["d_tha"]) < 0.005 for d in deltas.values())
    if all_flat:
        page.text(1 - M - 0.012, band_top - 0.010,
                  "Nincs döntést igénylő változás tegnap óta.",
                  fontsize=FS, color=GREEN, va="top", ha="right")
    cell_w = (1 - 2 * M - 0.024) / 3
    for i, (crop, fc) in enumerate(fcs.items()):
        cx = M + 0.012 + i * cell_w
        page.text(cx, band_top - 0.032, fc["crop"], fontsize=FS, color=MUTED, va="top")
        dtxt, dcol = delta_band_str(deltas[crop])
        page.text(cx, band_top - 0.052, dtxt, fontsize=FS,
                  fontweight="bold" if dcol != MUTED else "normal",
                  color=dcol, va="top")

    # három termény-oszlop
    col_w = (1 - 2 * M - 2 * 0.016) / 3
    for i, (crop, fc) in enumerate(fcs.items()):
        crop_column(fig, page, M + i * (col_w + 0.016), col_w, fc, deltas[crop])

    # térkép-sor: terményenként egy mini-choropleth, közös skálával
    page.text(M, 0.496, "TERÜLETI KÉP — eltérés a szokásostól (%)",
              fontsize=13, fontweight="bold", color=LIGHT, va="top")
    if gdf is not None:
        for i, (crop, fc) in enumerate(fcs.items()):
            mx = M + i * (col_w + 0.016)
            draw_anom_map(fig, [mx + 0.01, 0.362, col_w - 0.02, 0.112], fc, gdf)
            page.text(mx + col_w / 2, 0.360, fc["crop"], fontsize=FS,
                      color=MUTED, va="top", ha="center")
        draw_anom_colorbar(fig, [M + 0.02, 0.339, 0.30, 0.009])
        page.text(M + 0.36, 0.348, "szürke: nincs becslés · vastag keret: "
                  "fókusz-vármegye", fontsize=FS, color=LIGHT, va="top")

    # fókusz-vármegye tábla
    draw_focus_table(page, 0.316, fcs)

    # láblécsor
    page.text(M, 0.010, f"1/{total_pages} · Terméshozam-előrejelző · generálva: "
              f"{datetime.now().strftime('%Y-%m-%d %H:%M')} · Módszertan: utolsó oldal.",
              fontsize=FS, color=LIGHT, va="bottom")
    pdf.savefig(fig)
    plt.close(fig)


# ---------------------------------------------------------------------------- #
# 2+. oldal — "Ami még él" (futó szezonú termény)
# ---------------------------------------------------------------------------- #
def draw_live_page(pdf: PdfPages, fc: dict, page_no: int, total_pages: int,
                   is_last: bool, gdf=None) -> None:
    crop = fc_crop_key(fc)
    n = fc["national"]
    v = n.get("value")
    sc = fc["scenarios"]
    main, badge, cert = headline_text(fc)

    fig = plt.figure(figsize=(8.27, 11.69))
    page = fig.add_axes([0, 0, 1, 1]); page.set_axis_off()
    page.set_xlim(0, 1); page.set_ylim(0, 1)

    # fejléc
    page.text(M, 0.972, f"Ami még él — {fc['crop']}", fontsize=FS_TITLE,
              fontweight="bold", color=INK, va="top")
    page.text(1 - M, 0.972, fc["updated_at"], fontsize=FS_MID, color=MUTED,
              ha="right", va="top")
    page.text(M, 0.944, f"{sc['remaining_days']} nap a szezon végéig — "
              "a becslés még változhat", fontsize=FS, color=MUTED, va="top")
    pill(page, 1 - M, 0.978, "MÉG VÁLTOZHAT", live=True, ha="right")
    page.plot([M, 1 - M], [0.928, 0.928], color=BORDER, lw=1, transform=page.transAxes)

    # teljes headline
    head_lines = textwrap.wrap(main, 62)
    page.text(M, 0.918, "\n".join(head_lines), fontsize=14.5, color=INK,
              va="top", linespacing=1.4, fontweight="bold")
    y = 0.918 - len(head_lines) * 0.0205 - 0.008
    cert_lines = textwrap.wrap(cert, 82)
    page.text(M, y, "\n".join(cert_lines), fontsize=FS, color=MUTED,
              va="top", linespacing=1.35)
    y -= len(cert_lines) * 0.0165 + 0.018

    # forgatókönyv-blokk: sáv balra, tonna/forint tábla jobbra
    from_scenarios = sc["national"]
    area = v["area_ha"] if v else None
    price = v["price_huf_per_t"] if v else None
    band_rect = [M, y - 0.115, 0.40, 0.085]
    ax = fig.add_axes(band_rect)
    p10, p50, p90 = from_scenarios["p10"], from_scenarios["p50"], from_scenarios["p90"]
    point = n["predicted_yield_t_ha"]
    lo, hi = min(p10, point), max(p90, point)
    pad = (hi - lo) * 0.2 or 0.5
    ax.barh(0, p90 - p10, left=p10, height=0.34, color=BAND, alpha=0.5, zorder=2)
    ax.plot([p50, p50], [-0.3, 0.3], color=BLUE, lw=2.5, zorder=3)
    ax.scatter([point], [0], marker="v", s=130, color=RED, zorder=4)
    ax.annotate(f"kedvezőtlen\n{hu(p10)}", (p10, 0), xytext=(0, -32),
                textcoords="offset points", ha="center", fontsize=FS, color=MUTED)
    ax.annotate(f"kedvező\n{hu(p90)}", (p90, 0), xytext=(0, -32),
                textcoords="offset points", ha="center", fontsize=FS, color=MUTED)
    ax.annotate(f"becslés {hu(point)}", (point, 0), xytext=(0, 14),
                textcoords="offset points", ha="center", fontsize=FS,
                color=RED, fontweight="bold")
    ax.set_xlim(lo - pad, hi + pad); ax.set_ylim(-1.3, 0.95)
    ax.set_axis_off()
    ax.set_title("Forgatókönyvek (t/ha)", fontsize=13, color=INK, pad=2, loc="left")

    # tonna/forint tábla
    tx = 0.52
    page.text(tx, y, "Terményben és forintban*", fontsize=13,
              fontweight="bold", color=INK, va="top")
    ty = y - 0.024
    cols_x = [tx, tx + 0.20, tx + 0.31, tx + 0.42]
    for cx_, htxt in zip(cols_x, ["", "t/ha", "M tonna", "mrd Ft*"]):
        if htxt:
            page.text(cx_, ty, htxt, fontsize=FS, color=LIGHT, va="top", ha="right")
    ty -= 0.019
    rows = [("Kedvezőtlen", p10, False),
            ("Várható", p50, True),
            ("Kedvező", p90, False)]
    for label, p, bold in rows:
        weight = "bold" if bold else "normal"
        page.text(tx, ty, label, fontsize=FS, color=INK, va="top", fontweight=weight)
        page.text(cols_x[1], ty, hu(p), fontsize=FS, color=INK, va="top",
                  ha="right", fontweight=weight)
        if area and price:
            tons = p * area
            page.text(cols_x[2], ty, hu(tons / 1e6), fontsize=FS,
                      color=INK, va="top", ha="right", fontweight=weight)
            page.text(cols_x[3], ty, f"{tons * price / 1e9:.0f}", fontsize=FS,
                      color=INK, va="top", ha="right", fontweight=weight)
        ty -= 0.019
    y = min(y - 0.150, ty - 0.010)
    if area and price:
        risk = (p90 - p10) * area * price / 1e9
        page.text(M, y, f"A kedvezőtlen és a kedvező kimenet közti különbség "
                  f"kb. {risk:.0f} mrd Ft.", fontsize=FS,
                  fontweight="bold", color=INK, va="top")
        y -= 0.022
    if area and price:
        note = (f"*{v['price_year']}-es hivatalos termelői átlagáron "
                f"({price:,.0f} Ft/t".replace(",", " ") +
                f"), a legutóbbi lezárt évi vetésterülettel ({area / 1e3:.0f} ezer ha) "
                "— indikatív, nem piaci árajánlat.")
        page.text(M, y, textwrap.fill(note, 92), fontsize=FS, color=LIGHT,
                  va="top", style="italic")
        y -= 0.030

    # trend-részlet: napi becslések P10–P90 sávval
    hs = history_series(crop, max_days=21)
    if len(hs) >= 2:
        ax2 = fig.add_axes([M + 0.02, y - 0.155, 1 - 2 * M - 0.04, 0.125])
        xs = range(len(hs))
        preds = [h["pred"] for h in hs]
        if all(h["p10"] is not None for h in hs):
            ax2.fill_between(xs, [h["p10"] for h in hs], [h["p90"] for h in hs],
                             color=BAND, alpha=0.25, zorder=1)
        ax2.plot(xs, preds, color=BLUE, lw=1.8, zorder=3)
        ax2.scatter(xs, preds, s=26, color=BLUE, zorder=4)
        ax2.annotate(hu(preds[-1]), (len(hs) - 1, preds[-1]), xytext=(0, 8),
                     textcoords="offset points", ha="center", fontsize=FS,
                     color=INK, fontweight="bold")
        step = max(1, len(hs) // 6)
        ax2.set_xticks(list(xs)[::step])
        ax2.set_xticklabels([hs[i]["date"][5:] for i in list(xs)[::step]], fontsize=FS)
        ax2.tick_params(axis="y", labelsize=FS, colors=MUTED)
        ax2.spines[["top", "right"]].set_visible(False)
        ax2.set_title("A becslés napi alakulása (P10–P90 sávval, t/ha)",
                      fontsize=13, color=INK, loc="left", pad=4)
        y -= 0.185

    # fókusz-vármegyék kilátása + időjárása
    page.text(M, y, "FÓKUSZ-VÁRMEGYÉK — kilátás és időjárás", fontsize=13,
              fontweight="bold", color=LIGHT, va="top")
    y -= 0.022
    heads = ["", "Becslés", "P10–P90", "Hőstressz", "Vízmérleg", "Csapadék"]
    hx = [M, M + 0.26, M + 0.47, M + 0.61, M + 0.755, M + 0.88]
    for cx_, htxt in zip(hx[1:], heads[1:]):
        page.text(cx_, y, htxt, fontsize=FS, color=LIGHT, va="top", ha="right")
    y -= 0.019
    sc_counties = sc.get("counties") or {}
    for name in config.REPORT_FOCUS_COUNTIES:
        rec = next((c for c in fc["counties"] if c["county_name"] == name), None)
        if rec is None:
            continue
        page.text(M, y, name, fontsize=FS, color=INK, va="top")
        if rec["predicted_yield_t_ha"] is None:
            page.text(hx[1], y, "nincs becslés", fontsize=FS, color=MUTED,
                      va="top", ha="right")
            y -= 0.019
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
        y -= 0.019
    page.text(M, y - 0.002, textwrap.fill(
        "A vízmérleg = csapadék − párolgás a szezon eddigi részében; "
        "minél negatívabb, annál nagyobb az aszálynyomás.", 82),
        fontsize=FS, color=MUTED, va="top", linespacing=1.3)
    y -= 0.052

    # területi kép: kompakt anomália-térkép a lábléc fölé
    if gdf is not None:
        page.text(M, y, "TERÜLETI KÉP — eltérés a szokásostól (%)",
                  fontsize=13, fontweight="bold", color=LIGHT, va="top")
        map_h = max(0.10, y - 0.155)          # a lábléc (0.118) fölött marad
        draw_anom_map(fig, [M, y - 0.022 - map_h, 0.36, map_h], fc, gdf)
        draw_anom_colorbar(fig, [0.50, y - 0.055, 0.30, 0.009])
        page.text(0.50, y - 0.075, textwrap.fill(
            "Vastag keret: fókusz-vármegyék. Szürke: nincs becslés (Budapest). "
            "A színskála a weboldal térképével azonos.", 44),
            fontsize=FS, color=MUTED, va="top", linespacing=1.35)

    # módszertani lábléc — csak a legutolsó oldalon
    if is_last:
        page.plot([M, 1 - M], [0.118, 0.118], color=BORDER, lw=1,
                  transform=page.transAxes)
        foot = (
            "Módszertan: statisztikai modell (KSH vármegyei hozamok 2000-től + ERA5 "
            "időjárás); tipikus tévedés terményenként ±9–20%. Nem hivatalos adat. "
            "Részletek: prettyasap.github.io/wheat-forecast/magyarazat.html · "
            "Adatok: KSH, Open-Meteo/ERA5 (CC BY 4.0), Eurostat."
        )
        page.text(M, 0.106, textwrap.fill(foot, 82), fontsize=FS, color=LIGHT,
                  va="top", linespacing=1.35)
    page.text(M, 0.008, f"{page_no}/{total_pages} · Terméshozam-előrejelző · "
              f"generálva: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
              fontsize=FS, color=LIGHT, va="bottom")
    pdf.savefig(fig)
    plt.close(fig)


def main() -> None:
    today = date.today().isoformat()
    JELENTES_DIR.mkdir(parents=True, exist_ok=True)

    fcs = {crop: load_fc(crop) for crop in config.CROPS}
    gdf = gpd.read_file(config.WEB_DATA / "nuts3_hu.geojson")
    deltas = {crop: daily_delta(crop) for crop in config.CROPS}
    live_crops = [c for c, fc in fcs.items() if fc.get("scenarios")]
    total_pages = 1 + len(live_crops)

    out = JELENTES_DIR / f"jelentes_{today}.pdf"
    with PdfPages(out) as pdf:
        draw_summary_page(pdf, fcs, deltas, total_pages, gdf)
        for i, crop in enumerate(live_crops):
            draw_live_page(pdf, fcs[crop], 2 + i, total_pages,
                           is_last=(i == len(live_crops) - 1), gdf=gdf)
        info = pdf.infodict()
        info["Title"] = f"Napi vezetői jelentés — {today}"
        info["Author"] = "Terméshozam-előrejelző (statisztikai modell)"
    latest = config.WEB_DATA / "jelentes_latest.pdf"
    latest.write_bytes(out.read_bytes())
    print(f"[ok] {out} ({out.stat().st_size // 1024} KB, {total_pages} oldal) "
          f"+ jelentes_latest.pdf")


if __name__ == "__main__":
    main()
