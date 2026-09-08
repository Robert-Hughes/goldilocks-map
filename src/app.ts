import { addInfoPanel, readMapPanelPreferences } from "./info-panel";
import { LocationsController } from "./locations";
import { MilitaryAreasController } from "./military-areas";
import { MetricState } from "./metrics";
import { RasterView } from "./raster";
import { ServicesController } from "./services";
import type { GoldilocksData } from "./types";

declare const L: any;

const dataElement = document.getElementById("goldilocks-data");
if (!dataElement?.textContent) throw new Error("Embedded Goldilocks data was not found");
const data = JSON.parse(dataElement.textContent) as GoldilocksData;
dataElement.textContent = "";

const thirdPartyNoticesTemplate = document.getElementById("goldilocks-third-party-notices") as HTMLTemplateElement | null;
const thirdPartyNotices = thirdPartyNoticesTemplate?.content.textContent?.trim() ?? "";

if (data.format_version !== 6 || !data.metrics?.length || !data.categories?.length || !data.services || !data.military_areas) {
  throw new Error("This frontend requires Goldilocks data format v6");
}
const categoryById = new Map(data.categories.map((category) => [category.id, category]));
if (categoryById.size !== data.categories.length) throw new Error("Metric category IDs must be unique");
for (const metric of data.metrics) {
  if (!categoryById.has(metric.category_id)) throw new Error(`Metric ${metric.id} references unknown category ${metric.category_id}`);
  if (!data.sources[metric.source_id]) throw new Error(`Metric ${metric.id} references unknown source ${metric.source_id}`);
}
const geometryReference = data.metrics[0];
for (const metric of data.metrics) {
  if (metric.nodata !== geometryReference.nodata || metric.levels.length !== geometryReference.levels.length) {
    throw new Error(`Metric ${metric.id} does not share the common raster geometry`);
  }
  for (let index = 0; index < metric.levels.length; index += 1) {
    const level = metric.levels[index];
    const reference = geometryReference.levels[index];
    if (
      level.width !== reference.width ||
      level.height !== reference.height ||
      level.cell_size_m !== reference.cell_size_m
    ) {
      throw new Error(`Metric ${metric.id} LOD ${index} does not share the common raster geometry`);
    }
  }
}

const metrics = new MetricState(data);
const preferences = readMapPanelPreferences();
const map = L.map("map", {
  // Leaflet normally fades every newly ready GridLayer tile from opacity 0 to 1
  // over 200 ms. Our metric canvases render synchronously, so that fade only
  // makes redraws/zoom tile replacement look like a distracting white flash.
  fadeAnimation: false,
  // Leaflet's tap-hold handler turns a mobile long-press into the same
  // contextmenu event used for desktop right-click location actions.
  tapHold: true,
  zoomControl: false,
});
L.control.zoom({ position: "bottomright" }).addTo(map);

const isFileUrl = window.location.protocol === "file:";
let basemapLayer: any = null;
function setBasemapMonochrome(enabled: boolean) {
  basemapLayer?.getContainer()?.classList.toggle("goldilocks-basemap-monochrome", enabled);
}
if (isFileUrl) {
  const fileBasemapWarning = L.control({ position: "bottomleft" });
  fileBasemapWarning.onAdd = () => {
    const div = L.DomUtil.create("div", "file-basemap-warning");
    div.textContent = "Basemap disabled for file://. Serve Goldilocks over HTTP/HTTPS to load OpenStreetMap.";
    return div;
  };
  fileBasemapWarning.addTo(map);
} else {
  basemapLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
}

const raster = new RasterView(map, data, metrics, {
  showGridLines: preferences.gridLinesVisible,
  opacity: preferences.layerOpacity,
});
const services = new ServicesController(map, data.services);
const militaryAreas = new MilitaryAreasController(map, data.military_areas);
const locations = new LocationsController(map, preferences.locationsVisible);
map.fitBounds(L.latLngBounds(data.grid.bounds_wgs84), { padding: [18, 18] });
setBasemapMonochrome(preferences.basemapMonochrome);

map.on("click", (event: any) => {
  const cell = raster.cellAtLatLng(event.latlng);
  raster.setSelectedCell(cell);
  if (!cell) return;
  L.popup()
    .setLatLng(raster.bngToLatLng(cell.easting, cell.northing))
    .setContent(raster.popupHtml(cell))
    .openOn(map);
});

addInfoPanel(map, data, metrics, raster, services, militaryAreas, locations, thirdPartyNotices, preferences, setBasemapMonochrome);