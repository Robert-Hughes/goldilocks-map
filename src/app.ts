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

function chooseLodLevel(mapInstance: any, zoom = mapInstance.getZoom()): RasterLevel {
  // Use a fixed representative UK location for the screen-size calculation.
  // Web Mercator scale varies with latitude, so using the live map centre made
  // LOD change merely by panning north/south at a fixed zoom. A fixed reference
  // removes that instability while still calculating pixel size at runtime.
  //
  // Deliberately use map.project() rather than latLngToContainerPoint(). Leaflet
  // rounds layer/container coordinates to whole CSS pixels; once a 1 km base cell
  // is sub-pixel, its two endpoints can round to the same pixel and falsely
  // measure as zero, which would select the coarsest LOD in one jump. project()
  // retains floating-point pixel coordinates at the requested zoom, so adjacent
  // zoom levels advance through adjacent LOD levels as intended.
  const reference = L.latLng(LOD_REFERENCE_LAT, LOD_REFERENCE_LON);
  const [referenceEasting, referenceNorthing] = latLngToBng(reference);
  const baseCellEast = bngToLatLng(referenceEasting + data.grid.cell_size_m, referenceNorthing);
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

function rasterRangeForBounds(
  bounds: any,
  level: RasterLevel,
  marginCells = 1,
): { rowMin: number; rowMax: number; colMin: number; colMax: number } | null {
  // Never project remote viewport/tile corners into BNG. Intersect in WGS84
  // first and transform only the small rectangle near our UK raster.
  const south = Math.max(bounds.getSouth(), gridWgs84Envelope.south);
  const north = Math.min(bounds.getNorth(), gridWgs84Envelope.north);
  const west = Math.max(bounds.getWest(), gridWgs84Envelope.west);
  const east = Math.min(bounds.getEast(), gridWgs84Envelope.east);
  if (south > north || west > east) return null;

  // Include edge midpoints as well as corners so BNG curvature does not require
  // us to assume extrema occur exactly at geographic rectangle corners.
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

  return {
    colMin: Math.max(0, rawColMin - marginCells),
    rowMin: Math.max(0, rawRowMin - marginCells),
    colMax: Math.min(level.width - 1, rawColMax + marginCells),
    rowMax: Math.min(level.height - 1, rawRowMax + marginCells),
  };
}

const RasterGridLayer = L.GridLayer.extend({
  initialize(this: any, options: any) {
    L.GridLayer.prototype.initialize.call(this, options);
    this._selectedIndex = null;
  },

  createTile(this: any, coords: any) {
    const tileSize = this.getTileSize();
    const pixelRatio = Math.max(1, window.devicePixelRatio || 1);
    const canvas = L.DomUtil.create("canvas", "goldilocks-raster-tile") as HTMLCanvasElement;
    canvas.width = Math.max(1, Math.round(tileSize.x * pixelRatio));
    canvas.height = Math.max(1, Math.round(tileSize.y * pixelRatio));
    canvas.style.width = `${tileSize.x}px`;
    canvas.style.height = `${tileSize.y}px`;
    canvas.style.pointerEvents = "none";

    const context = canvas.getContext("2d");
    if (!context) return canvas;
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);

    // Leaflet owns tile lifecycle and zoom animation. Each requested tile has a
    // definite integer map zoom, so all tiles at that zoom independently choose
    // the same climate LOD. updateWhenZooming=false keeps the old tile set/LOD
    // scaled during pinch and asks for the new zoom's tiles only when it settles.
    const level = chooseLodLevel(this._map, coords.z);
    canvas.dataset.lod = String(level.level);
    canvas.dataset.lodCellSizeM = String(level.cell_size_m);
    this._drawTile(context, coords, tileSize, level);
    this._drawSelectionTile(context, coords, tileSize);
    return canvas;
  },

  setSelectedIndex(this: any, index: number | null) {
    this._selectedIndex = index;
    if (this._map) this.redraw();
  },

  _tileBounds(this: any, coords: any, tileSize: any) {
    const northWestPoint = L.point(coords.x * tileSize.x, coords.y * tileSize.y);
    const southEastPoint = northWestPoint.add(tileSize);
    return L.latLngBounds(
      this._map.unproject(northWestPoint, coords.z),
      this._map.unproject(southEastPoint, coords.z),
    );
  },

  _toTilePoint(this: any, latlng: any, coords: any, tileSize: any) {
    const tileOrigin = L.point(coords.x * tileSize.x, coords.y * tileSize.y);
    return this._map.project(latlng, coords.z).subtract(tileOrigin);
  },

  _drawTile(
    this: any,
    context: CanvasRenderingContext2D,
    coords: any,
    tileSize: any,
    level: RasterLevel,
  ) {
    const range = rasterRangeForBounds(this._tileBounds(coords, tileSize), level, 1);
    if (!range) return;
    const cellSize = level.cell_size_m;
    const cornerRows: any[][] = [];

    // Tiles naturally clip drawing at their 256 px boundary. Include a one-cell
    // range margin so cells crossing a tile edge are drawn into both neighbouring
    // canvases; Leaflet's clipping then joins them without requiring shared state.
    // Shared BNG intersections are still projected only once within each tile.
    for (let rowEdge = range.rowMin; rowEdge <= range.rowMax + 1; rowEdge += 1) {
      const northing = Math.min(data.grid.north, data.grid.south + rowEdge * cellSize);
      const cornerRow: any[] = [];
      for (let columnEdge = range.colMin; columnEdge <= range.colMax + 1; columnEdge += 1) {
        const easting = Math.min(data.grid.east, data.grid.west + columnEdge * cellSize);
        cornerRow.push(this._toTilePoint(bngToLatLng(easting, northing), coords, tileSize));
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

  _drawSelectionTile(this: any, context: CanvasRenderingContext2D, coords: any, tileSize: any) {
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
    ].map((latlng) => this._toTilePoint(latlng, coords, tileSize));

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

const rasterLayer = new RasterGridLayer({
  tileSize: 256,
  pane: "overlayPane",
  bounds: L.latLngBounds(data.grid.bounds_wgs84),
  noWrap: true,
  updateWhenIdle: false,
  updateWhenZooming: false,
  updateInterval: 100,
  keepBuffer: 2,
  className: "goldilocks-raster-grid",
});
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
