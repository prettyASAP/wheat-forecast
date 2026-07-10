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

let map, geojson, historyDates = [], currentForecast = null;
let selectedId = null;
let crop = "wheat";
const yieldHistory = {};  // crop -> yield_history JSON (cache)

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

function anomalyColorExpr() {
  const expr = ["case",
    ["==", ["feature-state", "hasData"], false], COLORS.noData,
    ["interpolate", ["linear"], ["feature-state", "anomaly"]]];
  const interp = expr[expr.length - 1];
  for (const [v, c] of COLORS.scale) interp.push(v, c);
  return expr;
}

function applyForecast(fc) {
  currentForecast = fc;
  const byId = Object.fromEntries(fc.counties.map(c => [c.nuts_id, c]));
  for (const f of geojson.features) {
    const id = f.properties.NUTS_ID;
    const c = byId[id];
    const has = !!(c && c.predicted_yield_t_ha !== null);
    map.setFeatureState({ source: "counties", id },
      { hasData: has, anomaly: has ? c.anomaly_pct : 0 });
  }
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
      paint: { "fill-color": anomalyColorExpr(), "fill-opacity": 0.88 },
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
