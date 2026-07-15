/* Búzahozam-előrejelző térkép.
   Statikus: a legenerált forecast.json-t és a history snapshotokat olvassa,
   semmilyen szerveroldali komponens vagy böngészőtárolás nincs. */

const COLORS = {
  scale: [
    [-20, "#b03a2e"],
    [-10, "#e67e22"],
    [0,   "#f5e8c8"],
    [10,  "#82c07a"],
    [20,  "#1e8449"],
  ],
  noData: "#d5d8dc",
  border: "#7f8c8d",
};

/* Térképrétegek. Az időjárási rétegeknél NINCS fix skála: az aktuális
   vármegye-értékek min-max tartományát színezzük (relatív, országon belüli
   összevetés), a jelmagyarázat a tényleges min/max értéket mutatja. */
const LAYERS = {
  anomaly: { note: "eltérés a szokásos hozamtól (piros = elmaradás, kék = többlet)",
             unit: "%", fixed: [-20, 20],
             // piros–kék divergens: színtévesztő-biztos (a piros–zöld nem az)
             colors: ["#b03a2e", "#e67e22", "#f5e8c8", "#7fb3d5", "#2874a6"] },
  wb:   { note: "vízmérleg (csapadék − párolgás), termésév eddig", unit: " mm",
          colors: ["#b03a2e", "#e8c78f", "#7fb3d5", "#2874a6"] },
  prec: { note: "csapadékösszeg, termésév eddig", unit: " mm",
          colors: ["#e8c78f", "#a9cce3", "#5499c7", "#1a5276"] },
  heat: { note: "hőstressznapok a kritikus ablakban", unit: " nap",
          colors: ["#f5e8c8", "#e67e22", "#b03a2e", "#78281f"] },
  gdd:  { note: "hőösszeg (GDD, 0 °C bázis), termésév eddig", unit: "",
          colors: ["#fdf2d0", "#f5b041", "#dc7633", "#a04000"] },
};
let currentLayer = "anomaly";

let map, geojson, historyDates = [], currentForecast = null;
let selectedId = null;
let crop = "wheat";
const yieldHistory = {};  // crop -> yield_history JSON (cache)

