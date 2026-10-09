#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kiolvassa a terméshozam-előrejelző állapotát a valódi fájlokból, és megírja az alap/allapot.json fájlt.

Csak olvas: a projektben egyedül az alap/allapot.json fájlt írja. Nem megy hálózatra, ezért az élő oldalt
nem látja, csak a helyi másolatot. A 'kesz' mögött mindig mérés áll, ahol csak nyom van, ott 'nyom',
amit nem lehet megmérni, az kimarad.

A projektmappából futtatandó: python3 alap/allapot.py (pandas és pyarrow kell hozzá)."""
import datetime, glob, hashlib, json, os, re, subprocess, sys, unicodedata

sys.dont_write_bytecode = True  # a src importja ne írjon __pycache__ fájlt a projektbe
P = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, P)
RAW, PROC, WEB = (os.path.join(P, *r) for r in (("data", "raw"), ("data", "processed"), ("web", "data")))
A = {}


def be(cs, allapot, uzenet, bizonyitek=""):
    A[cs] = {"allapot": allapot, "uzenet": uzenet, "bizonyitek": bizonyitek}


def js(ut):
    with open(ut, encoding="utf-8") as f:
        return json.load(f)


def _egyezik(pill, fc):
    """A pillanatkép a modell számát őrzi, a becslésfájlban a KSH-tény is állhat
    (src/ksh_actuals): a tényt a pillanatképre is ráhelyezve kell egyeznie."""
    if pill == fc:
        return True
    try:
        from src import ksh_actuals
    except Exception:
        return False
    ksh_actuals.apply(pill)
    return pill == fc


def sha(ut):
    return hashlib.sha256(open(ut, "rb").read()).hexdigest()


def git(*arg):
    """Csak olvasó git hívás; a GIT_OPTIONAL_LOCKS=0 miatt az indexet sem frissíti."""
    r = subprocess.run(["git", *arg], cwd=P, capture_output=True, text=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    return r.stdout.strip() if r.returncode == 0 else None


def rmse(a, b):
    return float(((a - b) ** 2).mean() ** 0.5)


def main():
    import pandas as pd
    from src import config  # csak állandók, a projekt saját terményregisztere
    from src.build_panel import season_days  # a szezon hossza napokban, tiszta függvény

    TERMENYEK = list(config.CROPS)
    TREND = [c for c in TERMENYEK if config.CROPS[c].get("method") == "trend"]
    MODELLES = [c for c in TERMENYEK if c not in TREND]

    # ---------- források ----------
    slugok = [config.CROPS[c]["ksh_slug"] for c in TERMENYEK]
    evek, hianyzik = set(), []
    for s in slugok:
        for kit in ("csv", "xlsx"):
            if not os.path.exists(os.path.join(RAW, "ksh", f"{s}.{kit}")):
                hianyzik.append(f"{s}.{kit}")
        csv_ut = os.path.join(RAW, "ksh", f"{s}.csv")
        if os.path.exists(csv_ut):
            fej = open(csv_ut, encoding=config.KSH_ENCODING).read().splitlines()[1].split(";")
            evek |= {int(x) for x in fej if x.strip().isdigit()}
    verziozott = git("ls-files", "data/raw/ksh", "data/raw/boundaries", "data/raw/prices") or ""
    gitben = "nincs verziózva, csak ezen a gépen van meg" if not verziozott else "verziózva"
    if hianyzik:
        be("ksh", "hiany", f"hiányzik: {', '.join(hianyzik)}", "data/raw/ksh fájljai megszámolva")
    else:
        be("ksh", "kesz", f"{len(slugok)} tábla, {min(evek)} és {max(evek)} között; {gitben}",
           f"data/raw/ksh: {', '.join(slugok)} csv és xlsx megvan, az évek a csv fejlécéből; git ls-files data/raw/ksh")

    hu_ut, web_ut = os.path.join(RAW, "boundaries", "nuts3_hu_20M_2024.geojson"), os.path.join(WEB, "nuts3_hu.geojson")
    if os.path.exists(hu_ut):
        g = js(hu_ut)
        kodok = sorted(f["properties"]["NUTS_ID"] for f in g["features"])
        jo = len(kodok) == 20 and kodok == sorted(config.KSH_TO_NUTS3.values())
        azonos = os.path.exists(web_ut) and js(web_ut) == g
        be("hatarok", "kesz" if jo and azonos else "hiba",
           f"{len(kodok)} egység, a webes másolat {'azonos' if azonos else 'ELTÉR'}; {gitben}",
           "data/raw/boundaries/nuts3_hu_20M_2024.geojson NUTS_ID-i összevetve a config.KSH_TO_NUTS3 kódjaival és a web/data/nuts3_hu.geojson tartalmával")
    else:
        be("hatarok", "hiany", "nincs határfájl", "data/raw/boundaries/nuts3_hu_20M_2024.geojson nem található")

    wx = sorted(glob.glob(os.path.join(RAW, "weather", "HU*.parquet")))
    tol, ig, rossz = set(), set(), []
    for ut in wx:
        d = pd.read_parquet(ut)
        if not set(config.OPENMETEO_DAILY_VARS) <= set(d.columns):
            rossz.append(os.path.basename(ut))
        tol.add(str(d["date"].min())[:10]); ig.add(str(d["date"].max())[:10])
    wx_git = git("ls-files", "data/raw/weather") or ""
    wx_gitben = "a parquet fájlok nincsenek verziózva" if ".parquet" not in wx_git else "verziózva"
    if len(wx) == 20 and not rossz and len(tol) == 1 and len(ig) == 1:
        be("era5", "kesz", f"20 vármegye, {min(tol)} és {max(ig)} között; {wx_gitben}",
           "data/raw/weather/HU*.parquet: fájlok megszámolva, oszlopok és első, utolsó nap fájlonként kiolvasva")
    else:
        be("era5", "hiany", f"{len(wx)} fájl a 20-ból, hibás oszlop: {len(rossz)}, eltérő időtartomány: {len(tol) > 1 or len(ig) > 1}",
           "data/raw/weather/HU*.parquet megszámolva")

    ar_ut, arak_ut = os.path.join(RAW, "prices", "eurostat_apri_ap_crpouta_hu.json"), os.path.join(WEB, "prices.json")
    if os.path.exists(ar_ut) and os.path.exists(arak_ut):
        ev = {c: v.get("latest_year") for c, v in js(arak_ut)["crops"].items()}
        ok = set(ev) >= set(TERMENYEK)
        be("eurostat_ar", "kesz" if ok else "hiany", f"{len(ev)} termény, legutóbbi árév: {max(ev.values())}",
           "data/raw/prices/eurostat_apri_ap_crpouta_hu.json megvan, a web/data/prices.json terményei és latest_year mezője kiolvasva")
    else:
        be("eurostat_ar", "hiany", "nincs árfájl", "data/raw/prices vagy web/data/prices.json hiányzik")

    ok_letoltes = all(A[k]["allapot"] == "kesz" for k in ("ksh", "hatarok", "era5", "eurostat_ar"))
    be("letoltes", "kesz" if ok_letoltes else "hiany",
       "minden nyers fájl megvan" if ok_letoltes else "hiányos: " + ", ".join(k for k in ("ksh", "hatarok", "era5", "eurostat_ar") if A[k]["allapot"] != "kesz"),
       "a négy forrás fenti mérése: 5 KSH tábla két formátumban, 20 egységes határfájl, 20 időjárásfájl, Eurostat árfájl")

    # ---------- panel, mutatók ----------
    gond_p, gond_m, sorok_p, sorok_m = [], [], 0, 0
    for c in TERMENYEK:
        p = pd.read_parquet(os.path.join(PROC, f"panel_{c}.parquet"))
        d = pd.read_parquet(os.path.join(PROC, f"weather_daily_{c}.parquet"))
        f = pd.read_parquet(os.path.join(PROC, f"features_{c}.parquet"))
        sorok_p += len(p); sorok_m += len(f)
        if p["nuts_id"].nunique() != 20:
            gond_p.append(f"{c}: {p['nuts_id'].nunique()} egység")
        if p.duplicated(["nuts_id", "crop_year"]).sum():
            gond_p.append(f"{c}: duplikált sor")
        sz = d.dropna(subset=["crop_year"])
        if sz[config.OPENMETEO_DAILY_VARS].isna().sum().sum():
            gond_p.append(f"{c}: hiányzó érték az időjárásban")
        # ugyanaz a feltétel, mint a build_panel fáziszáró ellenőrzésében: a panel utolsó événél nem későbbi
        # termésévek napszáma legfeljebb 2 nappal térhet el a szezon hosszától
        hossz = season_days(c)
        napok = sz[sz["crop_year"] <= p["crop_year"].max()].groupby(["nuts_id", "crop_year"])["date"].count()
        rossz_ev = sorted({int(e) for e in napok[(napok - hossz).abs() > 2].index.get_level_values("crop_year")})
        if rossz_ev:
            kivul = [e for e in rossz_ev if e < p["crop_year"].min()]
            gond_p.append(f"{config.CROPS[c]['label']}: a build_panel záróellenőrzésének feltétele nem teljesül a {', '.join(map(str, rossz_ev))}. termésévre "
                          f"({int(napok.xs(rossz_ev[0], level='crop_year').min())} nap a {hossz}-ból)"
                          + ("; ez az év a panelen kívül van, a panel évei teljesek" if kivul == rossz_ev else ""))
        if f.duplicated(["nuts_id", "crop_year"]).sum():
            gond_m.append(f"{c}: duplikált kulcs")
        if f.drop(columns=["nuts_id"]).isna().sum().sum():
            gond_m.append(f"{c}: hiányzó érték")
        if len(f) != 20 * p["crop_year"].nunique():
            gond_m.append(f"{c}: {len(f)} sor, várt {20 * p['crop_year'].nunique()}")
    be("panel", "hiba" if gond_p else "kesz", "; ".join(gond_p) if gond_p else f"{len(TERMENYEK)} termény, {sorok_p} hozamsor",
       "data/processed/panel_*.parquet és weather_daily_*.parquet: egységek száma, duplikátum, hiányzó érték, és a termésévenkénti napszám a szezon hosszához mérve (src/build_panel.py season_days, tűrés 2 nap), a build_panel futtatása nélkül újraszámolva")
    be("mutatok", "hiba" if gond_m else "kesz", "; ".join(gond_m) if gond_m else f"{len(TERMENYEK)} termény, {sorok_m} sor",
       "data/processed/features_*.parquet: sorok száma a panel éveihez mérve, hiányzó érték és duplikált kulcs újramérve")

    # ---------- validáció, mérési kapu ----------
    reszek, gond = [], []
    for c in MODELLES:
        lo_ut, bt_ut = os.path.join(PROC, f"loyo_results_{c}.parquet"), os.path.join(PROC, f"backtest_results_{c}.parquet")
        if not (os.path.exists(lo_ut) and os.path.exists(bt_ut)):
            gond.append(f"{c}: hiányzó eredményfájl"); continue
        lo = pd.read_parquet(lo_ut)
        m, n = rmse(lo["actual"], lo["pred"]), rmse(lo["actual"], lo["naive_trend"])
        reszek.append(f"{config.CROPS[c]['label']} {m:.3f} a naiv {n:.3f} ellen")
        if not m < n:
            gond.append(f"{c}: a modell nem veri a naiv trendet")
    be("validacio", "hiba" if gond else "kesz", "; ".join(gond) if gond else "RMSE t/ha: " + "; ".join(reszek),
       "data/processed/loyo_results_*.parquet actual, pred és naive_trend oszlopából újraszámolva; backtest_results_*.parquet megvan. A trendalapú terményekre nincs ilyen fájl")

    reszek, gond = [], []
    for c in TERMENYEK:
        ut = os.path.join(PROC, f"walkforward_summary_{c}.json")
        if not os.path.exists(ut):
            gond.append(f"{c}: nincs összefoglaló"); continue
        w = js(ut)
        if c in TREND:
            if w.get("method") != "trend":
                gond.append(f"{c}: a config trendet mond, az összefoglaló nem")
            reszek.append(f"{config.CROPS[c]['label']} trend {w['rmse_wf']:.3f}")
        else:
            if not w["rmse_wf"] < w["rmse_naive_wf"]:
                gond.append(f"{c}: időjárásmodellel megy ki, de nem veri a naiv trendet")
            v2 = any(x.startswith("gddc_") for x in config.CROPS[c]["model_features"])
            if (w["winner"] == "v2") != v2:
                gond.append(f"{c}: a config változókészlete nem a győztes ({w['winner']})")
            reszek.append(f"{config.CROPS[c]['label']} {w['winner']} {w['rmse_wf']:.3f} a naiv {w['rmse_naive_wf']:.3f} ellen")
    be("meresi_kapu", "hiba" if gond else "kesz", "; ".join(gond) if gond else "; ".join(reszek),
       "data/processed/walkforward_summary_*.json rmse_wf, rmse_naive_wf, winner és method mezője összevetve a src/config.py CROPS módszerével és változókészletével")

    riport = [f for f in ("backtest_report.md", "backtest_report_corn.md", "backtest_report_barley.md", "walkforward_report.md")
              if os.path.exists(os.path.join(P, "reports", f))]
    abrak = glob.glob(os.path.join(P, "reports", "figures", "*.png"))
    be("riportok", "kesz" if len(riport) == 4 else "hiany", f"{len(riport)} riport, {len(abrak)} ábra", "reports/*.md és reports/figures/*.png megszámolva")

    # ---------- tényidősor: a projekt saját KSH-olvasójával, a kapu újramérve ----------
    try:
        from src.export_history import ksh_national_official
        gond, n_ev = [], 0
        for c in TERMENYEK:
            if not os.path.exists(os.path.join(WEB, f"yield_history_{c}.json")):
                gond.append(f"{c}: nincs yield_history fájl"); continue
            p = pd.read_parquet(os.path.join(PROC, f"panel_{c}.parquet"))
            hiv = ksh_national_official(c)
            s = p.dropna(subset=["area_ha", "production_t"]).groupby("crop_year")[["area_ha", "production_t"]].sum()
            for ev in s.index:
                n_ev += 1
                for oszlop in ("area_ha", "production_t"):
                    if int(ev) in hiv[oszlop] and abs(s.loc[ev, oszlop] - hiv[oszlop][int(ev)]) > 25:
                        gond.append(f"{c} {int(ev)} {oszlop}")
        be("idosor", "hiba" if gond else "kesz", ("eltérés: " + "; ".join(gond[:4])) if gond else f"{len(TERMENYEK)} termény, {n_ev} év egyezik",
           "panel_*.parquet vármegyei összegei összevetve a data/raw/ksh csv Ország összesen sorával (a projekt ksh_national_official olvasójával), tűrés 25 egység")
    except Exception as e:  # ha a projekt olvasója nem tölthető be, csak a fájlok megléte látszik
        megvan = [c for c in TERMENYEK if os.path.exists(os.path.join(WEB, f"yield_history_{c}.json"))]
        be("idosor", "nyom", f"{len(megvan)} idősorfájl megvan", f"web/data/yield_history_*.json megszámolva; a keresztellenőrzés nem futott: {type(e).__name__}")

    # ---------- tesztöv: a projekt saját, csak olvasó ellenőrzője ----------
    venv = os.path.join(P, ".venv", "bin", "python")
    if os.path.exists(venv):
        r = subprocess.run([venv, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"], cwd=P, capture_output=True, text=True,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        veg = (r.stdout.strip().splitlines() or ["nincs kimenet"])[-1]
        m = re.search(r"(\d+) passed", veg)
        if r.returncode == 0 and m:
            be("tesztek", "kesz", f"{m.group(1)} teszt átment", "lefuttatva: .venv/bin/python -m pytest tests/ -q -p no:cacheprovider (gyorsítótár és bájtkód írása nélkül)")
        else:
            be("tesztek", "hiba", veg[:160], "lefuttatva: .venv/bin/python -m pytest tests/ -q -p no:cacheprovider")

    # ---------- napi futás: becslés, árak, PDF, kiadás ----------
    fc = {c: js(os.path.join(WEB, f"forecast_{c}.json")) for c in TERMENYEK if os.path.exists(os.path.join(WEB, f"forecast_{c}.json"))}
    if not fc:
        sys.exit("nincs egyetlen forecast fájl sem, nincs miből állapotot olvasni")
    napok = {c: d["updated_at"] for c, d in fc.items()}
    futas = max(napok.values())
    lemaradas = (datetime.date.today() - datetime.date.fromisoformat(futas)).days

    ismert = {c: d.get("weather_known_until") for c, d in fc.items()}
    be("elo_idojaras", "nyom", f"az utolsó ismert nap terményenként: {min(ismert.values())} és {max(ismert.values())} között",
       "a nyers napi lekérés nincs elmentve; csak a web/data/forecast_*.json weather_known_until mezője látszik")

    mp_ut = os.path.join(WEB, "market_prices.json")
    if os.path.exists(mp_ut):
        mp = js(mp_ut)
        tetel, ft = len(mp.get("items", [])), set(mp.get("valuation", {}).get("crops", {}))
        be("piaci_ar", "nyom", f"feldolgozva: {mp.get('updated_at')}", "a nyers API-válasz nincs elmentve; csak a feldolgozott web/data/market_prices.json látszik")
        hiany = []
        if tetel < 8: hiany.append(f"csak {tetel} tétel")
        if mp.get("updated_at") != futas: hiany.append(f"a fájl napja {mp.get('updated_at')}, a futásé {futas}")
        if not ft >= set(TERMENYEK): hiany.append("hiányzó forintosítási ár: " + ", ".join(sorted(set(TERMENYEK) - ft)))
        fx = mp.get("valuation", {}).get("fx", {})
        be("arak", "hiany" if hiany else "kesz", "; ".join(hiany) if hiany else f"{tetel} tétel, árfolyam {fx.get('rate')} ({fx.get('date')})",
           "web/data/market_prices.json: items száma, updated_at, valuation.crops és valuation.fx kiolvasva")
    else:
        be("arak", "hiany", "nincs market_prices.json", "web/data/market_prices.json nem található")

    gond = [f"hiányzó becslésfájl: {c}" for c in TERMENYEK if c not in fc]
    for c, d in fc.items():
        sor = d["counties"]
        becs = [r for r in sor if r["predicted_yield_t_ha"] is not None]
        if len(sor) != 20: gond.append(f"{c}: {len(sor)} sor")
        if len(becs) != 19: gond.append(f"{c}: {len(becs)} becslés")
        if c not in TREND:  # a kód józansági határai, ahogy a predict_live fáziszáró ellenőrzése használja
            also, felso = (1.5, 9.0) if c == "wheat" else (2.0, 12.0)
            for r in becs:
                if not also <= r["predicted_yield_t_ha"] <= felso: gond.append(f"{c} {r['nuts_id']}: hozam kilóg")
                if abs(r["anomaly_pct"]) > 50: gond.append(f"{c} {r['nuts_id']}: eltérés kilóg")
        ertek = [r["predicted_yield_t_ha"] for r in becs]
        if ertek and not min(ertek) <= d["national"]["predicted_yield_t_ha"] <= max(ertek):
            gond.append(f"{c}: az országos becslés a vármegyei tartományon kívül")
        for nid, q in ((d.get("scenarios") or {}).get("counties") or {}).items():
            if not q["p10"] <= q["p50"] <= q["p90"]: gond.append(f"{c} {nid}: percentilisek nem sorrendben")
    szort = "" if len(set(napok.values())) == 1 else "; a fájlok két napra szóródnak: " + ", ".join(f"{c} {n}" for c, n in napok.items())
    be("elo_becsles", "hiba" if gond else "kesz", "; ".join(gond[:4]) if gond else f"{len(fc)} termény, 19 vármegyei becslés mindegyikben{szort}",
       "web/data/forecast_*.json: sorok, becslések száma, országos érték a vármegyei tartományban, percentilisek sorrendje; a hozam- és eltéréshatár az időjárásmodelles terményekre mérve")

    pdf_ut, utolso = os.path.join(WEB, "jelentes", f"jelentes_{futas}.pdf"), os.path.join(WEB, "jelentes_latest.pdf")
    oldal = None
    if os.path.exists(pdf_ut):
        try:
            from pypdf import PdfReader
            oldal = len(PdfReader(pdf_ut).pages)
            be("pdf_jelentes", "kesz" if 2 <= oldal <= 4 else "hiba", f"jelentes_{futas}.pdf, {oldal} oldal",
               "web/data/jelentes alatt a futás napi PDF megvan, az oldalszám pypdf-fel kiolvasva; a lábléc-hézag mérése csak a futás naplójában van")
        except ImportError:
            be("pdf_jelentes", "nyom", f"jelentes_{futas}.pdf megvan", "pypdf nincs telepítve, az oldalszám nincs megmérve")
    else:
        be("pdf_jelentes", "hiany", f"nincs PDF a futás napjára ({futas})", "web/data/jelentes/jelentes_<nap>.pdf nem található")

    gond = []
    for c, d in fc.items():
        pill = os.path.join(WEB, "history", c, f"{d['updated_at']}.json")
        if not os.path.exists(pill): gond.append(f"{c}: nincs pillanatkép {d['updated_at']}")
        elif not _egyezik(js(pill), d): gond.append(f"{c}: a pillanatkép eltér a becslésfájltól")
    if os.path.exists(pdf_ut) and os.path.exists(utolso) and sha(pdf_ut) != sha(utolso):
        gond.append("a jelentes_latest.pdf eltér a dátumozott PDF-től")
    piszkos = git("status", "--porcelain", "--", "web/data")
    mogotte = git("rev-list", "--count", "HEAD..origin/main")
    if piszkos is None:
        be("kiadas", "nyom", "a git állapota nem olvasható", "git status nem futott le")
    else:
        if piszkos: gond.append(f"{len(piszkos.splitlines())} commitolatlan változás a web/data alatt")
        be("kiadas", "hiba" if gond else "kesz", "; ".join(gond[:4]) if gond else f"{len(fc)} becslés és a PDF azonos a másolatával, minden commitolva",
           "forecast_*.json összevetve a web/data/history/<termény>/<nap>.json tartalmával, a két PDF sha256-ja, git status --porcelain web/data")

    web_fajlok = [f for f in ("index.html", "app.js", "tabla.html", "magyarazat.html") if os.path.exists(os.path.join(P, "web", f))]
    be("web", "nyom", f"a helyi másolat {futas} napi, {lemaradas} napja; az origin/main utolsó ismert állapota {mogotte or '?'} committal előrébb jár",
       f"web/ alatt {len(web_fajlok)} oldalfájl megvan; az élő GitHub Pages oldal nincs megmérve (hálózat nélkül), git rev-list HEAD..origin/main a legutóbbi fetch szerint")
    if oldal and A["kiadas"]["allapot"] == "kesz":
        be("pdf", "kesz", f"jelentes_{futas}.pdf, {oldal} oldal", "a fájl megvan és commitolva van, a jelentes_latest.pdf vele bitre azonos")
    elif oldal:
        be("pdf", "nyom", f"jelentes_{futas}.pdf, {oldal} oldal", "a fájl megvan, de a kiadás ellenőrzése nem ment át")

    # ---------- tanulságok: a projekt Claude-memóriája ----------
    mem = os.path.expanduser("~/.claude/projects/" + re.sub(r"[^A-Za-z0-9]", "-", unicodedata.normalize("NFC", P)) + "/memory")
    mf = glob.glob(os.path.join(mem, "*.md"))
    kezi = git("log", "-1", "--format=%cs", "--invert-grep", "--grep=napi forecast frissítés")
    if mf and kezi:
        nap = datetime.date.fromtimestamp(max(os.path.getmtime(f) for f in mf)).isoformat()
        azota = git("rev-list", "--count", "--invert-grep", "--grep=napi forecast frissítés", f"--since={nap} 23:59:59", "HEAD")
        be("tanulsagok", "nyom" if nap >= kezi else "hiany",
           f"a memória utoljára {nap}, a legutóbbi kézi commit {kezi}" + (f", azóta {azota} kézi commit" if nap < kezi else ""),
           f"{len(mf)} md fájl módosítási napja a projekt memóriamappájában, összevetve a git log legutóbbi nem napi-frissítés commitjával; a tartalom nincs vizsgálva")

    ki = {"frissitve": datetime.datetime.now().isoformat(timespec="minutes"), "idoszak": f"{futas} napi futás, helyi másolat", "idoszak_kulcs": str(futas), "csomopontok": A}
    with open(os.path.join(P, "alap", "allapot.json"), "w", encoding="utf-8") as f:
        json.dump(ki, f, ensure_ascii=False, indent=2)
    for k, v in A.items():
        print(f"{k:13} {v['allapot']:6} {v['uzenet']}")


if __name__ == "__main__":
    main()
