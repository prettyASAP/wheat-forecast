"""Walk-forward (expanding-window) backtest — a testületi konszenzus mérési kapuja.

M1 (matematikus): minden t tesztévre (2011–2025) KIZÁRÓLAG a t előtti éveken
tanítunk; a hiperparamétereket (trendfok, ridge α) és a warm_nights felvételét
a tanítóablakon BELÜLI LOYO választja — semmi nem szivárog a jövőből.
A1+A3 (agrártudós): a v2 feature-készlet (plafonozott, termény-bázisú GDD + EDD)
a v1-gyel ugyanezen protokoll alatt versenyez; a csere csak akkor él, ha itt nyer.
Stressz-év riport (agrártudós kikötése): 2003/2007/2012/2022 as-of összevetés
v1 vs v2 — a walk-forward ablakból kimaradó szélsőévekre külön szemle.
M2: a győztes változat reziduumaiból empirikus q10/q90 sáv, vármegyénkénti
zsugorított szórással; lefedettség ÉV-szinten, binomiális CI-vel.

Kimenet:
  data/processed/walkforward_{crop}.parquet   (tesztévi reziduumok)
  data/processed/walkforward_summary_{crop}.json  (a sávhoz + a headline-hoz)
  reports/walkforward_report.md               (régi és új szám EGYÜTT — cégvezetői
                                               kikötés az átállás kommunikációjára)

Futtatás:  python -m src.walkforward
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from src import config
from src.model import fit_panel_model, load_model_data, predict_naive_trend
from src.validate import loyo_predict, rmse

WF_START = 2011          # első tesztév (11 tanítóév minimum)
GRID = [(d, a) for d in (1, 2) for a in (0.0, 5.0, 10.0, 25.0)]
SHRINK_K = 10            # vármegyei szórás-zsugorítás erőssége (k/(k+n_c))
STRESS_YEARS = [2003, 2007, 2012, 2022]


def v2_features(crop: str, warm: bool = False) -> list[str]:
    """A v2 készlet: gdd_* -> gddc_*, heat_days -> edd; opcionálisan warm_nights."""
    out = []
    for f in config.CROPS[crop]["model_features"]:
        if f.startswith("gdd_"):
            out.append("gddc_" + f[4:])
        elif f == "heat_days":
            out.append("edd")
        else:
            out.append(f)
    if warm and "warm_nights" not in out:
        out.append("warm_nights")
    return out


def inner_select(train: pd.DataFrame, crop: str,
                 feature_sets: dict[str, list[str]]) -> tuple[str, int, float]:
    """Beágyazott LOYO a tanítóablakon: (készlet, trendfok, alpha) kiválasztása."""
    best = (None, None, None, np.inf)
    for fname, feats in feature_sets.items():
        for deg, alpha in GRID:
            r = loyo_predict(train, crop=crop, features=feats,
                             trend_degree=deg, ridge_alpha=alpha)
            e = rmse(r["actual"], r["pred"])
            if e < best[3]:
                best = (fname, deg, alpha, e)
    return best[0], best[1], best[2]


def walk_forward(df: pd.DataFrame, crop: str,
                 feature_sets: dict[str, list[str]]) -> pd.DataFrame:
    """Expanding-window előrejelzés; készlet+hiperparaméter ablakonként újraválasztva."""
    rows = []
    for t in range(WF_START, int(df["crop_year"].max()) + 1):
        train = df[df["crop_year"] < t]
        test = df[df["crop_year"] == t]
        if test.empty:
            continue
        fname, deg, alpha = inner_select(train, crop, feature_sets)
        m = fit_panel_model(train, crop=crop, features=feature_sets[fname],
                            trend_degree=deg, ridge_alpha=alpha)
        pred = m.predict(test)
        naive = predict_naive_trend(train, test)
        for i, (_, r) in enumerate(test.iterrows()):
            rows.append({"crop_year": t, "nuts_id": r["nuts_id"],
                         "actual": r["yield_t_ha"], "pred": float(pred[i]),
                         "naive": float(naive[i]), "variant": fname,
                         "trend_degree": deg, "alpha": alpha})
    return pd.DataFrame(rows)


def national_wf_rmse(res: pd.DataFrame, df: pd.DataFrame) -> float:
    """Országos (terület-súlyozott) walk-forward RMSE.

    A vármegyei pooled RMSE-nél KISEBB: a vármegyei tévedések aggregáláskor
    részben kioltják egymást, ezért ez az ORSZÁGOS becslés (a fejléc-szám)
    tényleges tipikus hibája. Évente a vármegyei walk-forward becsléseket az
    ADOTT ÉVI betakarított területtel súlyozzuk országos hozammá, ugyanígy a
    tényt, majd az évek során RMSE-t számolunk (matematikai audit 5.1)."""
    area = df.set_index(["crop_year", "nuts_id"])["area_ha"]
    r = res.copy()
    r["area"] = [area.get((cy, nid), np.nan)
                 for cy, nid in zip(r["crop_year"], r["nuts_id"])]
    r = r.dropna(subset=["area"])
    nat = r.groupby("crop_year").apply(
        lambda g: pd.Series({
            "actual": np.average(g["actual"], weights=g["area"]),
            "pred": np.average(g["pred"], weights=g["area"]),
        }), include_groups=False)
    return rmse(nat["actual"].to_numpy(), nat["pred"].to_numpy())


def band_calibration(res: pd.DataFrame) -> dict:
    """M2: zsugorított vármegyei szórás + a standardizált pool empirikus q10/q90."""
    res = res.copy()
    res["resid"] = res["actual"] - res["pred"]
    sigma_pooled = float(res["resid"].std(ddof=1))
    sigma_c = {}
    for nid, g in res.groupby("nuts_id"):
        n_c = len(g)
        w = SHRINK_K / (SHRINK_K + n_c)
        sigma_c[nid] = float(np.sqrt(w * sigma_pooled ** 2
                                     + (1 - w) * g["resid"].var(ddof=1)))
    z = res.apply(lambda r: r["resid"] / sigma_c[r["nuts_id"]], axis=1)
    q10, q90 = float(np.quantile(z, 0.10)), float(np.quantile(z, 0.90))

    # év-szintű lefedettség: az adott évben a vármegyék hány %-a esett a sávba,
    # + hány évben volt a lefedettség >= 50% (durva év-siker) — binomiális CI-vel
    res["in_band"] = res.apply(
        lambda r: sigma_c[r["nuts_id"]] * q10 <= r["resid"]
        <= sigma_c[r["nuts_id"]] * q90, axis=1)
    by_year = res.groupby("crop_year")["in_band"].mean()
    n_years = len(by_year)
    overall = float(res["in_band"].mean())
    # binomiális 95% CI az ÉVEK szintjén (a megfigyelések éven belül korreláltak)
    year_hits = int((by_year >= 0.8).sum())
    from math import sqrt
    p_hat = year_hits / n_years
    ci = 1.96 * sqrt(max(p_hat * (1 - p_hat), 1e-9) / n_years)
    return {
        "sigma_pooled": sigma_pooled,
        "sigma_by_county": sigma_c,
        "q10": q10, "q90": q90,
        "coverage_overall": overall,
        "coverage_by_year": {int(y): float(v) for y, v in by_year.items()},
        "years_with_ge80_coverage": year_hits,
        "n_years": n_years,
        "year_coverage_ci95": [max(0.0, p_hat - ci), min(1.0, p_hat + ci)],
    }


def stress_year_report(df: pd.DataFrame, crop: str,
                       feature_sets: dict[str, list[str]]) -> list[dict]:
    """Szélsőévek (2003/2007/2012/2022) LOYO-alapú összevetése v1 vs v2 —
    az agrártudós kikötése: a walk-forward ablak ne rejtse el a stresszéveket."""
    out = []
    for year in STRESS_YEARS:
        test = df[df["crop_year"] == year]
        train = df[df["crop_year"] != year]
        if test.empty:
            continue
        row = {"year": year}
        for fname in ("v1", "v2"):
            m = fit_panel_model(train, crop=crop, features=feature_sets[fname])
            row[f"rmse_{fname}"] = rmse(test["yield_t_ha"].to_numpy(),
                                        m.predict(test))
        out.append(row)
    return out


def run_crop(crop: str) -> dict:
    label = config.CROPS[crop]["label"]
    df = load_model_data(crop)
    sets = {"v1": config.CROPS[crop]["model_features"],
            "v2": v2_features(crop),
            "v2w": v2_features(crop, warm=True)}

    print(f"\n=== {label} — walk-forward {WF_START}–{int(df['crop_year'].max())} ===")
    # HEADLINE-verseny: v1 önmagában vs v2-adaptív (belső döntés v2/v2w közt)
    res_v1 = walk_forward(df, crop, {"v1": sets["v1"]})
    res_v2 = walk_forward(df, crop, {"v2": sets["v2"], "v2w": sets["v2w"]})

    rmse_v1 = rmse(res_v1["actual"], res_v1["pred"])
    rmse_v2 = rmse(res_v2["actual"], res_v2["pred"])
    rmse_naive = rmse(res_v1["actual"], res_v1["naive"])
    warm_share = float((res_v2["variant"] == "v2w").mean())
    winner = "v2" if rmse_v2 <= rmse_v1 else "v1"
    res_best = res_v2 if winner == "v2" else res_v1

    print(f"  naiv trend:      {rmse_naive:.3f} t/ha")
    print(f"  v1 (régi):       {rmse_v1:.3f} t/ha")
    print(f"  v2 (agro, adaptív): {rmse_v2:.3f} t/ha  "
          f"(warm_nights az ablakok {100*warm_share:.0f}%-ában)")
    print(f"  GYŐZTES: {winner}")

    calib = band_calibration(res_best)
    stress = stress_year_report(df, crop, sets)

    res_best.to_parquet(config.DATA_PROCESSED / f"walkforward_{crop}.parquet",
                        index=False)
    # a LOYO-s (régi) szám a kettős kommunikációhoz
    loyo_old = json.loads(
        (config.DATA_PROCESSED / f"loyo_summary_{crop}.json").read_text())
    rmse_nat = national_wf_rmse(res_best, df)
    summary = {
        "winner": winner,
        "rmse_wf": rmse_v1 if winner == "v1" else rmse_v2,
        "rmse_wf_v1": rmse_v1, "rmse_wf_v2": rmse_v2,
        # az ORSZÁGOS fejléc-szám hibája (terület-súlyozva, aggregáláskor
        # a vármegyei tévedések részben kioltják egymást → kisebb, de HELYES)
        "rmse_wf_national": rmse_nat,
        "rmse_naive_wf": rmse_naive,
        "rmse_loyo_old": float(loyo_old["oos_std"]),
        "warm_nights_share": warm_share,
        "test_years": [WF_START, int(df["crop_year"].max())],
        "mean_yield": float(res_best["actual"].mean()),
        **calib,
        "stress_years": stress,
    }
    (config.DATA_PROCESSED / f"walkforward_summary_{crop}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  sáv: q10={calib['q10']:.2f} q90={calib['q90']:.2f} (standardizált), "
          f"lefedettség összesen {100*calib['coverage_overall']:.0f}%")
    return summary


def write_report(summaries: dict[str, dict]) -> None:
    lines = [
        "# Walk-forward validációs riport — testületi mérési kapu",
        "",
        f"*Készült: {date.today().isoformat()}. Protokoll: expanding-window "
        f"({WF_START}–2025), a hiperparaméterek és a warm_nights felvétele minden "
        "tanítóablakon belül, beágyazott LOYO-val választva — a tesztév utáni "
        "információ sehol nem szerepel.*",
        "",
        "## Fő számok — a régi (LOYO) és az új (walk-forward) módszertan EGYÜTT",
        "",
        "A walk-forward szám a szigorúbb: csak a múltból jósol, a trend "
        "extrapolál (nem interpolál). **Ez a szám az, amire üzleti döntést "
        "érdemes alapozni.** A régi LOYO-szám összevetésül szerepel.",
        "",
        "| Termény | LOYO (régi) | Walk-forward v1 | Walk-forward v2 (agro) | "
        "Naiv trend (WF) | Győztes |",
        "|---|---|---|---|---|---|",
    ]
    for crop, s in summaries.items():
        label = config.CROPS[crop]["label"]
        lines.append(
            f"| {label} | {s['rmse_loyo_old']:.3f} | {s['rmse_wf_v1']:.3f} | "
            f"{s['rmse_wf_v2']:.3f} | {s['rmse_naive_wf']:.3f} | "
            f"**{s['winner']}** |")
    lines += [
        "",
        "*(t/ha; v2 = termény-bázisú/plafonozott GDD + EDD hőstressz-intenzitás; "
        "a warm_nights változót a belső kiválasztás ablakonként dönti el.)*",
        "",
        "## Vármegyei vs. országos hiba",
        "",
        "A fenti RMSE **vármegye-szintű** (ebből számoljuk a vármegyei sávot). Az "
        "**országos** becslés tipikus hibája kisebb, mert a vármegyei tévedések "
        "aggregáláskor részben kioltják egymást — a főoldali/PDF „a becslés tipikus "
        "tévedése ±X%” EZT az országos számot közli:",
        "",
        "| Termény | Vármegyei WF-RMSE | Országos WF-RMSE | Országos hiba (%) |",
        "|---|---|---|---|",
    ]
    for crop, s in summaries.items():
        label = config.CROPS[crop]["label"]
        trend = s.get("mean_yield", 0) or 1
        lines.append(
            f"| {label} | {s['rmse_wf']:.3f} | {s['rmse_wf_national']:.3f} | "
            f"{100*s['rmse_wf_national']/trend:.1f}% |")
    lines += [
        "",
        "*(Az országos hiba %-a itt a minta-átlaghozamhoz viszonyít; a főoldal a "
        "mindenkori trend-szinthez, ezért ott pár tizeddel eltérhet.)*",
        "",
        "## Sáv-kalibráció (M2): empirikus kvantilisek, vármegyei zsugorított szórás",
        "",
        "| Termény | q10 | q90 | Lefedettség (össz.) | Évek >=80% lefedettséggel |",
        "|---|---|---|---|---|",
    ]
    for crop, s in summaries.items():
        label = config.CROPS[crop]["label"]
        lines.append(
            f"| {label} | {s['q10']:.2f} | {s['q90']:.2f} | "
            f"{100*s['coverage_overall']:.0f}% | "
            f"{s['years_with_ge80_coverage']}/{s['n_years']} "
            f"(95% CI: {100*s['year_coverage_ci95'][0]:.0f}–"
            f"{100*s['year_coverage_ci95'][1]:.0f}%) |")
    lines += [
        "",
        "Az aszimmetrikus q10/q90 a hozameloszlás balra ferdeségét tükrözi "
        "(az aszályos lehúzás nagyobb, mint a felfelé meglepetés) — a korábbi "
        "szimmetrikus Gauss-sáv ezt csonkolta.",
        "",
        "## Stressz-év riport (agrártudósi kikötés)",
        "",
        "A walk-forward ablakból kimaradó/lefedett szélsőévek LOYO-alapú "
        "összevetése (RMSE, t/ha) — a v2 készlet a szélsőségekben sem lehet "
        "rosszabb érdemben:",
        "",
        "| Termény | Év | v1 | v2 |",
        "|---|---|---|---|",
    ]
    for crop, s in summaries.items():
        label = config.CROPS[crop]["label"]
        for st in s["stress_years"]:
            lines.append(f"| {label} | {st['year']} | {st['rmse_v1']:.3f} | "
                         f"{st['rmse_v2']:.3f} |")
    lines += [
        "",
        "## Döntés",
        "",
        "A győztes feature-készlet kerül élesbe terményenként; az élő sáv a "
        "walk-forward empirikus kvantiliseiből számolódik (aszimmetrikus, "
        "vármegyénként eltérő szélességű). A kommunikált „tipikus tévedés” "
        "mostantól a walk-forward RMSE.",
    ]
    out = config.REPORTS_DIR / "walkforward_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[ok] riport: {out}")


def main() -> None:
    summaries = {crop: run_crop(crop) for crop in config.CROPS}
    write_report(summaries)


if __name__ == "__main__":
    main()