async function fetchJson(url, cacheBust = false) {
  // A GitHub Pages CDN ~10 percig cache-el; a naponta frissülő fájlokhoz napi
  // cache-törő paramétert teszünk (a history snapshotok változatlanok, oda nem kell).
  if (cacheBust) url += `?v=${new Date().toISOString().slice(0, 10)}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

// kattintható magyarázat-gomb (a fogalomtár kulcsával — lásd magyarazat.js)
function info(key) {
  return `<button class="info-btn" data-explain="${key}"
    aria-label="Magyarázat megnyitása">i</button>`;
}

// magyar tizedesvessző a kijelzett számokhoz (a JSON-ban pont marad)
function hu(v, d = 2) { return v.toFixed(d).replace(".", ","); }

// HTML-escape a JSON-ból érkező szövegekhez (védelem a template-interpolációnál)
function esc(s) {
  return String(s).replace(/[&<>"']/g, ch =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

// betöltési sorszám: a gyors termény-váltásnál a megkésett válasz eldobásához
let loadSeq = 0;

/* A térkép betöltési/hibaállapotának vezérlése — a szürke üres doboz helyett
   látható visszajelzés. state: "loading" | "error" | "hidden". */
function setMapStatus(state, message) {
  const box = document.getElementById("map-status");
  if (!box) return;
  if (state === "hidden") { box.hidden = true; return; }
  box.hidden = false;
  box.dataset.state = state;
  const txt = document.getElementById("map-status-text");
  if (txt && message) txt.textContent = message;
}

function layerValues(fc) {
  /* Rétegenkénti érték vármegyénként. Az anomáliánál Budapest = null (nincs
     modell); az időjárási rétegeknél mind a 20 egységre van érték. */
  const out = {};
  for (const c of fc.counties) {
    out[c.nuts_id] = {
      anomaly: c.predicted_yield_t_ha === null ? null : c.anomaly_pct,
      wb: c.weather_todate.wb_total_mm,
      prec: c.weather_todate.prec_total_mm,
      heat: c.weather_todate.heat_days,
      gdd: c.weather_todate.gdd_total,
    };
  }
  return out;
}

function paintForLayer(layer, fc) {
  const spec = LAYERS[layer];
  const vals = Object.values(layerValues(fc)).map(v => v[layer])
    .filter(v => v !== null && v !== undefined);
  let [lo, hi] = spec.fixed || (vals.length
    ? [Math.min(...vals), Math.max(...vals)]
    : [0, 1]);  // üres réteg — ne legyen Infinity/NaN a skálában
  if (lo === hi) { lo -= 1; hi += 1; }  // konstans réteg (pl. 0 hőstressznap)

  const interp = ["interpolate", ["linear"], ["feature-state", "v_" + layer]];
  spec.colors.forEach((c, i) =>
    interp.push(lo + (hi - lo) * i / (spec.colors.length - 1), c));
  const expr = ["case",
    ["==", ["feature-state", "has_" + layer], false], COLORS.noData, interp];

  // jelmagyarázat frissítése a TÉNYLEGES tartománnyal
  const fmt = v => spec.unit === "%"
    ? (v > 0 ? "+" : "") + v + "%"
    : Math.round(v).toLocaleString("hu-HU") + spec.unit;
  document.getElementById("legend-min").textContent = fmt(lo);
  document.getElementById("legend-max").textContent = fmt(hi);
  let note = spec.note;
  if (layer === "wb" && hi < 0) {
    note += " — idén mindenhol hiány; a kék a KISEBB hiányt jelenti";
  }
  document.getElementById("legend-note").textContent = note;
  const LAYER_EXPLAIN = { anomaly: "szokasos", wb: "vizmerleg", prec: "csapadek", heat: "hostressz", gdd: "hoosszeg" };
  const legendInfo = document.getElementById("legend-info");
  if (legendInfo) legendInfo.dataset.explain = LAYER_EXPLAIN[layer];
  document.getElementById("legend-bar").style.background =
    `linear-gradient(to right, ${spec.colors.join(", ")})`;
  return expr;
}

function applyForecast(fc) {
  currentForecast = fc;
  setMapStatus("hidden");  // az első adat megjött — az overlay eltűnhet
  const vals = layerValues(fc);
  for (const f of geojson.features) {
    const id = f.properties.NUTS_ID;
    const v = vals[id] || {};
    const state = {};
    for (const key of Object.keys(LAYERS)) {
      const has = v[key] !== null && v[key] !== undefined;
      state["has_" + key] = has;
      state["v_" + key] = has ? v[key] : 0;
    }
    map.setFeatureState({ source: "counties", id }, state);
  }
  map.setPaintProperty("counties-fill", "fill-color", paintForLayer(currentLayer, fc));
  const wxNote = fc.scenarios
    ? `időjárási adat (mért + 7 napos előrejelzés): ${fc.weather_known_until}-ig`
    : `az időjárási adat a teljes szezont lefedi`;
  document.getElementById("meta").textContent =
    `${fc.crop} · ${fc.crop_year}-es termésév · frissítve: ${fc.updated_at} · ${wxNote}`;
  // a PDF-link napi cache-törő paramétert kap, hogy sose régi (cache-elt)
  // jelentés nyíljon meg
  const pdfLink = document.getElementById("pdf-link");
  if (pdfLink) pdfLink.href = "data/jelentes_latest.pdf?d=" +
    encodeURIComponent(fc.updated_at || "");
  renderHeadline(fc);
  renderNational(fc);
  if (selectedId) showPanel(selectedId);
}

/* Vezetői headline: kimondott üzenet a számok helyett (infografikai review).
   A mondat a national blokk mezőiből áll össze — ugyanez a sablon szolgálja
   majd a napi PDF-jelentés fejlécét is. */
function renderHeadline(fc) {
  const el = document.getElementById("headline");
  const n = fc.national;
  if (!n) { el.innerHTML = ""; return; }
  const a = n.anomaly_pct, v = n.value, sc = fc.scenarios;
  const cap = fc.crop.charAt(0).toUpperCase() + fc.crop.slice(1);

  let main;
  if (a <= -3) {
    main = `A ${esc(fc.crop)} idei termése <b>${hu(n.predicted_yield_t_ha)} t/ha</b> körül
      várható, ami <b>${hu(Math.abs(a), 1)}%-kal marad el a sokéves szokásos
      szinttől</b>` +
      (v ? ` — a ${v.price_year}-es árakon számolva ez kb.
       <b>${Math.round(Math.abs(v.trend_gap_bn_huf))} mrd Ft kiesést jelent</b>.` : ".");
  } else if (a >= 3) {
    main = `A ${esc(fc.crop)} idei termése <b>${hu(n.predicted_yield_t_ha)} t/ha</b> körül
      várható, <b>${hu(a, 1)}%-kal a sokéves szokásos szint felett</b>` +
      (v ? ` — a ${v.price_year}-es árakon számolva ez kb.
       <b>${Math.round(v.trend_gap_bn_huf)} mrd Ft többletet jelent</b>.` : ".");
  } else {
    main = `A ${esc(fc.crop)} idei termése a sokéves szokásos szint közelében,
      <b>${hu(n.predicted_yield_t_ha)} t/ha</b> körül várható
      (${a > 0 ? "+" : ""}${hu(a, 1)}%) —
      érdemi kiesés vagy többlet egyelőre nem látszik.`;
  }

  const clauses = [];
  // ellentmondó előjelek feloldása (pl. kukorica: rossz tavalyi év + gyenge trend)
  if (a < 0 && n.yoy_pct >= 3) {
    clauses.push(`Tavalyhoz (${n.prev_year}) képest ez
      +${hu(n.yoy_pct, 1)}%-os javulás, a megszokott szinttől azonban elmarad.`);
  } else if (a > 0 && n.yoy_pct <= -3) {
    clauses.push(`Tavalyhoz (${n.prev_year}) képest ${hu(n.yoy_pct, 1)}%
      a visszaesés, a termés azonban így is a megszokott szint felett alakul.`);
  }
  if (n.rank_from_worst <= 5) {
    clauses.push(`Ha így marad, a ${n.rank_total} év
      ${n.rank_from_worst}. leggyengébb éve lenne.`);
  } else if (n.rank_from_worst >= n.rank_total - 4) {
    clauses.push(`Ha így marad, a ${n.rank_total} év
      ${n.rank_total - n.rank_from_worst + 1}. legerősebb éve lenne.`);
  }

  const cert = sc
    ? `<span class="badge open">MÉG VÁLTOZHAT</span> A szezonból még
       <b>${sc.remaining_days} nap</b> van hátra; a végeredmény az időjárástól
       függően <b>${hu(sc.national.p10)}–${hu(sc.national.p90)} t/ha</b> között
       alakulhat.`
    : `<span class="badge final">VÉGLEGES KÖZELI</span> A szezon időjárása már
       teljes egészében ismert, a becslés érdemben nem változik.`;
  const err = n.model_error_pct
    ? ` A becslés tipikus tévedése a múltbeli visszamérések alapján
       ±${hu(n.model_error_pct, 1)}%. ${info("tevedes")}`
    : "";

  el.innerHTML = `
    <div class="headline-main">${main} ${clauses.join(" ")}</div>
    <div class="headline-sub">${cert}${err}</div>`;
}

/* Delta-chip: színezett +/- badge */
function chip(v, suffix, inverse = false) {
  const neg = v < 0;
  const cls = (inverse ? !neg : neg) ? "chip neg" : "chip pos";
  return `<span class="${cls}">${v > 0 ? "+" : ""}${hu(v, 1)}${suffix}</span>`;
}

/* Percentilis pöttysor: a történelmi trend-anomáliák pontokként, az idei kiemelve.
   Az anomáliák a yield_history national blokkjából számolódnak kliensoldalon —
   ugyanazzal a trenddel, amivel a szerveroldal (trend_slope/intercept). */
function rankStripSVG(fc) {
  const hist = yieldHistory[crop];
  if (!hist) return "";
  const nat = hist.national;
  const anoms = nat.years.map((y, i) => {
    const tr = nat.trend_intercept + nat.trend_slope * (y - nat.trend_base_year);
    return { y, a: 100 * (nat.yields[i] - tr) / tr };
  });
  const cur = fc.national.anomaly_pct;
  const all = anoms.map(d => d.a).concat([cur]);
  const lo = Math.min(...all), hi = Math.max(...all);
  const W = 210, H = 30, P = 8;
  const X = a => P + (a - lo) / (hi - lo || 1) * (W - 2 * P);
  let g = `<line x1="${P}" y1="${H/2}" x2="${W-P}" y2="${H/2}" stroke="#dfe4e8"/>`;
  if (lo < 0 && hi > 0) {
    g += `<line x1="${X(0)}" y1="6" x2="${X(0)}" y2="${H-6}" stroke="#b8c2ca" stroke-dasharray="2,2"/>`;
  }
  for (const d of anoms) {
    g += `<circle cx="${X(d.a)}" cy="${H/2}" r="3" fill="#aab8c2" opacity="0.75">` +
         `<title>${d.y}: ${d.a > 0 ? "+" : ""}${hu(d.a, 1)}%</title></circle>`;
  }
  const curCol = cur < 0 ? "#c0392b" : "#1e8449";
  g += `<circle cx="${X(cur)}" cy="${H/2}" r="5.5" fill="${curCol}" stroke="#fff" stroke-width="1.5">` +
       `<title>${fc.crop_year}: ${cur > 0 ? "+" : ""}${hu(cur, 1)}%</title></circle>`;
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img"
           aria-label="Az idei anomália a történelmi évek közt">${g}</svg>`;
}

