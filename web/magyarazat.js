/* Egységes fogalomtár + kattintható magyarázat-doboz.
   Ez az EGYETLEN forrása minden adat-magyarázatnak: a térkép-oldal, a
   táblázat-oldal és a magyarazat.html is ebből dolgozik (később a PDF
   lábjegyzetei is). A szövegek szakmailag pontosak, de köznyelviek. */

const GLOSSARY = {
  becsles: {
    title: "Becslés (t/ha)",
    text: "A várható termésátlag tonna/hektárban, vármegyénként. A szoftver a KSH " +
      "2000 óta mért vármegyei hozamai és az ugyanott mért időjárás (hőmérséklet, " +
      "csapadék, párolgás) közti, statisztikailag kimutatott összefüggésekből számolja, " +
      "mit ígér az idei szezon időjárása. Nem hivatalos adat — statisztikai becslés; " +
      "a pontos számítás a lap alján, a Szakmai leírásban.",
  },
  szokasos: {
    title: "Eltérés a szokásostól (%)",
    text: "A „szokásos” az adott vármegye sokéves, emelkedő pályája: a hozamok a " +
      "jobb fajták és technológia miatt évről évre nőnek, ezért nem a múltbeli átlaghoz, " +
      "hanem ehhez a növekvő szinthez viszonyítunk. A −9% azt jelenti: ha az idei év " +
      "„átlagos” lenne, ennyivel többet várnánk.",
  },
  tartomany: {
    title: "Várható tartomány",
    text: "A becslés bizonytalansági sávja: 10-ből 8 esetben ebbe a tartományba esik " +
      "a végleges hozam (80%-os valószínűségi sáv). A sáv a modell múltbeli, " +
      "csak-múltból-jósolt tévedéseinek tényleges eloszlásából számolódik, " +
      "vármegyénként eltérő szélességgel (az ingadozóbb megyékben szélesebb), és " +
      "aszimmetrikus lehet: az aszályos lehúzás jellemzően nagyobb, mint a felfelé " +
      "meglepetés. Szezon közben a hátralévő időjárás bizonytalansága is hozzáadódik.",
  },
  forgatokonyvek: {
    title: "Mi lehet még belőle? (forgatókönyvek)",
    text: "Amíg a szezon tart, a hátralévő heteket 26 korábbi év TÉNYLEGES időjárásával " +
      "játsszuk végig — mintha az idei év innentől úgy folytatódna, mint 2003-ban, " +
      "2010-ben stb. A sáv két széle a kedvezőtlen és a kedvező kimenet (a 26 " +
      "lejátszásból a leggyengébb és legerősebb 10-10%-a), a vonal a középső, " +
      "legvalószínűbb kimenet, a ▲ a mostani becslés.",
  },
  helyezes: {
    title: "Hol áll ez az elmúlt évek közt?",
    text: "Minden szürke pötty egy-egy év 2000 óta: mennyivel tért el akkor a termés " +
      "a szokásos szinttől. A színes, nagyobb pötty az idei becslés. A szaggatott " +
      "függőleges vonal a szokásos szint (0%) — ettől balra a gyenge, jobbra a jó évek. " +
      "Ha a pötty fölé viszi az egeret, az évszám is megjelenik.",
  },
  ertek: {
    title: "Termelési érték (mrd Ft)",
    text: "Várható hozam × betakarított terület × termelői ár, milliárd forintban. " +
      "A terület a legutóbbi lezárt KSH-évből, az ár a legutolsó hivatalos Eurostat " +
      "termelői átlagár — a tényleges bevétel az idei ártól és területtől függ, ezért " +
      "ez nagyságrendi, „körülbelül” szám. A „kiesés/többlet a szokásoshoz” ugyanez " +
      "a számítás a szokásos szinttel vetve össze.",
  },
  trendalapu: {
    title: "Trend-alapú becslés (napraforgó, repce)",
    text: "Ezeknél a terményeknél visszaméréssel kimutattuk, hogy az idei " +
      "időjárás statisztikailag NEM javítja a becslést: a napraforgó időjárás-" +
      "tűrő, a repce ingadozását pedig kifagyás, kártevők és a vetésterület " +
      "változása mozgatja, amit a hőmérséklet és a csapadék nem lát. Ezért nem " +
      "„időjárás-modellt”, hanem a sokéves TRENDET közöljük (a vármegyék eltérő " +
      "szintje + az évről évre emelkedő pálya). Ez validált, számszerű " +
      "bizonytalansággal járó becslés — csak nem használ idei időjárás-jelet, " +
      "ezért nincs nála „a szokásostól való eltérés” és szezonközi forgatókönyv.",
  },
  tevedes: {
    title: "A becslés tipikus tévedése",
    text: "Visszamértük a modellt 2011-től évről évre úgy, hogy mindig CSAK a " +
      "korábbi évekből jósolt — pontosan úgy, ahogy élesben is dolgozik. A ±X% " +
      "ennek a tévedésnek a tipikus mértéke; szigorúbb (és nagyobb) szám, mint a " +
      "korábban közölt, de erre lehet üzleti döntést alapozni. A kukoricánál " +
      "nagyobb, mert az érzékenyebb a nyári időjárásra.",
  },
  csapadek: {
    title: "Csapadék (mm)",
    text: "A termésév eddig lehullott összes csapadéka milliméterben, a vármegye " +
      "középpontjára számolva (ERA5 időjárási elemzés). 1 mm = 1 liter víz " +
      "négyzetméterenként.",
  },
  vizmerleg: {
    title: "Vízmérleg (mm)",
    text: "Csapadék MÍNUSZ párolgás (a növényzet és a talaj vízigénye, FAO-módszerrel " +
      "számolva). A −250 mm azt jelenti: negyed méternyi vízoszloppal több párolgott " +
      "el, mint amennyi eső esett — ekkora a hiány. Magyarországon nyáron szinte " +
      "mindig negatív; a kérdés a hiány MÉRTÉKE. Ez a modell legfontosabb " +
      "aszály-jelzője.",
  },
  hostressz: {
    title: "Hőstressznapok",
    text: "Hány napon volt a csúcshőmérséklet a károsodási küszöb felett a termény " +
      "legérzékenyebb időszakában: búzánál 30 °C felett a szemtelítődés alatt " +
      "(május–június közepe), őszi árpánál ugyanez korábban (április vége–június " +
      "eleje), kukoricánál 32 °C felett a virágzás idején (július). Ilyenkor a " +
      "növény a hőség miatt kevesebb és apróbb szemet nevel.",
  },
  fagynapok: {
    title: "Téli fagynapok",
    text: "Hány napon süllyedt a minimum-hőmérséklet −15 °C alá december és február " +
      "között. A hótakaró nélküli kemény fagy kifagyaszthatja az ősszel vetett " +
      "búzát és árpát. A kukoricánál nem értelmezhető (tavaszi vetés), ott nem " +
      "jelenik meg.",
  },
  hoosszeg: {
    title: "Hőösszeg (GDD)",
    text: "A napi középhőmérsékletek összege a szezon kezdete óta (fok×nap, 0 °C " +
      "felett számolva; angolul GDD). A növény fejlődésének „üzemanyag-mérője”: " +
      "minél több gyűlik, annál előrébb tart a kalászolás/érés. Önmagában se nem jó, " +
      "se nem rossz — a többi vármegyéhez és az évszakhoz képest érdemes nézni.",
  },
  termesev: {
    title: "Termésév",
    text: "A termés betakarításának éve. Az őszi vetésű terményeknél (búza, őszi árpa) " +
      "a hozzá tartozó időjárás az ELŐZŐ ősszel kezdődik: a 2026-os termésév a 2025. " +
      "október 1. – 2026. június 30. időjárását jelenti. A kukoricánál minden az adott " +
      "naptári évben történik (április–szeptember).",
  },
  idojaras_eddig: {
    title: "Időjárási adat / „eddig”",
    text: "A mutatók a szezonból eddig eltelt, ténylegesen megfigyelt napokból " +
      "számolódnak (plusz legfeljebb 7 nap meteorológiai előrejelzés). A szezon " +
      "hátralévő részét a becslésben a korábbi évek időjárása képviseli — ez a " +
      "„még változhat” rész.",
  },
  frissites: {
    title: "Frissítés",
    text: "A rendszer minden reggel automatikusan letölti a legfrissebb időjárási " +
      "adatokat, újraszámolja a becslést mindhárom terményre, és eltárolja az aznapi " +
      "állapotot — az idővonal-csúszkán visszanézhető, hogyan mozgott a becslés a " +
      "szezon során.",
  },
  budapest: {
    title: "Budapest — miért nincs becslés?",
    text: "Budapest termőterülete elhanyagolható (a búzánál az országos terület kevesebb " +
      "mint 0,1%-a), a kevés tábla hozama pedig évről évre szeszélyesen ingadozik. " +
      "Egy megbízhatatlan becslés helyett inkább nem adunk becslést; az időjárási " +
      "adatok Budapestre is látszanak.",
  },
  modszertan: {
    title: "Hogyan készül a becslés? (módszertan dióhéjban)",
    text: "1) A KSH 2000 óta mért vármegyei termésátlagai + az ERA5 időjárási " +
      "adatbázis napi adatai vármegyénként. 2) Az időjárásból a növény szempontjából " +
      "fontos mutatókat számolunk (hőösszeg, hőstressz, fagy, vízmérleg — a fejlődési " +
      "szakaszokra bontva). 3) Regressziós modell számszerűsíti, hogy e mutatók " +
      "egy-egy egységnyi változása átlagosan mennyivel mozdította el a hozamot a 26 év " +
      "vármegyei adataiban. 4) Az idei szezon mutatóit behelyettesítve kapjuk a " +
      "becslést. A modellt minden évre visszamértük: az aszályéveket (2003, 2007, " +
      "2012, 2022) iránytartóan jelezte előre. Részletek: Szakmai leírás a lap alján.",
  },
};

