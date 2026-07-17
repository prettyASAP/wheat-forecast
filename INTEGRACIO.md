# Integráció: a napi terméshozam-PDF beillesztése egy másik PDF-pipeline-ba

Ez a dokumentum mindent tartalmaz ahhoz, hogy ez a projekt egy **másik szoftver**
PDF-generáló folyamatába illeszkedjen: a másik program lefuttatja ezt, megkapja a
kész **3 oldalas A4 PDF-et**, és beolvasztja a saját kimenetébe.

A jelentés HTML→PDF technológiával készül (headless Chromium / Playwright), a
Claude Designban tervezett szedéssel, **élő adatból**.

---

## 1. Egyetlen parancs (a lényeg)

A projekt gyökeréből:

```bash
python -m src.report_html --out /tetszoleges/utvonal/termeshozam.pdf
```

Ez legenerálja a PDF-et, és **a megadott útvonalra írja** (emellett a projekt
saját `web/data/jelentes_latest.pdf` helyére is). Kilépési kód 0 = siker.

- Kimenet: **3 oldalas A4 PDF** (szezonon kívül, ha egyik termény sincs futó
  szezonban, 2 oldal — jelenleg a kukorica fut, tehát 3 oldal).
- A parancs **hálózatot NEM igényel az adatokhoz** (a becslések a repóban
  verziózott JSON-fájlokból jönnek). Egyetlen külső kérés a Google Fonts
  (Barlow) betöltése rendereléskor; ha nincs net, rendszerfontra esik vissza,
  a PDF akkor is elkészül.

---

## 2. Telepítés (egyszeri)

**Python 3.12** kell. A projekt gyökeréből:

```bash
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install --with-deps chromium
```

- A `requirements.txt` pinnelt csomagokat telepít (pandas, numpy, geopandas,
  shapely, matplotlib, pyarrow, openpyxl, **playwright**, …).
- A `playwright install ... chromium` letölti a fej nélküli böngészőt (~150 MB).
  A `--with-deps` a Linux rendszerfüggőségeket is telepíti (GitHub Actionsön,
  Ubuntun működik; macOS/Windows alatt a `--with-deps` elhagyható).
- Ha a másik szoftvernek saját virtuális környezete van, telepítsd ugyanezeket
  oda, és onnan hívd (a `python` alább mindig a megfelelő venv Pythonja).

---

## 3. Mit olvas be (bemenő adatok — mind a repóban van)

A PDF **kizárólag** ezekből a — a repóban verziózott — fájlokból dolgozik, tehát
másolás után azonnal futtatható, adatletöltés nélkül:

| Fájl | Tartalom |
|---|---|
| `web/data/forecast_wheat.json`, `forecast_corn.json`, `forecast_barley.json` | a napi becslések (országos + vármegyei, sávok, forgatókönyvek, forintérték) |
| `web/data/nuts3_hu.geojson` | a vármegyehatárok a térképekhez |
| `web/data/history/{wheat,corn,barley}/*.json` | a napi pillanatképek a szezonközi trendábrához |

Nem kell hozzá adatbázis, szerver, sem az `src/` egyéb letöltő/számoló moduljai
(fetch, predict, walkforward) — azok csak az adat **frissítéséhez** kellenek
(lásd 6. pont).

---

## 4. Kimenet

- Alapból: `web/data/jelentes/jelentes_<ÉÉÉÉ-HH-NN>.pdf` **és**
  `web/data/jelentes_latest.pdf`.
- `--out <útvonal>` esetén **oda is** kiírja (a köztes HTML és térkép-PNG-k a
  `web/data/jelentes/`-be kerülnek, azok nem zavarnak).
- A `main()` visszaadja a legenerált PDF `Path`-ját.

---

## 5. Programozott hívás (két mód)

Minden útvonal a modulhoz képest abszolút (`config.PROJECT_ROOT =
Path(__file__)...`), ezért **tetszőleges munkakönyvtárból** hívható.

**A) Alfolyamatként (a legrobusztusabb, izolált):**

```python
import subprocess, sys
from pathlib import Path

PROJEKT = Path("/ide/masolt/mezogazdasag")          # a bemásolt mappa
VENV_PY = PROJEKT / ".venv" / "bin" / "python"       # vagy a saját venv Pythonod

subprocess.run(
    [str(VENV_PY), "-m", "src.report_html", "--out", "/kimenet/termeshozam.pdf"],
    cwd=str(PROJEKT), check=True,
)
# a PDF most itt van: /kimenet/termeshozam.pdf
```