/* Szcenárió-szalag: P10–P90 sáv, P50-jel, becslés-marker */
function scenarioBandSVG(p10, p50, p90, point, width = 210) {
  const all = [p10, p90, point];
  const lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.15 || 0.5;
  const W = width, H = 34, P = 6;
  const X = v => P + (v - (lo - pad)) / ((hi + pad) - (lo - pad)) * (W - 2 * P);
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img"
           aria-label="Időjárás-forgatókönyvek sávja">
    <rect x="${X(p10)}" y="10" width="${X(p90) - X(p10)}" height="10" rx="5"
          fill="#7fb3d5" opacity="0.45"/>
    <line x1="${X(p50)}" y1="7" x2="${X(p50)}" y2="23" stroke="#2874a6" stroke-width="2"/>
    <path d="M ${X(point)} 6 l 5 8 l -10 0 z" fill="#c0392b"/>
    <text x="${X(p10)}" y="33" font-size="9" fill="#6e7f8b" text-anchor="middle">${hu(p10, 1)}</text>
    <text x="${X(p90)}" y="33" font-size="9" fill="#6e7f8b" text-anchor="middle">${hu(p90, 1)}</text>
  </svg>`;
}

function renderNational(fc) {
  const el = document.getElementById("national");
  const n = fc.national;
  if (!n) { el.innerHTML = ""; return; }
  const sc = fc.scenarios;
  const v = n.value;

  const cards = [];
  cards.push(`
    <div class="kpi">
      <div class="kpi-label">Országos becslés · ${fc.crop_year} ${info("becsles")}</div>
      <div class="kpi-value">${hu(n.predicted_yield_t_ha)} <small>t/ha</small></div>
      <div class="kpi-sub">${chip(n.anomaly_pct, "%")} a szokásoshoz ${info("szokasos")} ·
        ${chip(n.yoy_pct, "%")} vs ${n.prev_year}</div>
    </div>`);
  cards.push(`
    <div class="kpi">
      <div class="kpi-label">Hol áll ez az elmúlt évek közt? ${info("helyezes")}</div>
      <div class="kpi-viz">${rankStripSVG(fc)}</div>
      <div class="kpi-sub">${n.rank_total} évből a ${n.rank_from_worst}. leggyengébb · a szaggatott vonal a szokásos szint</div>
    </div>`);
  if (sc) {
    cards.push(`
      <div class="kpi" title="${esc(sc.method)}">
        <div class="kpi-label">Mi lehet még belőle? (${sc.remaining_days} nap hátra) ${info("forgatokonyvek")}</div>
        <div class="kpi-viz">${scenarioBandSVG(sc.national.p10, sc.national.p50,
                                               sc.national.p90, n.predicted_yield_t_ha)}</div>
        <div class="kpi-sub">▲ mostani becslés · vonal: legvalószínűbb kimenet · a sáv széle: kedvezőtlen/kedvező időjárás</div>
      </div>`);
  }
  if (v) {
    cards.push(`
      <div class="kpi" title="${esc(v.note)}">
        <div class="kpi-label">Termelési érték ${info("ertek")}</div>
        <div class="kpi-value">~${Math.round(v.production_value_bn_huf)} <small>mrd Ft</small></div>
        <div class="kpi-sub">${chip(v.trend_gap_bn_huf, " mrd Ft")} ${v.trend_gap_bn_huf < 0 ? "kiesés" : "többlet"} a szokásoshoz ·
          a legutolsó hivatalos áron (${v.price_year}): ${hu(v.price_huf_per_t / 1000, 1)} eFt/t</div>
      </div>`);
  }
  el.innerHTML = cards.join("");
}

/* Kézi SVG vonaldiagram: historikus hozamok + idei becslés sávval.
   Semmi külső könyvtár — statikus oldal marad. */
function yieldChartSVG(years, yields, cur) {
  if (!years || !years.length || !yields || years.length !== yields.length) {
    return "";  // nincs/inkonzisztens idősor — ne rajzoljunk NaN-koordinátákat
  }
  const W = 268, H = 130, PL = 30, PR = 8, PT = 8, PB = 18;
  const allY = yields.concat(cur ? [cur.low, cur.high] : []);
  const allX = years.concat(cur ? [cur.year] : []);
  const y0 = Math.floor(Math.min(...allY) * 2) / 2, y1 = Math.ceil(Math.max(...allY) * 2) / 2;
  const x0 = Math.min(...allX), x1 = Math.max(...allX);
  const X = yr => PL + (yr - x0) / (x1 - x0) * (W - PL - PR);
  const Y = v => PT + (y1 - v) / (y1 - y0) * (H - PT - PB);

  let g = "";
  // y-tengely rácsok (3 osztás)
  for (let i = 0; i <= 2; i++) {
    const v = y0 + (y1 - y0) * i / 2;
    g += `<line x1="${PL}" y1="${Y(v)}" x2="${W - PR}" y2="${Y(v)}" stroke="#eceff1"/>` +
         `<text x="${PL - 4}" y="${Y(v) + 3}" text-anchor="end" font-size="9" fill="#6e7f8b">${hu(v, 1)}</text>`;
  }
  // vármegye-trendvonal (legkisebb négyzetek, kliensoldalon)
  const nY = years.length;
  const mx = years.reduce((a, b) => a + b, 0) / nY;
  const my = yields.reduce((a, b) => a + b, 0) / nY;
  let sxy = 0, sxx = 0;
  for (let i = 0; i < nY; i++) { sxy += (years[i] - mx) * (yields[i] - my); sxx += (years[i] - mx) ** 2; }
  const slope = sxx ? sxy / sxx : 0, icept = my - slope * mx;
  const clampY = v => Math.max(y0, Math.min(y1, v));
  g += `<line x1="${X(x0)}" y1="${Y(clampY(icept + slope * x0))}"
         x2="${X(x1)}" y2="${Y(clampY(icept + slope * x1))}"
         stroke="#b8c2ca" stroke-width="1" stroke-dasharray="4,3"/>`;
  // historikus vonal + pontok
  const pts = years.map((yr, i) => `${X(yr)},${Y(yields[i])}`).join(" ");
  g += `<polyline points="${pts}" fill="none" stroke="#5d7a94" stroke-width="1.4"/>`;
  // idei becslés: sáv + pont
  if (cur) {
    g += `<line x1="${X(cur.year)}" y1="${Y(cur.low)}" x2="${X(cur.year)}" y2="${Y(cur.high)}"
           stroke="#c0392b" stroke-width="2" opacity="0.45"/>` +
         `<circle cx="${X(cur.year)}" cy="${Y(cur.value)}" r="3.2" fill="#c0392b"/>`;
  }
  // x-tengely címkék
  g += `<text x="${X(x0)}" y="${H - 4}" font-size="9" fill="#6e7f8b">${x0}</text>` +
       `<text x="${X(x1)}" y="${H - 4}" text-anchor="end" font-size="9" fill="#6e7f8b">${x1}</text>`;
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"
           role="img" aria-label="Hozam idősor">${g}</svg>`;
}

