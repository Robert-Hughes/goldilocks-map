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

const map = L.map("map");

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

// This reference distance is fixed geographically, so calculate its projection
// once. Web Mercator doubles in scale for each Leaflet zoom level; tile LOD
// selection therefore only needs a multiplication by 2^zoom thereafter.
const lodReferenceBasePixelsAtZoom0 = (() => {
  const reference = L.latLng(LOD_REFERENCE_LAT, LOD_REFERENCE_LON);
  const [referenceEasting, referenceNorthing] = latLngToBng(reference);
  const baseCellEast = bngToLatLng(referenceEasting + data.grid.cell_size_m, referenceNorthing);
  return map.project(reference, 0).distanceTo(map.project(baseCellEast, 0));
})();

function chooseLodLevel(_mapInstance: any, zoom = _mapInstance.getZoom()): RasterLevel {
  // Use a fixed representative UK location for the screen-size calculation.
  // Web Mercator scale varies with latitude, so using the live map centre made
  // LOD change merely by panning north/south at a fixed zoom. Keeping the
  // reference projection fixed also means tile creation performs no projection
  // work just to select an LOD.
  const basePixels = lodReferenceBasePixelsAtZoom0 * Math.pow(2, zoom);

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

const BASE_CORNER_STRIDE = data.grid.width + 1;
const BASE_CORNER_COUNT = BASE_CORNER_STRIDE * (data.grid.height + 1);
const WEB_MERCATOR_WORLD_SIZE_Z0 = 256;

type RasterProjectionLookup = {
  worldX: Float64Array;
  worldY: Float64Array;
  projectedCornerCount: number;
  initMs: number;
};

function baseCornerIndexForLodEdge(level: RasterLevel, rowEdge: number, columnEdge: number): number {
  const baseCellStep = Math.round(level.cell_size_m / data.grid.cell_size_m);
  const baseRow = Math.min(data.grid.height, rowEdge * baseCellStep);
  const baseColumn = Math.min(data.grid.width, columnEdge * baseCellStep);
  return baseRow * BASE_CORNER_STRIDE + baseColumn;
}

function worldPixelAtZoom0(lon: number, lat: number): [number, number] {
  // Leaflet's default CRS is EPSG:3857. At zoom 0 its world is 256 px square;
  // subsequent integer zooms are exact powers-of-two scalings of these values.
  const latitudeRadians = lat * Math.PI / 180;
  const x = WEB_MERCATOR_WORLD_SIZE_Z0 * (lon + 180) / 360;
  const y = WEB_MERCATOR_WORLD_SIZE_Z0 * (
    1 - Math.asinh(Math.tan(latitudeRadians)) / Math.PI
  ) / 2;
  return [x, y];
}

function buildRasterProjectionLookup(): RasterProjectionLookup {
  const started = performance.now();
  const needed = new Uint8Array(BASE_CORNER_COUNT);

  // Coarse LOD cell edges are always base-grid edges at multiples of 2^LOD,
  // clipped at the source extent. Mark the four base corners of every valid cell
  // across every LOD so sea-only geometry never pays a projection cost.
  for (const level of lodLevels) {
    for (let row = 0; row < level.height; row += 1) {
      const rowOffset = row * level.width;
      for (let column = 0; column < level.width; column += 1) {
        if (level.values[rowOffset + column] === data.raster.nodata) continue;
        needed[baseCornerIndexForLodEdge(level, row, column)] = 1;
        needed[baseCornerIndexForLodEdge(level, row, column + 1)] = 1;
        needed[baseCornerIndexForLodEdge(level, row + 1, column)] = 1;
        needed[baseCornerIndexForLodEdge(level, row + 1, column + 1)] = 1;
      }
    }
  }

  const worldX = new Float64Array(BASE_CORNER_COUNT);
  const worldY = new Float64Array(BASE_CORNER_COUNT);
  worldX.fill(Number.NaN);
  worldY.fill(Number.NaN);

  let projectedCornerCount = 0;
  for (let index = 0; index < BASE_CORNER_COUNT; index += 1) {
    if (!needed[index]) continue;
    const baseRow = Math.floor(index / BASE_CORNER_STRIDE);
    const baseColumn = index - baseRow * BASE_CORNER_STRIDE;
    const easting = data.grid.west + baseColumn * data.grid.cell_size_m;
    const northing = data.grid.south + baseRow * data.grid.cell_size_m;
    const [lon, lat] = proj4(data.grid.crs, "EPSG:4326", [easting, northing]) as [number, number];
    const [x, y] = worldPixelAtZoom0(lon, lat);
    worldX[index] = x;
    worldY[index] = y;
    projectedCornerCount += 1;
  }

  const initMs = performance.now() - started;
  document.documentElement.dataset.goldilocksProjectionCorners = String(projectedCornerCount);
  document.documentElement.dataset.goldilocksProjectionInitMs = initMs.toFixed(1);
  console.info(
    `Goldilocks projection lookup: ${projectedCornerCount.toLocaleString()} / ${BASE_CORNER_COUNT.toLocaleString()} corners in ${initMs.toFixed(1)} ms`,
  );
  return { worldX, worldY, projectedCornerCount, initMs };
}

const rasterProjectionLookup = buildRasterProjectionLookup();

function cachedCornerTilePoint(
  level: RasterLevel,
  rowEdge: number,
  columnEdge: number,
  worldScale: number,
  tileOriginX: number,
  tileOriginY: number,
): { x: number; y: number } {
  const index = baseCornerIndexForLodEdge(level, rowEdge, columnEdge);
  return {
    x: rasterProjectionLookup.worldX[index] * worldScale - tileOriginX,
    y: rasterProjectionLookup.worldY[index] * worldScale - tileOriginY,
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

  _drawTile(
    this: any,
    context: CanvasRenderingContext2D,
    coords: any,
    tileSize: any,
    level: RasterLevel,
  ) {
    const range = rasterRangeForBounds(this._tileBounds(coords, tileSize), level, 1);
    if (!range) return;

    const worldScale = Math.pow(2, coords.z);
    const tileOriginX = coords.x * tileSize.x;
    const tileOriginY = coords.y * tileSize.y;

    // Tiles naturally clip drawing at their 256 px boundary. Include a one-cell
    // range margin so cells crossing an edge are drawn into both neighbours. Grid
    // geometry itself is never reprojected here: each valid cell resolves its four
    // corners from the startup world-coordinate lookup and only applies the cheap
    // zoom scale + tile-origin translation.
    for (let row = range.rowMin; row <= range.rowMax; row += 1) {
      for (let column = range.colMin; column <= range.colMax; column += 1) {
        const index = rasterIndex(row, column, level.width);
        const value = level.values[index];
        if (value === data.raster.nodata) continue;

        const southWest = cachedCornerTilePoint(level, row, column, worldScale, tileOriginX, tileOriginY);
        const southEast = cachedCornerTilePoint(level, row, column + 1, worldScale, tileOriginX, tileOriginY);
        const northEast = cachedCornerTilePoint(level, row + 1, column + 1, worldScale, tileOriginX, tileOriginY);
        const northWest = cachedCornerTilePoint(level, row + 1, column, worldScale, tileOriginX, tileOriginY);

        context.beginPath();
        context.moveTo(southWest.x, southWest.y);
        context.lineTo(southEast.x, southEast.y);
        context.lineTo(northEast.x, northEast.y);
        context.lineTo(northWest.x, northWest.y);
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

    const worldScale = Math.pow(2, coords.z);
    const tileOriginX = coords.x * tileSize.x;
    const tileOriginY = coords.y * tileSize.y;
    const level = lodLevels[0];
    const points = [
      cachedCornerTilePoint(level, row, column, worldScale, tileOriginX, tileOriginY),
      cachedCornerTilePoint(level, row, column + 1, worldScale, tileOriginX, tileOriginY),
      cachedCornerTilePoint(level, row + 1, column + 1, worldScale, tileOriginX, tileOriginY),
      cachedCornerTilePoint(level, row + 1, column, worldScale, tileOriginX, tileOriginY),
    ];

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
