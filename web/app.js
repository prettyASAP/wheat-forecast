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
  if (selectedId) showPanel(selectedId);
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
  if (c.predicted_yield_t_ha === null) {
    body.innerHTML = `<p class="note">${c.note || "Nincs becslés."}</p>${wxRows}`;
  } else {
    const cls = c.anomaly_pct < 0 ? "neg" : "pos";
    const sign = c.anomaly_pct > 0 ? "+" : "";
    body.innerHTML = `
      <div class="big-number">${c.predicted_yield_t_ha.toFixed(2)} t/ha</div>
      <div class="band">80%-os sáv: ${c.low.toFixed(2)} – ${c.high.toFixed(2)} t/ha</div>
      <div class="anomaly ${cls}">${sign}${c.anomaly_pct.toFixed(1)}% a trendhez képest</div>
      ${wxRows}`;
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
