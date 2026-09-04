import proj4 from "proj4";

declare const L: any;

type RasterLevelTransport = {
  level: number;
  width: number;
  height: number;
  cell_size_m: number;
  values: number[];
};

type RasterLevel = Omit<RasterLevelTransport, "values"> & { values: Uint8Array };

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
    proj4: string;
    cell_size_m: number;
    width: number;
    height: number;
    cell_count: number;
    valid_cell_count: number;
    west: number;
    south: number;
    east: number;
    north: number;
    row_order: "south_to_north";
    column_order: "west_to_east";
    bounds_wgs84: [number, number][];
    selection_center_wgs84: { lat: number; lon: number };
  };
  raster: {
    encoding: string;
    nodata: number;
    levels: RasterLevelTransport[];
    valid_days: number[];
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
};

type RasterCell = {
  row: number;
  column: number;
  index: number;
  value: number;
  validDays: number;
  easting: number;
  northing: number;
  lat: number;
  lon: number;
};

const dataElement = document.getElementById("goldilocks-data");
if (!dataElement?.textContent) {
  throw new Error("Embedded Goldilocks data was not found");
}
const data = JSON.parse(dataElement.textContent) as GoldilocksData;
if (!data.raster.levels.length) {
  throw new Error("Raster LOD pyramid is empty");
}
const lodLevels: RasterLevel[] = data.raster.levels.map((level, index) => {
  if (level.level !== index) throw new Error(`Unexpected raster LOD index ${level.level}; expected ${index}`);
  if (level.values.length !== level.width * level.height) {
    throw new Error(`Raster LOD ${index} value count does not match its dimensions`);
  }
  if (index === 0) {
    if (level.width !== data.grid.width || level.height !== data.grid.height || level.cell_size_m !== data.grid.cell_size_m) {
      throw new Error("Raster LOD0 does not match base grid metadata");
    }
  } else {
    const previous = data.raster.levels[index - 1];
    if (level.width !== Math.ceil(previous.width / 2) || level.height !== Math.ceil(previous.height / 2)) {
      throw new Error(`Raster LOD ${index} dimensions are not half of the previous level`);
    }
    if (level.cell_size_m !== previous.cell_size_m * 2) {
      throw new Error(`Raster LOD ${index} cell size is not double the previous level`);
    }
  }
  return { ...level, values: Uint8Array.from(level.values) };
});
const baseRasterValues = lodLevels[0].values;
if (data.raster.valid_days.length !== data.grid.width * data.grid.height) {
  throw new Error("Raster valid-day count does not match base grid dimensions");
}
const rasterValidDays = Uint8Array.from(data.raster.valid_days);
// JSON arrays are only the transport representation; keep compact typed arrays
// at runtime so this scales to much larger rasters.
for (const level of data.raster.levels) level.values = [];
data.raster.valid_days = [];

proj4.defs(data.grid.crs, data.grid.proj4);

const map = L.map("map", {
  center: [data.grid.selection_center_wgs84.lat, data.grid.selection_center_wgs84.lon],
  zoom: 12,
});

const useFileBasemap = window.location.protocol === "file:";
if (useFileBasemap) {
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  }).addTo(map);
} else {
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
}

function colorForValue(value: number): string {
  const min = data.summary.min;
  const max = data.summary.max;
  const ratio = max === min ? 0.5 : Math.max(0, Math.min(1, (value - min) / (max - min)));
  const hue = 210 - ratio * 210;
  return `hsl(${hue.toFixed(0)} 78% 48%)`;
}

function latLngToBng(latlng: any): [number, number] {
  return proj4("EPSG:4326", data.grid.crs, [latlng.lng, latlng.lat]) as [number, number];
}

function bngToLatLng(easting: number, northing: number): any {
  const [lon, lat] = proj4(data.grid.crs, "EPSG:4326", [easting, northing]) as [number, number];
  return L.latLng(lat, lon);
}

function rasterIndex(row: number, column: number, width: number): number {
  return row * width + column;
}

function cellAtLatLng(latlng: any): RasterCell | null {
  const [easting, northing] = latLngToBng(latlng);
  const column = Math.floor((easting - data.grid.west) / data.grid.cell_size_m);
  const row = Math.floor((northing - data.grid.south) / data.grid.cell_size_m);
  if (column < 0 || column >= data.grid.width || row < 0 || row >= data.grid.height) {
    return null;
  }
  const index = rasterIndex(row, column, data.grid.width);
  const value = baseRasterValues[index];
  if (value === data.raster.nodata) {
    return null;
  }
  const centerEasting = data.grid.west + (column + 0.5) * data.grid.cell_size_m;
  const centerNorthing = data.grid.south + (row + 0.5) * data.grid.cell_size_m;
  const center = bngToLatLng(centerEasting, centerNorthing);
  return {
    row,
    column,
    index,
    value,
    validDays: rasterValidDays[index],
    easting: Math.round(centerEasting),
    northing: Math.round(centerNorthing),
    lat: center.lat,
    lon: center.lng,
  };
}

