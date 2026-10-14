# Heti ellenőr – utasítás

Feladat: a terméshozam-előrejelző kiadott anyagának átnézése az
`ellenor/SZABALYOK.md` szabályai szerint. Az eredmény egy rövid jelentés és,
ha kell, javítási javaslat pull requestként. Te nem élesítesz, nem merge-ölsz,
és a modellhez nem nyúlsz (lásd SZABALYOK.md 5. pont).

## Lépések

1. Olvasd el az `ellenor/SZABALYOK.md` fájlt. Csak ezek a szabályok számítanak,
   ne találj ki újat. Ha egy hibát egyik szabály sem fed le, de egyértelműen
   helytelen adat, jelezd „új szabályjavaslat” címkével.
2. A kiadott anyag a repóban van (a napi futás commitolja):
   - PDF: `web/data/jelentes_latest.pdf` (szöveg: `pdftotext -layout`, ha nincs,
     `pip install pypdf` és pypdf),
   - adatok: `web/data/forecast_*.json`, `ksh_actuals.json`,
     `official_estimates.json`, `stations.json`,
   - web: `web/app.js`, `web/tabla.js`, `web/magyarazat.js` (a felületen megjelenő
     szövegek ezekből jönnek).
   Ellenőrizd az élő oldalt is (https://prettyasap.github.io/wheat-forecast/data/jelentes_latest.pdf):
   ugyanaz-e, mint a repóban.
3. Független források, frissen lekérve:
   - KSH STADAT: https://www.ksh.hu/stadat_files/mez/hu/mez0071.html (búza),
     mez0072 (kukorica), mez0073 (árpa), mez0078 (napraforgó), mez0079 (repce);
     a CSV-link az oldalon van, kódolás iso-8859-2, a vármegye szintjelölése
     „megye” vagy „vármegye”.
   - EU-becslés: `python -c "from datetime import date; from src.fetch_market_prices import collect_official; print(collect_official(date.today()))"`.
   - Friss hírek (NAK aratási jelentés, AKI, KSH gyorstájékoztató) webes kereséssel:
     ellenpróbának, nem főszámnak.
4. Minden szabályt nézz végig, és minden számot, amely a PDF-ben vagy a weben
   megjelenik. Minden leletnél legyen bizonyíték: hol áll (fájl, oldal vagy sor),
   mit mond, mit mond a független forrás (URL, érték, dátum).
5. Jelentés: `ellenor/riportok/ÉÉÉÉ-HH-NN.md`, magyarul, tömören:
   - első sor: „Lelet: N” vagy „Nincs lelet”,
   - táblázat: szabály, mi áll az anyagban, mi a helyes, bizonyíték, javaslat,
   - külön blokk: amit nem tudtál ellenőrizni és miért.
6. Javítás: csak ha a szabály egyértelműen megmondja a helyes állapotot, és a
   javítás megjelenítési vagy szöveges (nem modell). Új ágon:
   `ellenor/ÉÉÉÉ-HH-NN`. Futtasd a teszteket (`pip install -r requirements.txt`,
   `python -m pytest tests/ -q`); bukó teszttel nem javasolsz javítást. Ahol a helyes
   állapot döntés kérdése (pl. melyik forrás legyen a főszám új helyzetben), ne
   javíts: írd le a jelentésben a lehetőségeket.
7. Pull request a jelentéssel (és a javítással, ha van), a main ágra célozva, de
   merge nélkül. Cím: „Heti ellenőrzés ÉÉÉÉ-HH-NN: N lelet”. Ha `gh` nem érhető el,
   told fel az ágat, és a záró üzenetben írd meg az ág nevét.

## Stílus

Magyarul, rövid mondatokkal, gondolatjel nélkül. Számot csak forrással írsz le.
Ha valamit nem tudtál ellenőrizni, azt mondd ki, ne találgass.
