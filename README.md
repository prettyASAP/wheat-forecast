# Terméshozam-előrejelző (Magyarország, NUTS3)

Interaktív térkép, amely Magyarország 20 NUTS3 egységére (19 vármegye + Budapest)
mutatja az aktuális időjárást, és ebből folyamatosan frissülő búza- és
kukoricahozam-előrejelzést ad. A modell a KSH tényleges vármegyei hozamait
(2000-től) tanulja össze az ugyanezen területekre eső ERA5 időjárással; a két fő
magyarázó a hőmérséklet és a csapadék (ablakos GDD, hőstressz, vízmérleg).

## Állapot

| Fázis | Tartalom | Állapot |
|------|----------|---------|
| 1 | Repó váz + letöltő scriptek | ✅ |
| 2 | Panel + crosswalk | ✅ |
| 3 | Származtatott mutatók | ✅ |
| 4 | Modell + validáció (**mérési kapu**) | ✅ teljesült ([riport](reports/backtest_report.md)) |
| 5 | Élő korrekciós motor | ✅ |
| 6 | Térképes felület | ✅ |
| 7 | Automatizálás (GitHub Actions) | ✅ (GitHub remote + Pages bekapcsolás kell) |
| 8 | Kukorica bővítés | ✅ ([riport](reports/backtest_report_corn.md)) |

**Terminál-bővítések** (a 8 fázis után):
- **A** — vármegye-hozamgrafikon (2000–2025) + országos fejléc-mutatók (becslés,
  trend-anomália, YoY, percentilis); a historikus országos idősor a KSH hivatalos
  sora, szigorú parse-keresztellenőrzéssel
- **B** — termelői árak (Eurostat apri_ap_crpouta, HUF) + forintosítás: termelési
  érték és trend-rés mrd Ft-ban, az ár- és terület-évjárat explicit jelölésével
- **C** — térképréteg-váltó: anomália / vízmérleg / csapadék / hőstressz / GDD
  (az időjárási rétegek adatvezérelt, relatív skálával)
- **D** — időjárás-forgatókönyvek: a szezon hátralévő napjai a 26 analóg év
  tényleges időjárásával -> P10/P50/P90; szezon közben a fő becslés az együttes
  átlaga (Jensen-korrekció a konvex aszályjelző miatt)
- **E** — harmadik termény: **őszi árpa** (a KSH külön őszi árpa szekciójából,
  keverés nélkül; mérési kapu: LOYO 0.548 vs naiv 0.693, sáv 81.4%)

**Modell-eredmények (leave-one-year-out, out-of-sample):**
búza RMSE 0,53 t/ha (11,4%, R² 0,73), kukorica RMSE 1,40 t/ha (22,9%, R² 0,43) —
mindkettő érdemben veri a naiv trend-alapot. A 2022-es aszály iránytartása:
búza 14/19, kukorica 19/19 vármegye (a búza–kukorica kontraszt — a búza megúszta,
a kukorica összeomlott — a modellben is látszik).

## Gyors indítás

```bash
make setup      # .venv (python3.12) + függőségek
make data       # letöltés + panel + feature-ök (mindkét termény)
make model      # LOYO keresztvalidáció
make report     # as-of backtest + magyar riportok a reports/ alá
make live       # élő előrejelzés -> web/data/forecast_*.json
```

A térkép statikus: `python -m http.server --directory web` és nyisd meg a
`http://localhost:8000`-t. Termény-váltó a fejlécben, vármegyére kattintva részletek,
idővonal-csúszka a szezonon belüli alakuláshoz (2+ napi snapshot után).
Minden letöltő **idempotens**: meglévő fájlt nem tölt újra `--force` nélkül.

## Adatforrások és licenc

- **KSH** — búza termelése vármegye szerint (19.1.2.4. tábla). Szabadon letölthető és
  publikálható.
- **Eurostat GISCO** — NUTS3 2024 vármegyehatárok (20M). ⚠️ A GISCO geometriára egyes
  közlésekben **nem kereskedelmi** kikötés és forrásmegjelölés van. Kereskedelmi termékhez
  ellenőrizd a GISCO feltételeit, vagy válts **OpenStreetMap** közigazgatási határokra
  (ODbL, forrásmegjelöléssel).
- **Open-Meteo** — ERA5 reanalízis, kulcs nélkül. Adat **CC BY 4.0**, forrásmegjelöléssel.
  Nem kereskedelmi használat ingyenes (napi 10 000 hívás; 20 vármegyére bőven elég).
  ⚠️ **Kereskedelmi** használathoz előfizetés kell, VAGY saját instance önhostolható
  (Open-Meteo szerver AGPLv3, korlátlan hívás).

## Fontos modellezési döntések

- **Termésévi eltolás (búza)**: a Y évi hozamhoz a Y-1 okt 1 – Y jún 30 időjárás tartozik.
  A kukoricánál a szezon a Y naptári éven belüli (ápr–szept).
- **Időjárás kezdete 1999-10-01**: hogy a 2000-es termésév őszi vetési ablaka is lefedett
  legyen (kis, szándékos eltérés a brief 2000-01-01-jétől).
- **Budapest**: elhanyagolható termőterület — a modellben kihagyva (`config.BUDAPEST_HANDLING`).
- **wb_deficit**: konvex, halmozott vízmérleg-hiány mutató (a vármegye tanítómintabeli
  mediánjához képest) — a 2022-szerű, több ablakon átívelő aszályok megfogására;
  a mérési kapu iterációjának eredménye.
- **Bizonytalansági sáv**: a LOYO out-of-sample reziduumok szórásából (±1,282σ ≈ 80%),
  tényleges lefedettség búza 82%, kukorica 85%.

## Struktúra

`src/` pipeline (config + fetch + panel + features + model + validate + backtest + live),
`data/` (raw/interim/processed, nem verziózott), `web/` statikus térkép,
`reports/` a mérési kapu riportja, `.github/workflows/` napi cron.