function popupHtml(cell: RasterCell): string {
  return `
    <strong>${data.metric.label}</strong>
    <dl>
      <dt>Value</dt><dd>${cell.value} ${data.metric.units}</dd>
      <dt>Period</dt><dd>${data.metric.period}</dd>
      <dt>Cell</dt><dd>BNG-${cell.easting}-${cell.northing}</dd>
      <dt>Raster</dt><dd>row ${cell.row}, column ${cell.column}</dd>
      <dt>BNG</dt><dd>E ${cell.easting.toLocaleString()}, N ${cell.northing.toLocaleString()}</dd>
      <dt>WGS84</dt><dd>${cell.lat.toFixed(5)}, ${cell.lon.toFixed(5)}</dd>
      <dt>Valid days</dt><dd>${cell.validDays}</dd>
    </dl>`;
}

const LOD_MIN_CELL_PIXELS = 4;
const LOD_REFERENCE_LAT = 54.5;
const LOD_REFERENCE_LON = -2.0;

function chooseLodLevel(mapInstance: any): RasterLevel {
  // Use a fixed representative UK location for the screen-size calculation.
  // Web Mercator scale varies with latitude, so using the live map centre made
  // LOD change merely by panning north/south at a fixed zoom. A fixed reference
  // removes that instability while still calculating pixel size at runtime.
  //
  // Deliberately use map.project() rather than latLngToContainerPoint(). Leaflet
  // rounds layer/container coordinates to whole CSS pixels; once a 1 km base cell
  // is sub-pixel, its two endpoints can round to the same pixel and falsely
  // measure as zero, which would select the coarsest LOD in one jump. project()
  // retains floating-point pixel coordinates at the current (including fractional)
  // zoom, so adjacent zoom levels advance through adjacent LOD levels as intended.
  const reference = L.latLng(LOD_REFERENCE_LAT, LOD_REFERENCE_LON);
  const [referenceEasting, referenceNorthing] = latLngToBng(reference);
  const baseCellEast = bngToLatLng(referenceEasting + data.grid.cell_size_m, referenceNorthing);
  const zoom = mapInstance.getZoom();
  const basePixels = mapInstance.project(reference, zoom).distanceTo(
    mapInstance.project(baseCellEast, zoom),
  );

  let levelIndex = 0;
  while (
    levelIndex + 1 < lodLevels.length &&
    basePixels * Math.pow(2, levelIndex) < LOD_MIN_CELL_PIXELS
  ) {
    levelIndex += 1;
  }
  return lodLevels[levelIndex];
}

const gridWgs84Envelope = (() => {
  const latitudes = data.grid.bounds_wgs84.map(([lat]) => lat);
  const longitudes = data.grid.bounds_wgs84.map(([, lon]) => lon);
  return {
    south: Math.min(...latitudes),
    north: Math.max(...latitudes),
    west: Math.min(...longitudes),
    east: Math.max(...longitudes),
  };
})();

function visibleRasterRange(
  mapInstance: any,
  level: RasterLevel,
): { rowMin: number; rowMax: number; colMin: number; colMax: number } | null {
  const bounds = mapInstance.getBounds();

  // Never project remote viewport corners into BNG. At low zoom they can be
  // thousands of kilometres from Britain, where their projected extrema are a
  // poor description of the small UK area we actually care about. Intersect in
  // WGS84 first, then project only that clipped rectangle around the raster.
  const south = Math.max(bounds.getSouth(), gridWgs84Envelope.south);
  const north = Math.min(bounds.getNorth(), gridWgs84Envelope.north);
  const west = Math.max(bounds.getWest(), gridWgs84Envelope.west);
  const east = Math.min(bounds.getEast(), gridWgs84Envelope.east);
  if (south > north || west > east) return null;

  // Include edge midpoints as well as corners. This is cheap (eight transforms
  // per redraw) and avoids assuming BNG extrema always occur at the corners of
  // a geographic rectangle as the covered area grows toward full-UK scale.
  const midLat = (south + north) / 2;
  const midLon = (west + east) / 2;
  const projected = [
    L.latLng(south, west),
    L.latLng(south, midLon),
    L.latLng(south, east),
    L.latLng(midLat, west),
    L.latLng(midLat, east),
    L.latLng(north, west),
    L.latLng(north, midLon),
    L.latLng(north, east),
  ].map(latLngToBng);
  const eastings = projected.map(([easting]) => easting);
  const northings = projected.map(([, northing]) => northing);
  const minEasting = Math.min(...eastings);
  const maxEasting = Math.max(...eastings);
  const minNorthing = Math.min(...northings);
  const maxNorthing = Math.max(...northings);

  const rawColMin = Math.floor((minEasting - data.grid.west) / level.cell_size_m);
  const rawColMax = Math.floor((maxEasting - data.grid.west) / level.cell_size_m);
  const rawRowMin = Math.floor((minNorthing - data.grid.south) / level.cell_size_m);
  const rawRowMax = Math.floor((maxNorthing - data.grid.south) / level.cell_size_m);

  if (rawColMax < 0 || rawRowMax < 0 || rawColMin >= level.width || rawRowMin >= level.height) {
    return null;
  }

  // One extra cell prevents projection/rounding artefacts at viewport edges,
  // while keeping redraw work proportional to the visible part of the chosen LOD.
  const marginCells = 1;
  const colMin = Math.max(0, rawColMin - marginCells);
  const rowMin = Math.max(0, rawRowMin - marginCells);
  const colMax = Math.min(level.width - 1, rawColMax + marginCells);
  const rowMax = Math.min(level.height - 1, rawRowMax + marginCells);
  return { rowMin, rowMax, colMin, colMax };
}