**B) Közvetlen importtal (ha ugyanabban a Python-környezetben vagy):**

```python
import sys
from pathlib import Path

PROJEKT = Path("/ide/masolt/mezogazdasag")
sys.path.insert(0, str(PROJEKT))                     # hogy az `src` importálható legyen

from src.report_html import main
pdf_path = main(out_path="/kimenet/termeshozam.pdf") # visszaadja a Path-ot
```

---

## 6. Beolvasztás a másik szoftver PDF-jébe (összefűzés)

A kész PDF-et bármely PDF-könyvtárral hozzáfűzheted a többihez. Példa `pypdf`-fel:

```python
from pypdf import PdfWriter

writer = PdfWriter()
writer.append("/a/masik/szoftver/sajat_resze.pdf")   # a többi oldal
writer.append("/kimenet/termeshozam.pdf")            # ez a 3 oldal a végére
with open("/kimenet/vegleges_egyesitett.pdf", "wb") as f:
    writer.write(f)
```

(Az oldalméret A4; ha a másik dokumentum is A4, gond nélkül illeszkedik.)

---

## 7. Friss adat (opcionális)

A repóban lévő `forecast_*.json` a legutóbbi napi futásból való. Ha a PDF-nek a
**mai** időjárással kell frissülnie, előbb futtasd a becslést (ehhez kell net,
mert az Open-Meteo ERA5-öt tölt):

```bash
python -m src.predict_live --crop wheat
python -m src.predict_live --crop corn
python -m src.predict_live --crop barley
python -m src.report_html --out /kimenet/termeshozam.pdf
```

Ha csak „a jelentést, ahogy van" akarod beilleszteni, a 7. pont kihagyható — a
`report_html` a meglévő adatból dolgozik.

---

## 8. Buktatók / megjegyzések

- **A `src` csomagként importálódik.** Vagy a projekt gyökeréből futtasd
  (`python -m src.report_html`), vagy tedd a gyökeret a `sys.path`-ra (5/B).
- **Chromium kell.** Ha a `playwright install chromium` kimaradt, a renderelés
  hibázik. `--no-pdf` kapcsolóval csak a HTML készül el (Playwright nélkül).
- **Ékezetes mappanév** (a projekt neve `mezőgazdaság`): a kód mindenhol
  UTF-8/`pathlib` útvonalakat használ, ez rendben van; ha a másik rendszer
  kényes az ékezetre, bátran nevezd át a mappát (pl. `mezogazdasag`), a kód
  ettől működik.
- **geopandas rendszerfüggőségek:** a `requirements.txt` pinnelt `geopandas`/
  `shapely` pip-wheeljei a legtöbb rendszeren önállóan működnek; ha mégis GEOS/
  GDAL hiányt jelez, telepítsd a rendszer GDAL-t, vagy conda-környezetet
  használj.
- **Determinisztikus.** Ugyanazon adatból ugyanaz a PDF (a fejlécben a generálás
  időbélyege az egyetlen, ami változik).
- **Oldalszám:** futó szezonban 3 oldal, szezonon kívül (nincs „még változhat"
  termény) 2 oldal — a beolvasztó logika kezelje mindkettőt (ne feltételezz fix
  oldalszámot).

---

## 9. Minimális fájlkészlet (ha nem az egész repót másolod)

A legegyszerűbb az **egész mappát** bemásolni. Ha csak a PDF-hez szükséges részt
akarod, ezek kellenek:

```
src/report_html.py        # a generátor
src/config.py             # útvonalak, terménydefiníciók
web/data/forecast_*.json  # a becslések (3 db)
web/data/nuts3_hu.geojson # vármegyehatárok
web/data/history/**       # a trendábrához
requirements.txt          # függőségek
```

(A `src/__init__.py` is kell, ha a `src` csomagként importálódik; a repóban jelen
van.)

---

## Összefoglalás egy mondatban

Másold be a mappát, telepítsd a függőségeket (`requirements.txt` + `playwright
install chromium`), majd hívd:
`python -m src.report_html --out <cél.pdf>` — a 3 oldalas A4 PDF a megadott
helyre kerül, és a saját PDF-edhez fűzheted (pl. `pypdf`).
