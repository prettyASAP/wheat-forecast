# Terméshozam-előrejelző (Magyarország, NUTS3)

[![tests](https://github.com/prettyASAP/wheat-forecast/actions/workflows/tests.yml/badge.svg)](https://github.com/prettyASAP/wheat-forecast/actions/workflows/tests.yml) [![daily-forecast](https://github.com/prettyASAP/wheat-forecast/actions/workflows/daily.yml/badge.svg)](https://github.com/prettyASAP/wheat-forecast/actions/workflows/daily.yml)

**Élő térkép:** https://prettyasap.github.io/wheat-forecast/ · [Módszertan](https://prettyasap.github.io/wheat-forecast/magyarazat.html)

Vármegyei szintű (19 vármegye, NUTS3) terméshozam-előrejelzés búzára, kukoricára,
őszi árpára, napraforgóra és repcére. A modell a KSH 2000 óta közölt vármegyei
hozamait tanulja össze az ugyanazokra a területekre aggregált ERA5 időjárással,
és a szezon során naponta frissül. A futást GitHub Actions végzi minden reggel;
az eredmény egy statikus térkép és egy napi PDF jelentés.

> *English summary:* county-level crop yield nowcasting for Hungary. A panel
> regression with county fixed effects and a linear trend, fitted on official KSH
> yields (2000–) and ERA5 weather features, re-run daily via GitHub Actions and
> published as a static map. Validated out-of-sample (leave-one-year-out and
> as-of backtests) against a naive trend baseline.

## Eredmények (out-of-sample)

Leave-one-year-out keresztvalidáció, vármegye-év szinten, a naiv trend-alaphoz mérve:

| Termény | RMSE (t/ha) | RMSE (%) | R² | Naiv trend RMSE (t/ha) | 80%-os sáv lefedettsége | Riport |
|---|---|---|---|---|---|---|
| Búza | 0,529 | 11,4% | 0,73 | 0,682 | 82,6% | [backtest](reports/backtest_report.md) |
| Kukorica | 1,396 | 22,9% | 0,43 | 1,725 | 85,0% | [backtest](reports/backtest_report_corn.md) |
| Őszi árpa | 0,545 | 12,1% | 0,78 | 0,693 | 81,8% | [backtest](reports/backtest_report_barley.md) |

A 2022-es aszályban a modell iránya búzánál 19-ből 14, kukoricánál 19-ből 19
vármegyében stimmelt (a búza megúszta, a kukorica összeomlott, és ez a modellben
is látszik). A szezon közbeni pontosságot a [walk-forward riport](reports/walkforward_report.md)
méri. Napraforgóra és repcére ugyanez a pipeline fut, külön riport nélkül.

## Mit tud

- **Napi nowcast** a szezon hátralévő részére analóg évek tényleges időjárásával
  (26 forgatókönyv, P10/P50/P90), a konvex aszályjelző miatt Jensen-korrekcióval
- **Térképrétegek:** hozam-anomália, vízmérleg, csapadék, hőstressz, GDD
- **Vármegyei idősor** 2000-től, országos mutatók (becslés, trend-eltérés, YoY, percentilis)
- **Forintosítás:** Eurostat termelői árakkal termelési érték és trend-rés mrd Ft-ban
- **Napi vezetői PDF** a `web/data/jelentes_latest.pdf` alatt

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