const RasterCanvasLayer = L.Layer.extend({
  initialize(this: any) {
    this._selectedIndex = null;
    this._renderCenter = null;
    this._renderZoom = null;
    this._activeLevel = lodLevels[0];
  },

  onAdd(this: any, mapInstance: any) {
    this._map = mapInstance;
    this._canvas = L.DomUtil.create("canvas", "goldilocks-raster-layer leaflet-zoom-animated");
    this._canvas.style.pointerEvents = "none";
    mapInstance.getPane("overlayPane").appendChild(this._canvas);
    mapInstance.on("moveend zoomend resize viewreset", this._reset, this);
    mapInstance.on("zoom", this._onZoom, this);
    mapInstance.on("zoomanim", this._onZoomAnim, this);
    this._reset();
  },

  onRemove(this: any, mapInstance: any) {
    mapInstance.off("moveend zoomend resize viewreset", this._reset, this);
    mapInstance.off("zoom", this._onZoom, this);
    mapInstance.off("zoomanim", this._onZoomAnim, this);
    this._canvas.remove();
    this._map = null;
    this._canvas = null;
    this._renderCenter = null;
    this._renderZoom = null;
  },

  setSelectedIndex(this: any, index: number | null) {
    this._selectedIndex = index;
    if (this._map) this._reset();
  },

  _onZoom(this: any) {
    if (!this._map) return;
    this._updateTransform(this._map.getCenter(), this._map.getZoom());
  },

  _onZoomAnim(this: any, event: any) {
    this._updateTransform(event.center, event.zoom);
  },

  _updateTransform(this: any, center: any, zoom: number) {
    const mapInstance = this._map;
    const canvas = this._canvas as HTMLCanvasElement | null;
    if (!mapInstance || !canvas || this._renderCenter === null || this._renderZoom === null) return;

    // Keep the currently rendered LOD fixed for the whole zoom gesture. Leaflet
    // continuously transforms this bitmap; only `_reset` after the gesture chooses
    // a new LOD and rerasterises it at the final zoom.
    const scale = mapInstance.getZoomScale(zoom, this._renderZoom);
    const viewHalf = mapInstance.getSize().multiplyBy(0.5);
    const currentCenterPoint = mapInstance.project(this._renderCenter, zoom);
    const topLeftOffset = viewHalf
      .multiplyBy(-scale)
      .add(currentCenterPoint)
      .subtract(mapInstance._getNewPixelOrigin(center, zoom));
    L.DomUtil.setTransform(canvas, topLeftOffset, scale);
  },

  _reset(this: any) {
    const mapInstance = this._map;
    const canvas = this._canvas as HTMLCanvasElement;
    if (!mapInstance || !canvas) return;

    this._activeLevel = chooseLodLevel(mapInstance);
    canvas.dataset.lod = String(this._activeLevel.level);
    canvas.dataset.lodCellSizeM = String(this._activeLevel.cell_size_m);

    const size = mapInstance.getSize();
    const topLeft = mapInstance.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, topLeft);

    const pixelRatio = Math.max(1, window.devicePixelRatio || 1);
    canvas.width = Math.max(1, Math.round(size.x * pixelRatio));
    canvas.height = Math.max(1, Math.round(size.y * pixelRatio));
    canvas.style.width = `${size.x}px`;
    canvas.style.height = `${size.y}px`;

    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, size.x, size.y);
    this._draw(context, this._activeLevel);
    this._drawSelection(context);
    this._renderCenter = mapInstance.getCenter();
    this._renderZoom = mapInstance.getZoom();
  },

  _draw(this: any, context: CanvasRenderingContext2D, level: RasterLevel) {
    const range = visibleRasterRange(this._map, level);
    if (!range) return;
    const cellSize = level.cell_size_m;
    const cornerRows: any[][] = [];

    // Adjacent cells share corners. Project every visible grid intersection once
    // per redraw rather than doing four proj4 transforms for every cell. The final
    // coarse cell on an odd-sized level is clipped to the original raster extent.
    for (let rowEdge = range.rowMin; rowEdge <= range.rowMax + 1; rowEdge += 1) {
      const northing = Math.min(data.grid.north, data.grid.south + rowEdge * cellSize);
      const cornerRow: any[] = [];
      for (let columnEdge = range.colMin; columnEdge <= range.colMax + 1; columnEdge += 1) {
        const easting = Math.min(data.grid.east, data.grid.west + columnEdge * cellSize);
        cornerRow.push(this._map.latLngToContainerPoint(bngToLatLng(easting, northing)));
      }
      cornerRows.push(cornerRow);
    }

    for (let row = range.rowMin; row <= range.rowMax; row += 1) {
      const localRow = row - range.rowMin;
      for (let column = range.colMin; column <= range.colMax; column += 1) {
        const index = rasterIndex(row, column, level.width);
        const value = level.values[index];
        if (value === data.raster.nodata) continue;

        const localColumn = column - range.colMin;
        const points = [
          cornerRows[localRow][localColumn],
          cornerRows[localRow][localColumn + 1],
          cornerRows[localRow + 1][localColumn + 1],
          cornerRows[localRow + 1][localColumn],
        ];

        context.beginPath();
        context.moveTo(points[0].x, points[0].y);
        for (let pointIndex = 1; pointIndex < points.length; pointIndex += 1) {
          context.lineTo(points[pointIndex].x, points[pointIndex].y);
        }
        context.closePath();
        context.globalAlpha = 0.62;
        context.fillStyle = colorForValue(value);
        context.fill();
        context.globalAlpha = 1;
        context.strokeStyle = "rgba(38,50,56,0.72)";
        context.lineWidth = 0.7;
        context.stroke();
      }
    }
  },

  _drawSelection(this: any, context: CanvasRenderingContext2D) {
    if (this._selectedIndex === null) return;
    const row = Math.floor(this._selectedIndex / data.grid.width);
    const column = this._selectedIndex % data.grid.width;
    if (row < 0 || row >= data.grid.height || column < 0 || column >= data.grid.width) return;

    const west = data.grid.west + column * data.grid.cell_size_m;
    const east = Math.min(data.grid.east, west + data.grid.cell_size_m);
    const south = data.grid.south + row * data.grid.cell_size_m;
    const north = Math.min(data.grid.north, south + data.grid.cell_size_m);
    const points = [
      bngToLatLng(west, south),
      bngToLatLng(east, south),
      bngToLatLng(east, north),
      bngToLatLng(west, north),
    ].map((latlng) => this._map.latLngToContainerPoint(latlng));

    context.beginPath();
    context.moveTo(points[0].x, points[0].y);
    for (let pointIndex = 1; pointIndex < points.length; pointIndex += 1) {
      context.lineTo(points[pointIndex].x, points[pointIndex].y);
    }
    context.closePath();
    context.globalAlpha = 1;
    context.strokeStyle = "#111";
    context.lineWidth = 2.2;
    context.stroke();
  },
});

