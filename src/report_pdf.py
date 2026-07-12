"""Napi vezetői PDF-jelentés (terminál-bővítés: PDF-kör).

Terményenként egy A4-es oldal, az infografikai szerkesztő által megadott
szerkezetben: headline-mondat + bizonyosság-jelvény → 4 kulcsszám → anomália-
térkép + történelmi pöttysor (+ forgatókönyv-szalag szezon közben) → top/bottom
vármegyék → időjárás-driver mondat → napi változás → lábjegyzet.

Elvek:
  - minden szöveg >= 12 pt (a felhasználó kifejezett kérése),
  - minden elem fix rácspozíción (figure-koordináták), nincs átfedés,
  - ugyanazok a számok és mondatok, mint a weben (a headline-logika a
    web/app.js renderHeadline portja; a szövegek a fogalomtárral konzisztensek).

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
from matplotlib.patches import FancyBboxPatch

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
BORDER = "#dfe4e8"
CARD_BG = "#fbfcfd"
NO_DATA = "#d5d8dc"
ANOM_COLORS = ["#b03a2e", "#e67e22", "#f5e8c8", "#7fb3d5", "#2874a6"]

FS = 12          # minimum betűméret (kérés: semmi nem lehet kisebb)
FS_KPI = 19      # kulcsszámok
FS_HEAD = 14.5   # headline
FS_TITLE = 21    # oldalcím

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": FS,
    "text.color": INK,
    "axes.edgecolor": BORDER,
})

JELENTES_DIR = config.WEB_DATA / "jelentes"


def hu(v: float, d: int = 2) -> str:
    return f"{v:.{d}f}".replace(".", ",")


# ---------------------------------------------------------------------------- #
# Headline — a web/app.js renderHeadline() portja (ugyanaz az üzenet)
# ---------------------------------------------------------------------------- #
def headline_text(fc: dict) -> tuple[str, str, str]:
    """(főmondat, bizonyosság-jelvény szövege, bizonyosság-mondat)"""
    n = fc["national"]
    a = n["anomaly_pct"]
    v = n.get("value")
    sc = fc.get("scenarios")
    crop = fc["crop"]

    if a <= -3:
        main = (f"A {crop} idei termése {hu(n['predicted_yield_t_ha'])} t/ha körül "
                f"várható, {hu(abs(a), 1)}%-kal a sokéves szokásos alatt")
        if v:
            main += (f" — ez a {v['price_year']}-es árakon kb. "
                     f"{abs(v['trend_gap_bn_huf']):.0f} mrd Ft kiesés.")
        else:
            main += "."
    elif a >= 3:
        main = (f"A {crop} termése {hu(a, 1)}%-kal a szokásos felett várható "
                f"({hu(n['predicted_yield_t_ha'])} t/ha)")
        if v:
            main += (f" — kb. {v['trend_gap_bn_huf']:.0f} mrd Ft többlet "
                     f"a {v['price_year']}-es árakon.")
        else:
            main += "."
    else:
        main = (f"A {crop} termése a szokásos szint közelében alakul "
                f"({hu(n['predicted_yield_t_ha'])} t/ha, {a:+.1f}%) — érdemi "
                "kiesés vagy többlet egyelőre nem látszik.").replace(".", ",", 0)

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
        badge = "MÉG VÁLTOZHAT"
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


def weather_driver_text(fc: dict) -> str:
    """Országos időjárás-összefoglaló mondat a vármegyei adatokból."""
    est = [c for c in fc["counties"] if c["predicted_yield_t_ha"] is not None]
    wbs = [(c["weather_todate"]["wb_total_mm"], c["county_name"]) for c in est]
    precs = [c["weather_todate"]["prec_total_mm"] for c in est]
    heats = [(c["weather_todate"]["heat_days"], c["county_name"]) for c in est]
    wb_min, wb_min_c = min(wbs)
    wb_max, _ = max(wbs)
    heat_max, heat_max_c = max(heats)
    txt = (f"Vízmérleg (csapadék − párolgás) a szezon eddigi részében: "
           f"{hu(wb_min, 0)} … {hu(wb_max, 0)} mm vármegyénként — a legszárazabb "
           f"{wb_min_c}. Csapadék: {hu(min(precs), 0)}–{hu(max(precs), 0)} mm.")
    if heat_max > 0:
        txt += f" A legtöbb hőstressznap: {heat_max_c} ({heat_max} nap)."
    return txt


def delta_text(crop: str, fc: dict) -> str:
    """Napi változás az előző snapshothoz képest (history/)."""
    hdir = config.WEB_DATA / "history" / crop
    dates = sorted(p.stem for p in hdir.glob("????-??-??.json"))
    if len(dates) < 2:
        return "Változás tegnaphoz képest: első jelentési nap."
    prev = json.loads((hdir / f"{dates[-2]}.json").read_text(encoding="utf-8"))
    prev_nat = prev.get("national", {}).get("predicted_yield_t_ha")
    if prev_nat is None:
        return "Változás tegnaphoz képest: nem összevethető (formátumváltás)."
    d = fc["national"]["predicted_yield_t_ha"] - prev_nat
    if abs(d) < 0.005:
        return f"Változás tegnaphoz ({dates[-2]}) képest: nincs érdemi változás."
    return (f"Változás tegnaphoz ({dates[-2]}) képest: "
            f"{hu(d, 2) if d < 0 else '+' + hu(d, 2)} t/ha "
            f"({'javulás' if d > 0 else 'romlás'}).")


# ---------------------------------------------------------------------------- #
# Rajzoló segédek
# ---------------------------------------------------------------------------- #
def card(page_ax, x, y, w, h):
    page_ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.008",
        facecolor=CARD_BG, edgecolor=BORDER, linewidth=0.8,
        transform=page_ax.transAxes, zorder=1))


KPI_H = 0.086

def kpi(page_ax, x, y, w, label, value, sub, sub_color=MUTED):
    card(page_ax, x, y, w, KPI_H)
    page_ax.text(x + 0.012, y + KPI_H - 0.012, label.upper(), fontsize=FS,
                 color=LIGHT, va="top", transform=page_ax.transAxes)
    page_ax.text(x + 0.012, y + KPI_H / 2 - 0.004, value, fontsize=FS_KPI,
                 color=INK, fontweight="bold", va="center",
                 transform=page_ax.transAxes)
    page_ax.text(x + 0.012, y + 0.013, sub, fontsize=FS, color=sub_color,
                 va="center", transform=page_ax.transAxes)


def draw_map(fig, rect, fc, gdf):
    ax = fig.add_axes(rect)
    anoms = {c["nuts_id"]: (c["anomaly_pct"] if c["predicted_yield_t_ha"] is not None
                            else None) for c in fc["counties"]}
    gdf = gdf.copy()
    gdf["anom"] = gdf["NUTS_ID"].map(anoms)
    cmap = LinearSegmentedColormap.from_list("anom", ANOM_COLORS)
    norm = Normalize(vmin=-20, vmax=20)
    gdf[gdf["anom"].isna()].plot(ax=ax, color=NO_DATA, edgecolor="#7f8c8d", linewidth=0.5)
    gdf[gdf["anom"].notna()].plot(ax=ax, column="anom", cmap=cmap, norm=norm,
                                  edgecolor="#7f8c8d", linewidth=0.5)
    ax.set_axis_off()
    ax.set_title("Eltérés a szokásos hozamtól, vármegyénként",
                 fontsize=FS + 1, color=INK, pad=6)
    # kompakt színskála a térkép alá
    cax = fig.add_axes([rect[0] + rect[2] * 0.18, rect[1] - 0.012,
                        rect[2] * 0.64, 0.010])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap),
                      cax=cax, orientation="horizontal")
    cb.set_ticks([-20, -10, 0, 10, 20])
    cb.ax.set_xticklabels(["−20%", "−10%", "0", "+10%", "+20%"], fontsize=FS)
    cb.outline.set_edgecolor(BORDER)


def draw_rank_strip(fig, rect, fc, hist):
    """Történelmi pöttysor: minden év trend-anomáliája + az idei kiemelve."""
    ax = fig.add_axes(rect)
    nat = hist["national"]
    anoms, years = [], []
    for y, v in zip(nat["years"], nat["yields"]):
        tr = nat["trend_intercept"] + nat["trend_slope"] * (y - nat["trend_base_year"])
        anoms.append(100 * (v - tr) / tr)
        years.append(y)
    cur = fc["national"]["anomaly_pct"]
    ax.axvline(0, color="#b8c2ca", ls="--", lw=1)
    ax.scatter(anoms, np.zeros(len(anoms)), s=55, color="#aab8c2", alpha=0.8, zorder=2)
    cur_col = RED if cur < 0 else GREEN
    ax.scatter([cur], [0], s=150, color=cur_col, edgecolor="white",
               linewidth=1.5, zorder=3)
    # szélső évek felirata (a szerkesztői kérés: a szélsők ne legyenek névtelenek)
    i_min, i_max = int(np.argmin(anoms)), int(np.argmax(anoms))
    ax.annotate(str(years[i_min]), (anoms[i_min], 0), xytext=(0, -16),
                textcoords="offset points", ha="center", fontsize=FS, color=LIGHT)
    ax.annotate(str(years[i_max]), (anoms[i_max], 0), xytext=(0, -16),
                textcoords="offset points", ha="center", fontsize=FS, color=LIGHT)
    ax.annotate(str(fc["crop_year"]), (cur, 0), xytext=(0, 11),
                textcoords="offset points", ha="center", fontsize=FS,
                color=cur_col, fontweight="bold")
    ax.set_ylim(-1, 1)
    lo, hi = min(anoms + [cur]), max(anoms + [cur])
    pad = (hi - lo) * 0.08
    ax.set_xlim(lo - pad, hi + pad)
    ax.set_yticks([])
    ax.spines[["left", "top", "right"]].set_visible(False)
    ax.spines["bottom"].set_color(BORDER)
    ax.tick_params(axis="x", labelsize=FS, colors=MUTED)
    ax.set_title(f"Hol áll {fc['crop_year']} az évek közt? (%)",
                 fontsize=FS + 1, color=INK, pad=4, loc="left")


def draw_scenario_band(fig, rect, fc):
    sc = fc["scenarios"]["national"]
    point = fc["national"]["predicted_yield_t_ha"]
    ax = fig.add_axes(rect)
    lo = min(sc["p10"], point); hi = max(sc["p90"], point)
    pad = (hi - lo) * 0.2 or 0.5
    ax.barh(0, sc["p90"] - sc["p10"], left=sc["p10"], height=0.34,
            color="#7fb3d5", alpha=0.5, zorder=2)
    ax.plot([sc["p50"], sc["p50"]], [-0.3, 0.3], color=BLUE, lw=2.5, zorder=3)
    ax.scatter([point], [0], marker="v", s=130, color=RED, zorder=4)
    ax.annotate(f"kedvezőtlen\n{hu(sc['p10'])}", (sc["p10"], 0), xytext=(0, -30),
                textcoords="offset points", ha="center", fontsize=FS, color=MUTED)
    ax.annotate(f"kedvező\n{hu(sc['p90'])}", (sc["p90"], 0), xytext=(0, -30),
                textcoords="offset points", ha="center", fontsize=FS, color=MUTED)
    ax.annotate(f"becslés {hu(point)}", (point, 0), xytext=(0, 13),
                textcoords="offset points", ha="center", fontsize=FS,
                color=RED, fontweight="bold")
    ax.set_xlim(lo - pad, hi + pad)
    ax.set_ylim(-1.15, 0.9)
    ax.set_axis_off()
    ax.set_title(f"Mi lehet még belőle? ({fc['scenarios']['remaining_days']} nap hátra, t/ha)",
                 fontsize=FS + 1, color=INK, pad=2)


def draw_county_table(page_ax, x, y, w, fc):
    """Top 3 / alsó 3 vármegye — kézzel pozicionált sorok (12 pt)."""
    est = sorted([c for c in fc["counties"] if c["predicted_yield_t_ha"] is not None],
                 key=lambda c: c["anomaly_pct"])
    rows = [("A legnagyobb elmaradás", est[:3]),
            ("A legjobban tartó vármegyék", est[-3:][::-1])]
    short = {"Szabolcs-Szatmár-Bereg": "Szabolcs-Szat.-B.",
             "Borsod-Abaúj-Zemplén": "Borsod-Abaúj-Z.",
             "Jász-Nagykun-Szolnok": "Jász-Nagykun-Sz.",
             "Győr-Moson-Sopron": "Győr-Moson-S."}
    cols = [(0.0, ""), (0.40, ""), (0.63, ""), (0.81, "")]
    line_h = 0.0195
    yy = y
    for title, group in rows:
        page_ax.text(x, yy, title, fontsize=FS, color=LIGHT, fontweight="bold",
                     transform=page_ax.transAxes, va="top")
        yy -= line_h
        for c in group:
            gap = c.get("trend_gap_bn_huf")
            if gap is not None and abs(gap) < 0.05:
                gap = 0.0  # ne írjunk "-0,0"-t
            vals = [short.get(c["county_name"], c["county_name"]),
                    f"{hu(c['predicted_yield_t_ha'])} t/ha",
                    f"{'+' if c['anomaly_pct'] > 0 else ''}{hu(c['anomaly_pct'], 1)}%",
                    (f"{'+' if gap > 0 else ''}{hu(gap, 1)} mrd Ft" if gap is not None else "–")]
            for (cx, _), val in zip(cols, vals):
                color = INK
                if val.endswith("%") or "mrd" in val:
                    # előjel szerint: negatív piros, pozitív zöld, nulla semleges
                    color = (RED if val.startswith("-")
                             else GREEN if val.startswith("+") else INK)
                page_ax.text(x + cx * w, yy, val, fontsize=FS, color=color,
                             transform=page_ax.transAxes, va="top")
            yy -= line_h
        yy -= 0.006
    return yy


# ---------------------------------------------------------------------------- #
# Egy termény = egy oldal
# ---------------------------------------------------------------------------- #
def draw_page(pdf: PdfPages, crop: str, gdf, page_no: int, total: int) -> None:
    fc = json.loads((config.WEB_DATA / f"forecast_{crop}.json").read_text(encoding="utf-8"))
    hist = json.loads((config.WEB_DATA / f"yield_history_{crop}.json").read_text(encoding="utf-8"))
    n = fc["national"]
    v = n.get("value")
    main, badge, cert = headline_text(fc)

    fig = plt.figure(figsize=(8.27, 11.69))  # A4 álló
    page = fig.add_axes([0, 0, 1, 1]); page.set_axis_off()
    page.set_xlim(0, 1); page.set_ylim(0, 1)

    M = 0.06  # oldalmargó

    # --- fejléc ---
    page.text(M, 0.965, f"Napi terményjelentés — {fc['crop']}",
              fontsize=FS_TITLE, fontweight="bold", color=INK, va="top")
    page.text(1 - M, 0.965, f"{fc['updated_at']}", fontsize=FS + 2, color=MUTED,
              ha="right", va="top")
    page.text(M, 0.936, f"{fc['crop_year']}-es termésév · Magyarország, vármegyei bontás",
              fontsize=FS, color=MUTED, va="top")
    # bizonyosság-jelvény
    badge_col = "#9a6a12" if fc.get("scenarios") else GREEN
    badge_bg = "#fdf3e0" if fc.get("scenarios") else "#e8f6ee"
    page.text(1 - M, 0.936, f" {badge} ", fontsize=FS, color=badge_col,
              ha="right", va="top", fontweight="bold",
              bbox=dict(boxstyle="round,pad=0.35", fc=badge_bg, ec=badge_col, lw=0.8))
    page.plot([M, 1 - M], [0.922, 0.922], color=BORDER, lw=1, transform=page.transAxes)

    # --- headline (dinamikus magasság: a sorok száma tolja lejjebb a többit) ---
    head_lines = textwrap.wrap(main, 62)
    page.text(M, 0.912, "\n".join(head_lines), fontsize=FS_HEAD, color=INK,
              va="top", linespacing=1.4, fontweight="bold")
    y_cursor = 0.912 - len(head_lines) * 0.0205 - 0.008
    cert_lines = textwrap.wrap(cert, 82)
    page.text(M, y_cursor, "\n".join(cert_lines), fontsize=FS, color=MUTED,
              va="top", linespacing=1.35)
    y_cursor -= len(cert_lines) * 0.0165 + 0.012

    # --- 4 KPI kártya ---
    kw = (1 - 2 * M - 3 * 0.016) / 4
    ky = y_cursor - KPI_H
    neg = n["anomaly_pct"] < 0
    kpi(page, M, ky, kw, "Becslés", f"{hu(n['predicted_yield_t_ha'])} t/ha",
        f"tavaly: {hu(n['prev_year_yield_t_ha'])}")
    kpi(page, M + (kw + 0.016), ky, kw, "A szokásoshoz",
        f"{'+' if not neg else ''}{hu(n['anomaly_pct'], 1)}%",
        f"vs {n['prev_year']}: {'+' if n['yoy_pct'] > 0 else ''}{hu(n['yoy_pct'], 1)}%",
        RED if neg else GREEN)
    kpi(page, M + 2 * (kw + 0.016), ky, kw, "Helyezés",
        f"{n['rank_from_worst']}.",
        f"leggyengébb · {n['rank_total']} év")
    if v:
        gap = v["trend_gap_bn_huf"]
        kpi(page, M + 3 * (kw + 0.016), ky, kw, "Termelési érték",
            f"{v['production_value_bn_huf']:.0f} mrd Ft",
            f"vs szokásos: {'+' if gap > 0 else ''}{gap:.0f} mrd",
            RED if gap < 0 else GREEN)

    # --- térkép (bal) + pöttysor/szcenárió (jobb) — a KPI-sor aljához horgonyozva ---
    map_top = ky - 0.022          # a kártyasor alja alatt
    draw_map(fig, [M - 0.01, map_top - 0.295, 0.52, 0.27], fc, gdf)
    draw_rank_strip(fig, [0.60, map_top - 0.115, 0.34, 0.075], fc, hist)
    if fc.get("scenarios"):
        draw_scenario_band(fig, [0.60, map_top - 0.265, 0.34, 0.075], fc)
    else:
        page.text(0.60, map_top - 0.19, textwrap.fill(
            "Nincs időjárás-forgatókönyv: a szezon lezárult, az eredményt már "
            "csak a hivatalos KSH-mérés pontosítja.", 40),
            fontsize=FS, color=MUTED, va="top", linespacing=1.4)

    # --- vármegyei szélsők táblázata ---
    table_top = map_top - 0.355
    ty = draw_county_table(page, M, table_top, 0.46, fc)

    # --- driver + delta a táblázat mellett jobbra (dinamikus folyás) ---
    rx, ry = 0.58, table_top
    page.text(rx, ry, "Mi mozgatja?", fontsize=FS, color=LIGHT,
              fontweight="bold", va="top")
    ry -= 0.0195
    drv_lines = textwrap.wrap(weather_driver_text(fc), 36)
    page.text(rx, ry, "\n".join(drv_lines), fontsize=FS, color=INK,
              va="top", linespacing=1.35)
    ry -= len(drv_lines) * 0.0165 + 0.014
    dlt_lines = textwrap.wrap(delta_text(crop, fc), 36)
    page.text(rx, ry, "\n".join(dlt_lines), fontsize=FS, color=MUTED,
              va="top", linespacing=1.35)

    # --- lábjegyzet ---
    page.plot([M, 1 - M], [0.132, 0.132], color=BORDER, lw=1, transform=page.transAxes)
    foot = (
        f"Módszertan: statisztikai modell a KSH 2000 óta mért vármegyei hozamaiból "
        f"és az ERA5 időjárási adatokból; tipikus tévedés ±{hu(n.get('model_error_pct', 0), 1)}%. "
        f"A „szokásos” a sokéves, technológiai fejlődéssel korrigált szint; "
        f"ár: legutolsó hivatalos ({v['price_year'] if v else '–'}). Nem hivatalos adat. "
        "Fogalomtár: prettyasap.github.io/wheat-forecast/magyarazat.html · "
        "Adatok: KSH, Open-Meteo/ERA5 (CC BY 4.0), Eurostat."
    )
    page.text(M, 0.122, textwrap.fill(foot, 82), fontsize=FS, color=LIGHT,
              va="top", linespacing=1.35)
    page.text(M, 0.010, f"{page_no}/{total} · Terméshozam-előrejelző · "
              f"generálva: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
              fontsize=FS, color=LIGHT, va="bottom")

    pdf.savefig(fig)
    plt.close(fig)


def main() -> None:
    today = date.today().isoformat()
    JELENTES_DIR.mkdir(parents=True, exist_ok=True)
    gdf = gpd.read_file(config.WEB_DATA / "nuts3_hu.geojson")

    out = JELENTES_DIR / f"jelentes_{today}.pdf"
    crops = list(config.CROPS)
    with PdfPages(out) as pdf:
        for i, crop in enumerate(crops, 1):
            draw_page(pdf, crop, gdf, i, len(crops))
        info = pdf.infodict()
        info["Title"] = f"Napi terményjelentés — {today}"
        info["Author"] = "Terméshozam-előrejelző (statisztikai modell)"
    latest = config.WEB_DATA / "jelentes_latest.pdf"
    latest.write_bytes(out.read_bytes())
    print(f"[ok] {out} ({out.stat().st_size // 1024} KB, {len(crops)} oldal) "
          f"+ jelentes_latest.pdf")


if __name__ == "__main__":
    main()
