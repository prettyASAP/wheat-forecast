# Ellenőrzési szabályok – terméshozam-előrejelzés

A heti ellenőr ezek alapján nézi át a kiadott anyagot (napi PDF:
`web/data/jelentes_latest.pdf`, web: `web/index.html`, `web/tabla.html`,
`web/magyarazat.html`, adatok: `web/data/*.json`). Élő cím:
https://prettyasap.github.io/wheat-forecast/

Alapelv: **csak helyes adat jelenhet meg. Ami nem bizonyíthatóan helyes, az ne
szerepeljen.** Nincs kitalált szám, nincs ellenőrizetlen magyarázat, nincs
túlmagyarázás és AI-zsargon.

## 1. Melyik szám a főszám

| Helyzet | Főszám | Mellette |
|---|---|---|
| Lezárt termésév, a KSH közölte (STADAT 19.1.2.x) | KSH-adat | a modell záró becslése, egy sorban |
| Teljesen lezárt szezon, KSH még nincs | Európai Bizottság becslése (legfeljebb 45 napos, a termény saját termésévére) | a modell záró becslése; megyei szám nincs |
| Futó szezon (búza, árpa márc. 1-től; kukorica jún. 1-től a szezon végéig) | a modell becslése, 80%-os sávval | EU-becslés viszonyításként |
| Termésév eleje (búza, árpa okt. 1. – febr. vége; kukorica ápr. 1. – máj. vége) | nincs hozamszám, modelles tartomány sem | sokéves szint és az előző évi tény |
| Trendalapú termény (napraforgó, repce) KSH nélkül | lezárt szezonban EU-becslés, futóban a sokéves szint, forint nélkül | – |

Ellenőrizd: a KSH aktuális táblája (ksh.hu/stadat_files/mez/hu/mez0071, 0072,
0073, 0078, 0079) közöl-e már újabb évet, mint amit a kiadott anyag tényként mutat.

## 2. Időjárás

- Megyei csapadék csak mérésből (HungaroMet állomásátlag). A modell megyénként
  egyetlen pontot néz, ezért megyei modellcsapadék és megyei vízmérleg nem jelenhet meg.
- Két forrás csak pontosan azonos időablakra vethető össze. A HungaroMet napja
  06 UTC-től másnap 06 UTC-ig tart, a modellé éjféltől éjfélig.
- Kiírt időjárás csak eltelt napokból (tegnapig). Előrejelzett nap nem szerepelhet
  „esett” állításban.
- Számszerű adat mellett mindig a pontos időszak áll („Okt. 1–11. között”),
  nem nyitott „óta”.
- Vízmérleg = csapadék mínusz potenciális párolgás (ET₀). Nem „párolgás”.

## 3. Megfogalmazás

- Tilos: „vetés óta”, „vetési időszak” (a rögzített okt. 1. nem a vetés napja),
  „termésév kezdete”, „termésév eleje”, „termésév eddig”.
- A „termésév” csak a betakarítás évét jelentheti („2027. évi termés”).
- Nincs gondolatjel a prózában, nincs túlmagyarázás, nincs „a lap magától kihagyja”
  jellegű, gépi készítésre utaló mondat.
- Helyezésnél „a 27 év leggyengébb éve” (nem „1. leggyengébb”), „az 5.”.
- Forrásjelölés szó szerint: „Adatbázis: Meteorológiai Adattár, HungaroMet
  Nonprofit Zrt. átlagolva az egyes értékekre”. Az állomásátlag közlése eldöntött
  kérdés (EU 2023/138 nagy értékű adatkészlet-rendelet, 2024. június 9. óta), nem
  kell újra felvetni.

## 4. Egyeztetés

- Ugyanaz a mérőszám mindenhol ugyanazzal az értékkel szerepeljen (PDF, web,
  fogalomtár), például a tipikus tévedés.
- Az ár feliratában a tényleges árperiódus álljon (a JSON value.note mezője
  szerint), ne egy hónapnév, amely csak részben fedi.

- Ha két forrás ugyanarra a számra 10%-nál jobban eltér (pl. modell és EU, EU és
  KSH, modell és állomás azonos ablakon), az okot meg kell keresni és igazolni.
  Hihető magyarázatot ellenőrzés nélkül nem írunk le.
- Friss hírek (NAK aratási jelentés, AKI, KSH gyorstájékoztató) ellenpróbának
  jók, de főszámként csak gépileg letölthető hivatalos forrás szerepelhet.

## Nyitott kérdés (döntés a felhasználóé, ne javítsd magadtól)

- Őszi árpa: az EU csak az összes árpát közli, ezért ha az árpa szezonja lezárult,
  de a KSH még nem közölt, nincs EU-főszám. Jelezd, ha ez a helyzet előáll.

## 5. Amihez az ellenőr nem nyúlhat

- A modellhez (src/model.py, src/features.py, src/build_panel.py, src/config.py
  modellbeállításai, data/raw, data/processed) csak mérési kapuval lehet nyúlni:
  ezt csak javasolhatja, nem javíthatja.
- A main ágra csak akkor commitolhat, ha a HETI_ELLENOR.md „Mikor frissíthetsz
  közvetlenül” feltételei mind teljesülnek; minden más javítás pull request, a
  döntés a felhasználóé. A `.github/workflows/` mappához és a generált fájlokhoz
  (`web/data/`) nem nyúlhat.
