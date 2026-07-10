(function () {
  "use strict";

  var COLUMNS = [
    { key: "county_name", label: "Vármegye", numeric: false },
    { key: "predicted_yield_t_ha", label: "Becslés (t/ha)", numeric: true, decimals: 2 },
    { key: "anomaly_pct", label: "Anomália (%)", numeric: true, decimals: 1, anomaly: true },
    { key: "low", label: "Sáv alja", numeric: true, decimals: 2 },
    { key: "high", label: "Sáv teteje", numeric: true, decimals: 2 },
    { key: "prec_total_mm", label: "Csapadék (mm)", numeric: true, decimals: 1 },
    { key: "wb_total_mm", label: "Vízmérleg (mm)", numeric: true, decimals: 1 },
    { key: "heat_days", label: "Hőstressznapok", numeric: true, decimals: 0 },
    { key: "gdd_total", label: "GDD", numeric: true, decimals: 0 }
  ];

  var state = {
    crop: "wheat",
    data: null,
    rows: [],
    sortKey: "county_name",
    sortDir: 1 // 1 = növekvő, -1 = csökkenő
  };

  var headRow = document.getElementById("head-row");
  var tbody = document.getElementById("table-body");
  var meta = document.getElementById("meta");
  var statusEl = document.getElementById("status");
  var csvBtn = document.getElementById("csv-btn");
  var cropSwitch = document.getElementById("crop-switch");

  // ---- fejléc felépítése ----
  COLUMNS.forEach(function (col) {
    var th = document.createElement("th");
    th.dataset.key = col.key;
    var label = document.createElement("span");
    label.textContent = col.label;
    var arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.setAttribute("aria-hidden", "true");
    th.appendChild(label);
    th.appendChild(document.createTextNode(" "));
    th.appendChild(arrow);
    function toggleSort() {
      if (state.sortKey === col.key) {
        state.sortDir = -state.sortDir;
      } else {
        state.sortKey = col.key;
        state.sortDir = col.numeric ? -1 : 1; // számoknál csökkenővel indítunk
      }
      render();
    }
    th.addEventListener("click", toggleSort);
    // billentyűzet-hozzáférés a rendezéshez (UX-audit P2.5)
    th.tabIndex = 0;
    th.setAttribute("role", "button");
    th.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleSort(); }
    });
    headRow.appendChild(th);
  });

  // ---- sor-adat kinyerése a JSON-ból ----
  function flatten(county) {
    var w = county.weather_todate || {};
    return {
      county_name: county.county_name,
      predicted_yield_t_ha: county.predicted_yield_t_ha,
      anomaly_pct: county.anomaly_pct,
      low: county.low,
      high: county.high,
      prec_total_mm: nullable(w.prec_total_mm),
      wb_total_mm: nullable(w.wb_total_mm),
      heat_days: nullable(w.heat_days),
      gdd_total: nullable(w.gdd_total),
      hasEstimate: county.predicted_yield_t_ha !== null && county.predicted_yield_t_ha !== undefined,
      note: typeof county.note === "string" ? county.note : ""
    };
  }

  function nullable(v) {
    return typeof v === "number" && isFinite(v) ? v : null;
  }

  // ---- rendezés: becslés nélküli sorok (Budapest) mindig alul ----
  function sortedRows() {
    var key = state.sortKey;
    var dir = state.sortDir;
    var col = COLUMNS.find(function (c) { return c.key === key; });
    var copy = state.rows.slice();
    copy.sort(function (a, b) {
      if (a.hasEstimate !== b.hasEstimate) return a.hasEstimate ? -1 : 1;
      var av = a[key], bv = b[key];
      var aNull = av === null || av === undefined;
      var bNull = bv === null || bv === undefined;
      if (aNull && bNull) return cmpName(a, b);
      if (aNull) return 1;
      if (bNull) return -1;
      var r;
      if (col && col.numeric) {
        r = av - bv;
      } else {
        r = String(av).localeCompare(String(bv), "hu");
      }
      if (r === 0) return cmpName(a, b);
      return r * dir;
    });
    return copy;
  }

  function cmpName(a, b) {
    return String(a.county_name).localeCompare(String(b.county_name), "hu");
  }

  // ---- megjelenítés ----
  function fmt(value, decimals) {
    if (value === null || value === undefined) return null;
    return value.toFixed(decimals).replace(".", ",");
  }

  function render() {
    // fejléc-nyilak
    Array.prototype.forEach.call(headRow.children, function (th) {
      var arrow = th.querySelector(".arrow");
      if (th.dataset.key === state.sortKey) {
        th.classList.add("sorted");
        arrow.textContent = state.sortDir === 1 ? "▲" : "▼";
        th.setAttribute("aria-sort", state.sortDir === 1 ? "ascending" : "descending");
      } else {
        th.classList.remove("sorted");
        arrow.textContent = "";
        th.removeAttribute("aria-sort");
      }
    });

    tbody.textContent = "";
    var rows = sortedRows();

    // oszloponkénti min-max az adatsávokhoz (infografikai heatmap-cellák)
    var ranges = {};
    COLUMNS.forEach(function (col) {
      if (!col.numeric) return;
      var vals = rows.map(function (r) { return r[col.key]; })
        .filter(function (v) { return v !== null && v !== undefined; });
      if (vals.length) {
        ranges[col.key] = [Math.min.apply(null, vals), Math.max.apply(null, vals)];
      }
    });

    rows.forEach(function (row) {
      var tr = document.createElement("tr");
      if (!row.hasEstimate) tr.classList.add("no-estimate");
      COLUMNS.forEach(function (col) {
        var td = document.createElement("td");
        var v = row[col.key];
        if (!col.numeric) {
          td.textContent = String(v);
        } else if (v === null || v === undefined) {
          // becslés nélküli megye becslés-oszlopainál jelölés
          td.textContent = col.key === "predicted_yield_t_ha" ? "nincs becslés" : "–";
          td.classList.add("na");
        } else {
          td.textContent = fmt(v, col.decimals);
          if (col.anomaly) {
            if (v < 0) td.classList.add("anomaly-neg");
            else if (v > 0) td.classList.add("anomaly-pos");
          }
          // adatsáv a cella hátterében (csak számított %-érték — XSS-mentes).
          // Anomáliánál a sáv HOSSZA az eltérés MÉRTÉKÉT kódolja (nullától),
          // ne a tartományon belüli pozíciót — különben a legjobb megye kapná
          // a leghosszabb piros sávot (UX-audit P1.2).
          var rg = ranges[col.key];
          if (rg && rg[1] > rg[0]) {
            var pct, color;
            if (col.anomaly) {
              var maxAbs = Math.max(Math.abs(rg[0]), Math.abs(rg[1])) || 1;
              pct = 100 * Math.abs(v) / maxAbs;
              color = v < 0 ? "rgba(192,57,43,.14)" : "rgba(30,132,73,.14)";
            } else {
              pct = 100 * (v - rg[0]) / (rg[1] - rg[0]);
              color = "rgba(93,122,148,.13)";
            }
            td.style.background = "linear-gradient(to right, " + color + " " +
              pct.toFixed(1) + "%, transparent " + pct.toFixed(1) + "%)";
          }
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  // ---- CSV export ----
  function csvField(s) {
    s = String(s);
    if (/[";\n\r]/.test(s)) {
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  }

  function downloadCsv() {
    if (!state.data) return;
    var lines = [];
    lines.push(COLUMNS.map(function (c) { return csvField(c.label); }).join(";"));
    sortedRows().forEach(function (row) {
      var cells = COLUMNS.map(function (col) {
        var v = row[col.key];
        if (v === null || v === undefined) {
          return col.key === "predicted_yield_t_ha" ? csvField("nincs becslés") : "";
        }
        if (col.numeric) return v.toFixed(col.decimals); // pont tizedes marad
        return csvField(v);
      });
      lines.push(cells.join(";"));
    });
    var bom = "\uFEFF";
    var blob = new Blob([bom + lines.join("\r\n") + "\r\n"], {
      type: "text/csv;charset=utf-8"
    });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "elorejelzes_" + state.crop + "_" + (state.data.updated_at || "na") + ".csv";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  csvBtn.addEventListener("click", downloadCsv);

  // ---- adatbetöltés ----
  function setStatus(text, isError) {
    if (text) {
      statusEl.textContent = text;
      statusEl.classList.remove("hidden");
      statusEl.classList.toggle("error", !!isError);
    } else {
      statusEl.textContent = "";
      statusEl.classList.add("hidden");
      statusEl.classList.remove("error");
    }
  }

  function load(crop) {
    state.crop = crop;
    csvBtn.disabled = true;
    meta.textContent = "betöltés…";
    setStatus("Adatok betöltése…");
    fetch("data/forecast_" + crop + ".json", { cache: "no-cache" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        state.data = data;
        state.rows = (data.counties || []).map(flatten);
        meta.textContent =
          (data.crop ? data.crop + " · " : "") +
          (data.crop_year ? data.crop_year + "-es termésév · " : "") +
          (data.updated_at ? "frissítve: " + data.updated_at : "") +
          (data.weather_known_until
            ? " · időjárás eddig: " + data.weather_known_until : "");
        csvBtn.disabled = false;
        setStatus(null);
        render();
      })
      .catch(function (err) {
        state.data = null;
        state.rows = [];
        tbody.textContent = "";
        meta.textContent = "hiba";
        setStatus("Nem sikerült betölteni az adatokat (" + err.message + ").", true);
      });
  }

  // ---- termény-váltó ----
  cropSwitch.addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-crop]");
    if (!btn) return;
    Array.prototype.forEach.call(
      cropSwitch.querySelectorAll("button"),
      function (b) { b.classList.toggle("active", b === btn); b.setAttribute("aria-selected", ( b === btn) ? "true" : "false"); }
    );
    load(btn.dataset.crop);
  });

  load(state.crop);
})();
