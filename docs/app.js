// Load SDK modules through the CDN module loader
const [FeatureLayer, reactiveUtils] = await $arcgis.import([
  "@arcgis/core/layers/FeatureLayer.js",
  "@arcgis/core/core/reactiveUtils.js",
]);

const { serviceUrl } = window.APP_CONFIG;
const TZ = "America/New_York";

const fmtHour = (d) => new Date(d).toLocaleString("en-US", {
  timeZone: TZ, weekday: "short", month: "short", day: "numeric", hour: "numeric",
});
const fmtTick = (d) => new Date(d).toLocaleString("en-US", {
  timeZone: TZ, weekday: "short", hour: "numeric",
});
const fmtNum = (n) => Math.round(n).toLocaleString("en-US");

// ---------- Renderers ----------
const sizeByField = (field) => ({
  type: "size", field,
  stops: [{ value: 0, size: 2 }, { value: 50, size: 9 }, { value: 250, size: 26 }],
});
const marker = (color) => ({
  type: "simple-marker", color, outline: { color: [255, 255, 255, 0.8], width: 0.5 },
});
const RENDERERS = {
  predicted: { type: "simple", symbol: marker([230, 85, 13, 0.75]), visualVariables: [sizeByField("predicted")] },
  actual: { type: "simple", symbol: marker([84, 39, 143, 0.75]), visualVariables: [sizeByField("actual")] },
  error: {
    type: "simple",
    symbol: { ...marker([200, 200, 200]), size: 9 },
    visualVariables: [{
      type: "color", field: "error",
      stops: [
        { value: -30, color: "#2166ac", label: "Under-forecast (−30)" },
        { value: 0, color: "#f7f7f7", label: "0" },
        { value: 30, color: "#b2182b", label: "Over-forecast (+30)" },
      ],
    }],
  },
};

// ---------- Layers ----------
const zonesLayer = new FeatureLayer({
  url: `${serviceUrl}/0`,
  title: "Taxi zones",
  outFields: ["zone_id", "zone", "borough", "predicted_total", "actual_total", "error_pct"],
  renderer: {
    type: "simple",
    symbol: { type: "simple-fill", color: [0, 0, 0, 0], outline: { color: [120, 120, 120, 0.5], width: 0.5 } },
  },
  popupEnabled: false,
  legendEnabled: false,
});

const hourlyLayer = new FeatureLayer({
  url: `${serviceUrl}/1`,
  title: "Lyft requests per hour",
  outFields: ["*"],
  renderer: RENDERERS.predicted,
  popupTemplate: {
    title: "{zone} ({borough})",
    content: [{
      type: "fields",
      fieldInfos: [
        { fieldName: "ts_utc", label: "Hour" },
        { fieldName: "predicted", label: "Predicted requests", format: { places: 1 } },
        { fieldName: "actual", label: "Actual requests" },
        { fieldName: "error", label: "Error (predicted − actual)", format: { places: 1 } },
      ],
    }],
  },
});

const mapEl = document.getElementById("map");
await mapEl.viewOnReady();
mapEl.map.addMany([zonesLayer, hourlyLayer]);
await hourlyLayer.load();

// ---------- Time slider ----------
const slider = document.getElementById("slider");
await slider.componentOnReady();
const fullExtent = hourlyLayer.timeInfo.fullTimeExtent;
slider.fullTimeExtent = fullExtent;
slider.stops = { interval: { value: 1, unit: "hours" } };

// Start on tomorrow 6 PM New York time: the last hour of the window is 11 PM tomorrow
const start = new Date(fullExtent.end.getTime() - 5 * 60 * 60 * 1000);
slider.timeExtent = { start, end: start };

// ---------- City-wide totals for the selected hour (server-side statistics) ----------
async function updateCityStats(timeExtent) {
  if (!timeExtent?.start) return;
  document.getElementById("time-label").textContent = fmtHour(timeExtent.start);
  const result = await hourlyLayer.queryFeatures({
    where: "1=1",
    timeExtent,
    outStatistics: [
      { statisticType: "sum", onStatisticField: "predicted", outStatisticFieldName: "pred" },
      { statisticType: "sum", onStatisticField: "actual", outStatisticFieldName: "act" },
    ],
  });
  const { pred, act } = result.features[0].attributes;
  document.getElementById("city-pred").textContent = fmtNum(pred);
  // Hours in the future have no actual demand yet
  if (act === null || act === undefined) {
    document.getElementById("city-act").textContent = "—";
    document.getElementById("city-err").textContent = "—";
    document.getElementById("time-label").textContent += " · forecast only";
    return;
  }
  document.getElementById("city-act").textContent = fmtNum(act);
  const errPct = act > 0 ? ((pred - act) / act) * 100 : 0;
  document.getElementById("city-err").textContent = `${errPct > 0 ? "+" : ""}${errPct.toFixed(1)}%`;
}

reactiveUtils.watch(() => slider.timeExtent, updateCityStats, { initial: true });

// ---------- Renderer mode ----------
document.getElementById("mode").addEventListener("change", (e) => {
  hourlyLayer.renderer = RENDERERS[e.target.value];
});

// ---------- Zone chart on click ----------
let chart = null;
let highlight = null;
const zonesView = await mapEl.view.whenLayerView(zonesLayer);

mapEl.addEventListener("arcgisViewClick", async (event) => {
  const { features } = await zonesLayer.queryFeatures({
    geometry: event.detail.mapPoint,
    spatialRelationship: "intersects",
    outFields: ["*"],
    returnGeometry: true,
  });
  if (!features.length) return;
  const zone = features[0].attributes;

  highlight?.remove();
  highlight = zonesView.highlight(features[0]);

  const { features: hours } = await hourlyLayer.queryFeatures({
    where: `zone_id = ${zone.zone_id}`,
    timeExtent: fullExtent,
    outFields: ["ts_utc", "predicted", "actual"],
    orderByFields: ["ts_utc"],
    returnGeometry: false,
  });
  const rows = hours.map((f) => f.attributes);

  document.getElementById("zone-title").textContent = `${zone.zone} (${zone.borough})`;
  document.getElementById("zone-stats").textContent =
    `Week total: predicted ${fmtNum(zone.predicted_total)}, actual ${fmtNum(zone.actual_total)} ` +
    `(${zone.error_pct > 0 ? "+" : ""}${zone.error_pct}%)`;

  chart?.destroy();
  chart = new Chart(document.getElementById("zone-chart"), {
    type: "line",
    data: {
      labels: rows.map((r) => fmtTick(r.ts_utc)),
      datasets: [
        { label: "Predicted", data: rows.map((r) => r.predicted), borderColor: "#e6550d", pointRadius: 0, borderWidth: 2, tension: 0.2 },
        { label: "Actual", data: rows.map((r) => r.actual), borderColor: "#54278f", pointRadius: 0, borderWidth: 2, tension: 0.2 },
      ],
    },
    options: {
      animation: false,
      interaction: { mode: "index", intersect: false },
      scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { beginAtZero: true } },
    },
  });
});