function showPanel(nutsId) {
  const c = currentForecast.counties.find(x => x.nuts_id === nutsId);
  if (!c) return;
  selectedId = nutsId;
  document.getElementById("panel-name").textContent = c.county_name;
  const body = document.getElementById("panel-body");
  const wx = c.weather_todate;
  // "pötty a pályán": hol áll a vármegye értéke a 20 egység tartományában
  const TRACK_KEYS = {
    "Csapadék": "prec_total_mm", "Vízmérleg": "wb_total_mm",
    "Hőstressznapok": "heat_days", "Fagynapok": "frost_days_winter",
    "Hőösszeg (GDD)": "gdd_total",
  };
  function trackRow(label, value, unit, color, key) {
    if (value === null || value === undefined) return "";
    const all = currentForecast.counties
      .map(x => x.weather_todate[TRACK_KEYS[label]])
      .filter(x => x !== null && x !== undefined);
    const lo = Math.min(...all), hi = Math.max(...all);
    const pct = hi === lo ? 50 : 100 * (value - lo) / (hi - lo);
    const fmtV = Number.isInteger(value) ? value : hu(value, 1);
    return `
      <div class="track-row" title="országos tartomány: ${Math.round(lo)} … ${Math.round(hi)}">
        <span class="track-label">${label} ${info(key)}</span>
        <span class="track"><span class="track-dot" style="left:${pct.toFixed(1)}%;background:${color}"></span></span>
        <span class="track-value">${fmtV}${unit}</span>
      </div>`;
  }
  const wxRows = `
    <div class="chart-title">Időjárás eddig — a pötty: hol áll a vármegye a 20 közül
    (bal = legalacsonyabb, jobb = legmagasabb érték)</div>
    ${trackRow("Csapadék", wx.prec_total_mm, " mm", "#5499c7", "csapadek")}
    ${trackRow("Vízmérleg", wx.wb_total_mm, " mm", "#2874a6", "vizmerleg")}
    ${trackRow("Hőstressznapok", wx.heat_days, "", "#c0392b", "hostressz")}
    ${trackRow("Fagynapok", wx.frost_days_winter, "", "#8e44ad", "fagynapok")}
    ${trackRow("Hőösszeg (GDD)", wx.gdd_total, "", "#d68910", "hoosszeg")}`;

  // hozamgrafikon a cache-elt idősorból
  let chart = "";
  const hist = yieldHistory[crop];
  const hc = hist && hist.counties[nutsId];
  if (hc) {
    const cur = c.predicted_yield_t_ha === null ? null : {
      year: currentForecast.crop_year, value: c.predicted_yield_t_ha,
      low: c.low, high: c.high,
    };
    chart = `<div class="chart-title">Hozam ${hc.years[0]}–${hc.years[hc.years.length - 1]}
             (piros: idei becslés a sávval)</div>` +
            yieldChartSVG(hc.years, hc.yields, cur);
  }
  if (c.predicted_yield_t_ha === null) {
    body.innerHTML = `<p class="note">${esc(c.note || "Nincs becslés.")} ${info("budapest")}</p>${chart}${wxRows}`;
  } else {
    const cls = c.anomaly_pct < 0 ? "neg" : "pos";
    const sign = c.anomaly_pct > 0 ? "+" : "";
    const scc = currentForecast.scenarios
      && currentForecast.scenarios.counties[nutsId];
    const scRow = scc
      ? `<div class="band" title="${esc(currentForecast.scenarios.method)}">
         ha az időjárás a betakarításig kedvezőtlen: ${hu(scc.p10)}, ha kedvező: ${hu(scc.p90)} t/ha ${info("forgatokonyvek")}</div>
         <div class="kpi-viz">${scenarioBandSVG(scc.p10, scc.p50, scc.p90,
                                                c.predicted_yield_t_ha, 250)}</div>`
      : "";
    body.innerHTML = `
      <div class="big-number">${hu(c.predicted_yield_t_ha)} t/ha</div>
      <div class="band" title="80%-os valószínűségi sáv${currentForecast.scenarios ? ' — a modell hibája és a hátralévő időjárás bizonytalansága együtt' : ''}">Várható tartomány: ${hu(c.low)} – ${hu(c.high)} t/ha — 10-ből 8 esetben ebbe esik ${info("tartomany")}</div>
      ${scRow}
      <div class="anomaly ${cls}">${sign}${hu(c.anomaly_pct, 1)}% a szokásoshoz képest ${info("szokasos")}</div>
      ${c.value_bn_huf !== undefined ? `<div class="band">termelési érték: ~${hu(c.value_bn_huf, 1)} mrd Ft
        (${c.trend_gap_bn_huf < 0 ? "kiesés" : "többlet"} a szokásoshoz: ${hu(Math.abs(c.trend_gap_bn_huf), 1)} mrd Ft) ${info("ertek")}</div>` : ""}
      ${chart}${wxRows}`;
  }
  document.getElementById("panel").classList.remove("hidden");
}

