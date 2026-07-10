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
  anomaly: { note: "anomália a trendhez képest", unit: "%", fixed: [-20, 20],
             colors: ["#b03a2e", "#e67e22", "#f5e8c8", "#82c07a", "#1e8449"] },
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

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
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
  let [lo, hi] = spec.fixed || [Math.min(...vals), Math.max(...vals)];
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
  document.getElementById("legend-note").textContent = spec.note;
  document.getElementById("legend-bar").style.background =
    `linear-gradient(to right, ${spec.colors.join(", ")})`;
  return expr;
}

function applyForecast(fc) {
  currentForecast = fc;
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
  document.getElementById("meta").textContent =
    `${fc.crop} · ${fc.crop_year}-es termésév · frissítve: ${fc.updated_at}` +
    ` · időjárás eddig: ${fc.weather_known_until}`;
  renderNational(fc);
  if (selectedId) showPanel(selectedId);
}

function renderNational(fc) {
  const el = document.getElementById("national");
  const n = fc.national;
  if (!n) { el.innerHTML = ""; return; }
  const cls = n.anomaly_pct < 0 ? "neg" : "pos";
  const s = v => (v > 0 ? "+" : "") + v.toFixed(1);
  el.innerHTML = `
    <span class="nat-item"><b>Országos becslés:</b> ${n.predicted_yield_t_ha.toFixed(2)} t/ha</span>
    <span class="nat-item ${cls}">${s(n.anomaly_pct)}% a trendhez képest</span>
    <span class="nat-item">${s(n.yoy_pct)}% vs ${n.prev_year} (${n.prev_year_yield_t_ha.toFixed(2)} t/ha)</span>
    <span class="nat-item">${n.rank_total} évből a ${n.rank_from_worst}. leggyengébb</span>`;
}

/* Kézi SVG vonaldiagram: historikus hozamok + idei becslés sávval.
   Semmi külső könyvtár — statikus oldal marad. */
function yieldChartSVG(years, yields, cur) {
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
         `<text x="${PL - 4}" y="${Y(v) + 3}" text-anchor="end" font-size="8" fill="#90a0ab">${v.toFixed(1)}</text>`;
  }
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
  g += `<text x="${X(x0)}" y="${H - 4}" font-size="8" fill="#90a0ab">${x0}</text>` +
       `<text x="${X(x1)}" y="${H - 4}" text-anchor="end" font-size="8" fill="#90a0ab">${x1}</text>`;
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
  const frostRow = wx.frost_days_winter === null || wx.frost_days_winter === undefined
    ? ""
    : `<tr><td>Téli fagynapok (−15 °C alatt)</td><td>${wx.frost_days_winter}</td></tr>`;
  const wxRows = `
    <table>
      <tr><td>Csapadék (termésév)</td><td>${wx.prec_total_mm} mm</td></tr>
      <tr><td>Vízmérleg (csap. − párolgás)</td><td>${wx.wb_total_mm} mm</td></tr>
      <tr><td>Hőstressznapok (kritikus ablak)</td><td>${wx.heat_days}</td></tr>
      ${frostRow}
      <tr><td>Hőösszeg (GDD)</td><td>${wx.gdd_total}</td></tr>
    </table>`;
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
    body.innerHTML = `<p class="note">${c.note || "Nincs becslés."}</p>${chart}${wxRows}`;
  } else {
    const cls = c.anomaly_pct < 0 ? "neg" : "pos";
    const sign = c.anomaly_pct > 0 ? "+" : "";
    body.innerHTML = `
      <div class="big-number">${c.predicted_yield_t_ha.toFixed(2)} t/ha</div>
      <div class="band">80%-os sáv: ${c.low.toFixed(2)} – ${c.high.toFixed(2)} t/ha</div>
      <div class="anomaly ${cls}">${sign}${c.anomaly_pct.toFixed(1)}% a trendhez képest</div>
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
  const d = historyDates[Number(e.target.value)];
  document.getElementById("slider-date").textContent = d;
  try {
    applyForecast(await fetchJson(`data/history/${crop}/${d}.json`));
  } catch (err) { console.error(err); }
});

async function loadCrop(newCrop) {
  crop = newCrop;
  document.querySelectorAll("#crop-switch button").forEach(b =>
    b.classList.toggle("active", b.dataset.crop === crop));
  const fc = await fetchJson(`data/forecast_${crop}.json`);
  if (!yieldHistory[crop]) {
    try {
      yieldHistory[crop] = await fetchJson(`data/yield_history_${crop}.json`);
    } catch (e) { console.error("yield_history betöltés:", e); }
  }
  try {
    historyDates = await fetchJson(`data/history/${crop}/index.json`);
  } catch { historyDates = []; }
  applyForecast(fc);
  setupTimeline();
}

document.querySelectorAll("#crop-switch button").forEach(b =>
  b.addEventListener("click", () => loadCrop(b.dataset.crop).catch(console.error)));

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
}

init().catch(e => {
  document.getElementById("meta").textContent = "Hiba az adatok betöltésekor: " + e.message;
});