const rasterLayer = new RasterCanvasLayer();
rasterLayer.addTo(map);
map.fitBounds(L.latLngBounds(data.grid.bounds_wgs84), { padding: [18, 18] });

map.on("click", (event: any) => {
  const cell = cellAtLatLng(event.latlng);
  if (!cell) {
    rasterLayer.setSelectedIndex(null);
    return;
  }
  rasterLayer.setSelectedIndex(cell.index);
  L.popup().setLatLng(bngToLatLng(cell.easting, cell.northing)).setContent(popupHtml(cell)).openOn(map);
});

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

const INFO_PANEL_STORAGE_KEY = "goldilocks.infoPanelCollapsed";
const info = L.control({ position: "topright" });
info.onAdd = () => {
  const div = L.DomUtil.create("div", "info-control");
  let initiallyCollapsed = window.matchMedia("(max-width: 600px)").matches;
  try {
    const storedState = window.localStorage.getItem(INFO_PANEL_STORAGE_KEY);
    if (storedState === "true") initiallyCollapsed = true;
    if (storedState === "false") initiallyCollapsed = false;
  } catch {
    // localStorage can be unavailable in privacy-restricted contexts; keep the responsive default.
  }
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
    try {
      window.localStorage.setItem(INFO_PANEL_STORAGE_KEY, String(collapsed));
    } catch {
      // The control still works when storage is unavailable; only persistence is lost.
    }
  });
  L.DomEvent.disableClickPropagation(div);
  L.DomEvent.disableScrollPropagation(div);
  return div;
};
info.addTo(map);