function setupTimeline() {
  const tl = document.getElementById("timeline");
  const slider = document.getElementById("slider");
  const label = document.getElementById("slider-date");
  if (historyDates.length < 2) {  // egy nappal nincs mit csúsztatni
    tl.classList.add("hidden");
    return;
  }
  tl.classList.remove("hidden");
  slider.max = historyDates.length - 1;
  slider.value = historyDates.length - 1;
  label.textContent = historyDates[historyDates.length - 1];
}

document.getElementById("slider").addEventListener("input", async e => {
  const seq = loadSeq;             // pillanatkép: melyik termény-betöltéshez tartozunk
  const cropNow = crop;
  const d = historyDates[Number(e.target.value)];
  if (!d) return;
  document.getElementById("slider-date").textContent = d;
  try {
    const snap = await fetchJson(`data/history/${cropNow}/${d}.json`);
    if (seq !== loadSeq) return;   // közben terményt váltottak
    applyForecast(snap);
  } catch (err) { console.error(err); }
});

async function loadCrop(newCrop) {
  const seq = ++loadSeq;  // e betöltés sorszáma
  crop = newCrop;
  document.querySelectorAll("#crop-switch button").forEach(b => {
    const active = b.dataset.crop === crop;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", active ? "true" : "false");
  });
  const fc = await fetchJson(`data/forecast_${crop}.json`, true);
  if (!yieldHistory[newCrop]) {
    try {
      yieldHistory[newCrop] = await fetchJson(`data/yield_history_${newCrop}.json`);
    } catch (e) { console.error("yield_history betöltés:", e); }
  }
  let dates = [];
  try {
    dates = await fetchJson(`data/history/${newCrop}/index.json`, true);
  } catch { /* nincs history — üres marad */ }
  if (seq !== loadSeq) return;  // közben másik terményre váltottak — eldobjuk
  historyDates = dates;
  applyForecast(fc);
  setupTimeline();
}

