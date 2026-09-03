declare const L: any;

type Cell = {
  id: string;
  value: number;
  valid_days: number;
  easting: number;
  northing: number;
  lat: number;
  lon: number;
  corners: [number, number][];
};

type GoldilocksData = {
  metric: {
    id: string;
    label: string;
    units: string;
    period: string;
    threshold_c: number;
    definition: string;
  };
  grid: {
    crs: string;
    resolution_m: [number, number];
    shape: [number, number];
    cell_count: number;
    selection_center_wgs84: { lat: number; lon: number };
  };
  source: {
    provider: string;
    variable: string;
    file: string;
    url: string;
    status: string;
    resolution: string;
    note: string;
  };
  summary: { min: number; max: number };
  cells: Cell[];
};

const dataElement = document.getElementById("goldilocks-data");
if (!dataElement?.textContent) {
  throw new Error("Embedded Goldilocks data was not found");
}
const data = JSON.parse(dataElement.textContent) as GoldilocksData;

const map = L.map("map", {
  center: [data.grid.selection_center_wgs84.lat, data.grid.selection_center_wgs84.lon],
  zoom: 12,
});

L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png", {
  maxZoom: 20,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
}).addTo(map);

function colorForValue(value: number): string {
  const min = data.summary.min;
  const max = data.summary.max;
  const ratio = max === min ? 0.5 : Math.max(0, Math.min(1, (value - min) / (max - min)));
  const hue = 210 - ratio * 210;
  return `hsl(${hue.toFixed(0)} 78% 48%)`;
}

function popupHtml(cell: Cell): string {
  return `
    <strong>${data.metric.label}</strong>
    <dl>
      <dt>Value</dt><dd>${cell.value} ${data.metric.units}</dd>
      <dt>Period</dt><dd>${data.metric.period}</dd>
      <dt>Cell</dt><dd>${cell.id}</dd>
      <dt>BNG</dt><dd>E ${cell.easting.toLocaleString()}, N ${cell.northing.toLocaleString()}</dd>
      <dt>WGS84</dt><dd>${cell.lat.toFixed(5)}, ${cell.lon.toFixed(5)}</dd>
      <dt>Valid days</dt><dd>${cell.valid_days}</dd>
    </dl>`;
}

const overlay = L.featureGroup();
for (const cell of data.cells) {
  const polygon = L.polygon(cell.corners, {
    color: "#263238",
    weight: 0.7,
    opacity: 0.72,
    fillColor: colorForValue(cell.value),
    fillOpacity: 0.62,
  });
  polygon.bindPopup(popupHtml(cell));
  polygon.on("mouseover", () => polygon.setStyle({ weight: 2, fillOpacity: 0.78 }));
  polygon.on("mouseout", () => polygon.setStyle({ weight: 0.7, fillOpacity: 0.62 }));
  polygon.addTo(overlay);
}
overlay.addTo(map);

if (data.cells.length) {
  map.fitBounds(overlay.getBounds(), { padding: [18, 18] });
}

const legend = L.control({ position: "bottomright" });
legend.onAdd = () => {
  const div = L.DomUtil.create("div", "legend");
  const min = data.summary.min;
  const max = data.summary.max;
  const values: number[] = [];
  const steps = Math.min(6, Math.max(1, max - min + 1));
  for (let i = 0; i < steps; i += 1) {
    values.push(Math.round(min + ((max - min) * i) / Math.max(1, steps - 1)));
  }
  const uniqueValues = [...new Set(values)];
  div.innerHTML = `<div class="legend-title">${data.metric.label}<br><small>${data.metric.period}</small></div>` +
    uniqueValues.map((value) => `<div class="legend-row"><span class="legend-swatch" style="background:${colorForValue(value)}"></span><span>${value} ${value === 1 ? "day" : "days"}</span></div>`).join("");
  return div;
};
legend.addTo(map);

const info = L.control({ position: "topright" });
info.onAdd = () => {
  const div = L.DomUtil.create("div", "info-control");
  const initiallyCollapsed = window.matchMedia("(max-width: 600px)").matches;
  if (initiallyCollapsed) div.classList.add("collapsed");
  div.innerHTML = `
    <div class="info-header">
      <strong>${data.metric.label}</strong>
      <button class="info-toggle" type="button" aria-label="${initiallyCollapsed ? "Expand" : "Collapse"} information panel" aria-expanded="${!initiallyCollapsed}">${initiallyCollapsed ? "+" : "−"}</button>
    </div>
    <div class="info-body">
      ${data.metric.definition}
      <strong>Data provenance</strong>
      ${data.source.provider}, ${data.source.status}, ${data.source.resolution}. Variable: <code>${data.source.variable}</code>.<br>
      Source: <a href="${data.source.url}" target="_blank" rel="noopener">${data.source.file}</a>.<br>
      ${data.source.note}
    </div>`;
  const button = div.querySelector(".info-toggle") as HTMLButtonElement;
  button.addEventListener("click", () => {
    const collapsed = div.classList.toggle("collapsed");
    button.textContent = collapsed ? "+" : "−";
    button.setAttribute("aria-expanded", String(!collapsed));
    button.setAttribute("aria-label", `${collapsed ? "Expand" : "Collapse"} information panel`);
  });
  L.DomEvent.disableClickPropagation(div);
  L.DomEvent.disableScrollPropagation(div);
  return div;
};
info.addTo(map);