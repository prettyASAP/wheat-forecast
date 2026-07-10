# Búzahozam-előrejelző (Magyarország, NUTS3)

Interaktív térkép, amely Magyarország 20 NUTS3 egységére (19 vármegye + Budapest)
mutatja az aktuális időjárást, és ebből folyamatosan frissülő búzahozam-előrejelzést
ad. A modell a KSH tényleges vármegyei hozamait (2000-től) tanulja össze az ugyanezen
területekre eső ERA5 időjárással; a két fő magyarázó a hőmérséklet és a csapadék.

## Állapot

| Fázis | Tartalom | Állapot |
|------|----------|---------|
| 1 | Repó váz + letöltő scriptek | ✅ folyamatban/kész |
| 2 | Panel + crosswalk | ⏳ |
| 3 | Származtatott mutatók | ⏳ |
| 4 | Modell + validáció (**mérési kapu**) | ⏳ |
| 5 | Élő korrekciós motor | ⏳ |
| 6 | Térképes felület | ⏳ |
| 7 | Automatizálás (GitHub Actions) | ⏳ |
| 8 | Kukorica bővítés (opcionális) | ⏳ |

## Gyors indítás

```bash
make setup      # .venv (python3.12) + függőségek
make fetch      # KSH hozam + NUTS3 határok + ERA5 időjárás -> data/raw/
```

Vagy egyesével: `make ksh`, `make boundaries`, `make weather`.
Az `weather` cél a `boundaries` kimenetéből (centroidok) dolgozik, ezért azt futtasd előbb.
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

- **Termésévi eltolás**: a Y évi hozamhoz a Y-1 okt 1 – Y jún 30 időjárás tartozik.
- **Időjárás kezdete 1999-10-01**: hogy a 2000-es termésév őszi vetési ablaka is lefedett
  legyen (kis, szándékos eltérés a brief 2000-01-01-jétől).
- **Budapest**: elhanyagolható búzatermelés — a modellben kihagyva (`config.BUDAPEST_HANDLING`).

## Struktúra

`src/` pipeline (config + fetch + panel + features + model + validate + backtest + live),
`data/` (raw/interim/processed, nem verziózott), `web/` statikus térkép,
`reports/` a mérési kapu riportja, `.github/workflows/` napi cron.