document.querySelectorAll("#crop-switch button").forEach(b =>
  b.addEventListener("click", () => loadCrop(b.dataset.crop).catch(err => {
    console.error(err);
    // a meglévő térkép marad; ha nincs friss adat, látható hibajelzés
    if (!currentForecast) {
      setMapStatus("error", "Nem sikerült betölteni az adatot. Ellenőrizd a "
        + "kapcsolatot, majd próbáld újra.");
    }
  })));

document.getElementById("layer-select").addEventListener("change", e => {
  currentLayer = e.target.value;
  if (currentForecast) {
    map.setPaintProperty("counties-fill", "fill-color",
      paintForLayer(currentLayer, currentForecast));
  }
});

async function init() {
  const gj = await fetchJson("data/nuts3_hu.geojson");
  geojson = gj;
  // MapLibre feature-state-hez numerikus/string id kell a feature-ön
  for (const f of geojson.features) f.id = f.properties.NUTS_ID;

  map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {},
      layers: [{ id: "bg", type: "background",
                 paint: { "background-color": "#eef1f3" } }],
    },
    bounds: [[15.9, 45.6], [23.1, 48.7]],
    fitBoundsOptions: { padding: 24 },
    attributionControl: false,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }));

  map.on("load", () => {
    // egyes böngészőkben a konténer mérete a map létrejötte után áll be
    map.resize();
    map.fitBounds([[15.9, 45.6], [23.1, 48.7]], { padding: 24, duration: 0 });
    map.addSource("counties", { type: "geojson", data: geojson, promoteId: "NUTS_ID" });
    map.addLayer({
      id: "counties-fill",
      type: "fill",
      source: "counties",
      // a tényleges színezést applyForecast() állítja be az adat betöltése után
      paint: { "fill-color": COLORS.noData, "fill-opacity": 0.88 },
    });
    map.addLayer({
      id: "counties-line",
      type: "line",
      source: "counties",
      paint: { "line-color": COLORS.border, "line-width": 1 },
    });

    loadCrop("wheat").catch(console.error);

    map.on("click", "counties-fill", e => showPanel(e.features[0].properties.NUTS_ID));
    map.on("mouseenter", "counties-fill", () => map.getCanvas().style.cursor = "pointer");
    map.on("mouseleave", "counties-fill", () => map.getCanvas().style.cursor = "");

    // hover tooltip a vármegyenévvel
    const tip = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
    map.on("mousemove", "counties-fill", e => {
      const p = e.features[0].properties;
      tip.setLngLat(e.lngLat).setText(p.NUTS_NAME || p.NAME_LATN).addTo(map);
    });
    map.on("mouseleave", "counties-fill", () => tip.remove());
  });

  document.getElementById("panel-close").addEventListener("click", () => {
    selectedId = null;
    document.getElementById("panel").classList.add("hidden");
  });

  // a konténer méretének változásakor (mobil elforgatás, layout-váltás) a
  // MapLibre-t újra kell méretezni, különben torzul/elcsúszik a térkép
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => map && map.resize(), 150);
  });
}

// "Újrapróbálom" gomb a hibaállapotban — a legmegbízhatóbb újrapróba az
// oldal újratöltése (az init/map felépítése tiszta állapotból induljon)
document.getElementById("map-status-retry").addEventListener("click",
  () => location.reload());

init().catch(e => {
  console.error(e);
  setMapStatus("error", "Nem sikerült betölteni a térképet: " + e.message);
});