/* Kattintható magyarázat-doboz. Használat: a HTML-be
   <button class="info-btn" data-explain="kulcs" aria-label="Magyarázat">ⓘ</button>
   kerül; a doboz megnyitását ez a modul kezeli (esemény-delegálással). */
(function () {
  function ensureBox() {
    let box = document.getElementById("explain-box");
    if (box) return box;
    box = document.createElement("div");
    box.id = "explain-box";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "false");
    box.innerHTML = '<button id="explain-close" aria-label="Bezárás">×</button>' +
      '<h3 id="explain-title"></h3><p id="explain-text"></p>' +
      '<a id="explain-more" href="magyarazat.html">Minden magyarázat egy oldalon →</a>';
    document.body.appendChild(box);
    box.querySelector("#explain-close").addEventListener("click", hide);
    document.addEventListener("keydown", e => { if (e.key === "Escape") hide(); });
    return box;
  }
  function hide() {
    const box = document.getElementById("explain-box");
    if (box) box.classList.remove("open");
  }
  function show(key) {
    const g = GLOSSARY[key];
    if (!g) return;
    const box = ensureBox();
    box.querySelector("#explain-title").textContent = g.title;
    box.querySelector("#explain-text").textContent = g.text;
    box.classList.add("open");
  }
  // capture-fázis: az ⓘ kattintás ne érje el a szülő elemeket (pl. a táblázat
  // rendező fejlécét), különben a magyarázat mellett rendezne is
  document.addEventListener("click", e => {
    const btn = e.target.closest(".info-btn");
    if (btn && btn.dataset.explain) {
      e.preventDefault();
      e.stopPropagation();
      show(btn.dataset.explain);
    }
  }, true);
  window.GLOSSARY = GLOSSARY;
  window.showExplain = show;
})();
