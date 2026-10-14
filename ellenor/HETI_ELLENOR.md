# Heti ellenőr – utasítás

Cél: a terméshozam-előrejelző minden kiírt száma a legfrissebb megbízható adatot
mutassa, az `ellenor/SZABALYOK.md` szerint, és a szöveg illeszkedjen a számokhoz.
Ha van frissebb megbízható adat, amelyet az anyag még nem használ, frissítsd rá.
Ha egy szám megváltozott, nézd át az összes szöveget, amely azt a számot
értelmezi, és igazítsd hozzá.

## Fontos: hol kell javítani

A kiadott anyagot (`web/data/*.json`, `web/data/jelentes*`) a napi futás
(`.github/workflows/daily.yml`) minden éjjel újragenerálja a kódból. Ezeket a
fájlokat soha ne szerkeszd kézzel, mert másnap felülíródnak. A javítás mindig a
kódban történik (letöltők, `src/ksh_actuals.py`, `src/report_html.py`, `web/*.js`,
`web/*.html`), hogy a napi futás magától a helyes számot adja.

## Lépések

1. Olvasd el az `ellenor/SZABALYOK.md` fájlt. Csak ezek a szabályok számítanak.
   Ha egy hibát egyik szabály sem fed le, de egyértelműen helytelen adat, jelezd
   „új szabályjavaslat” címkével (ezt ne javítsd magadtól).
2. A kiadott anyag a repóban: `web/data/jelentes_latest.pdf` (`pdftotext -layout`,
   ha nincs, `pip install pypdf`), `web/data/forecast_*.json`, `ksh_actuals.json`,
   `official_estimates.json`, `stations.json`, és a felületi szövegek
   (`web/app.js`, `web/tabla.js`, `web/magyarazat.js`, `web/magyarazat.html`).
   Nézd meg az élő oldalt is: https://prettyasap.github.io/wheat-forecast/data/jelentes_latest.pdf
3. Minden kiírt számhoz keresd meg a legfrissebb megbízható forrást, frissen lekérve:
   - KSH STADAT: https://www.ksh.hu/stadat_files/mez/hu/mez0071.html (búza),
     mez0072 (kukorica), mez0073 (árpa), mez0078 (napraforgó), mez0079 (repce).
     A CSV-link az oldalon van, kódolás iso-8859-2. Nézd meg az „Utolsó frissítés”
     dátumát és az utolsó évoszlopot.
   - EU-becslés: `python -c "from datetime import date; from src.fetch_market_prices import collect_official; print(collect_official(date.today()))"`.
   - HungaroMet és piaci árak: a napi futás tölti le; nézd meg, hogy friss-e
     (`stations.json` updated_at, `market_prices.json` updated_at).
   - Friss hírek (NAK, AKI, KSH gyorstájékoztató) webes kereséssel: csak
     ellenpróbának. Főszám csak gépileg letölthető hivatalos forrásból lehet.
4. Vesd össze: a kiadott szám a legfrissebb megbízható adat-e. Ha nem, keresd meg
   az okát a kódban (pl. formátumváltás a forrásnál, hiányzó letöltés, rossz év),
   és javítsd ott.
5. Ha bármelyik szám megváltozik (vagy megváltozott a múlt hét óta), nézd át az
   összes szöveget, amely értelmezi: fejlécmondat, kártyafeliratok, „Mi húzta”,
   rangsor-mondat, forrásjelölés, lábjegyzet, fogalomtár (pl. „2026-ban jóval
   alacsonyabb”), és igazítsd a számhoz. Szám nélküli állítás sem maradhat, ha a
   szám már mást mutat.
6. Ellenőrzés a javítás után, mind kötelező:
   - `pip install -r requirements.txt`, `python -m pytest tests/ -q`: mind zöld;
   - újragenerálás a javított kóddal: az érintett terményekre
     `python -m src.ksh_actuals`, `python -m src.predict_live --crop <termény>`,
     majd `python -m src.report_html --no-pdf`; a kapott HTML szövegén nézd végig
     újra a szabályokat;
   - a generált fájlokat (`web/data/`) a commit előtt állítsd vissza
     (`git checkout -- web/data/`), mert azokat a napi futás állítja elő.
7. Jelentés: `ellenor/riportok/ÉÉÉÉ-HH-NN.md`, magyarul, tömören:
   - első sor: „Frissítve: N, javaslat: M” vagy „Nincs frissebb adat, nincs lelet”;
   - táblázat: mit néztél, mi állt, mi a legfrissebb megbízható adat (URL, érték,
     dátum), mit tettél;
   - amit nem tudtál ellenőrizni és miért.

## Mikor frissíthetsz közvetlenül (commit a main ágra)

Csak ha MINDEN teljesül:
- a változás forrása gépileg letölthető hivatalos adat (KSH, EU DG AGRI,
  HungaroMet, MNB), és a szabályfájl egyértelműen megmondja, mi a helyes állapot;
- a javítás nem érinti a modellt (SZABALYOK 5. pont fájljai) és a
  `.github/workflows/` mappát;
- a tesztek zöldek, és az újragenerált anyag minden szabálynak megfelel.

Ilyenkor egy commit a main ágra: kódjavítás és a jelentés együtt, üzenet:
„Heti ellenőr ÉÉÉÉ-HH-NN: <rövid leírás>”. Push előtt `git pull --rebase`.

Minden más esetben (döntés kell, a modell érintett, bizonytalan forrás, bukó
teszt, új szabályjavaslat) ne commitolj a main ágra: készíts ágat
(`ellenor/ÉÉÉÉ-HH-NN`), és nyiss pull requestet „Heti ellenőrzés ÉÉÉÉ-HH-NN”
címmel. Ha `gh` nem érhető el, told fel az ágat, és a záró üzenetben írd meg a
nevét. Ha nincs semmi teendő, csak a jelentést commitold a main ágra.

## Stílus

Magyarul, rövid mondatokkal, gondolatjel nélkül. Számot csak forrással írsz le.
Ha valamit nem tudtál ellenőrizni, mondd ki, ne találgass.
