import { gunzipSync } from "fflate";
import proj4 from "proj4";

declare const L: any;

type RasterLevelMeta = {
  level: number;
  width: number;
  height: number;
  cell_size_m: number;
  value_offset: number;
  value_count: number;
};

type RasterLevel = Omit<RasterLevelMeta, "value_offset" | "value_count"> & {
  values: Uint16Array;
};

type MetricCategory = {
  id: string;
  label: string;
  order: number;
};

type MetricTransport = {
  id: string;
  category_id: string;
  label: string;
  units: string;
  period: string;
  definition: string;
  source_variable: string;
  scale: number;
  offset: number;
  decimals: number;
  nodata: number;
  encoded_min: number;
  encoded_max: number;
  summary: { min: number; max: number };
  coverage: Record<string, unknown>;
  palette_reverse?: boolean;
  levels: RasterLevelMeta[];
  blob_encoding: "gzip+uint16le";
  raw_bytes: number;
  compressed_bytes: number;
  sha256_raw: string;
  blob_base64: string;
};

type GoldilocksData = {
  format_version: 3;
  grid: {
    crs: string;
    proj4: string;
    cell_size_m: number;
    width: number;
    height: number;
    cell_count: number;
    valid_cell_count: number;
    source_valid_cell_count?: number;
    west: number;
    south: number;
    east: number;
    north: number;
    row_order: "south_to_north";
    column_order: "west_to_east";
    bounds_wgs84: [number, number][];
  };
  categories: MetricCategory[];
  metrics: MetricTransport[];
  default_metric_id: string;
  preview_partial_sources: boolean;
  sources: {
    provider?: string;
    resolution?: string;
    historical_release?: string;
    provisional_release?: string;
    note?: string;
  };
  data_pruning: Array<{
    id: string;
    area: string;
    action: string;
    reason: string;
    audit_period: string;
    cells: Array<{ row: number; column: number; easting: number; northing: number }>;
  }>;
};

type MetricRuntime = {
  transport: MetricTransport;
  levels: RasterLevel[];
};

type RasterCell = {
  row: number;
  column: number;
  index: number;
  encodedValue: number;
  easting: number;
  northing: number;
  lat: number;
  lon: number;
};

type TilePoint = { x: number; y: number };

type DisplayRange = {
  minEncoded: number;
  maxEncoded: number;
};

type RasterProjectionLookup = {
  worldX: Float64Array;
  worldY: Float64Array;
  projectedCornerCount: number;
  initMs: number;
};

const PANEL_COLLAPSED_STORAGE_KEY = "goldilocks.infoPanelCollapsed";
const GRIDLINES_STORAGE_KEY = "goldilocks.showGridLines";
const SELECTED_METRIC_STORAGE_KEY = "goldilocks.selectedMetric";
const DISPLAY_RANGE_STORAGE_PREFIX = "goldilocks.displayRange.";
const LAYER_OPACITY_STORAGE_KEY = "goldilocks.layerOpacity";
const DEFAULT_LAYER_OPACITY = 0.62;
const LOD_MIN_CELL_PIXELS = 4;
const LOD_REFERENCE_LAT = 54.5;
const LOD_REFERENCE_LON = -2.0;
const PALETTE_BIN_COUNT = 64;
const WEB_MERCATOR_WORLD_SIZE_Z0 = 256;

const dataElement = document.getElementById("goldilocks-data");
if (!dataElement?.textContent) throw new Error("Embedded Goldilocks data was not found");
const data = JSON.parse(dataElement.textContent) as GoldilocksData;
dataElement.textContent = "";
if (data.format_version !== 3 || !data.metrics?.length || !data.categories?.length) {
  throw new Error("This frontend requires Goldilocks categorized multi-metric data format v3");
}
const categoryById = new Map(data.categories.map((category) => [category.id, category]));
if (categoryById.size !== data.categories.length) throw new Error("Metric category IDs must be unique");
for (const metric of data.metrics) {
  if (!categoryById.has(metric.category_id)) throw new Error(`Metric ${metric.id} references unknown category ${metric.category_id}`);
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
proj4.defs(data.grid.crs, data.grid.proj4);

function readStoredBoolean(key: string, fallback: boolean): boolean {
  try {
    const stored = window.localStorage.getItem(key);
    if (stored === "true") return true;
    if (stored === "false") return false;
  } catch {
    // localStorage can be unavailable in privacy-restricted contexts.
  }
  return fallback;
}

function writeStoredBoolean(key: string, value: boolean) {
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Controls still work without persistence.
  }
}

function readStoredString(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStoredString(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Controls still work without persistence.
  }
}

function readStoredLayerOpacity(): number {
  const stored = readStoredString(LAYER_OPACITY_STORAGE_KEY);
  if (stored === null) return DEFAULT_LAYER_OPACITY;
  const value = Number(stored);
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : DEFAULT_LAYER_OPACITY;
}

function writeStoredLayerOpacity(value: number) {
  writeStoredString(LAYER_OPACITY_STORAGE_KEY, Math.max(0, Math.min(1, value)).toFixed(2));
}

function fullDisplayRange(metric: MetricTransport): DisplayRange {
  return { minEncoded: metric.encoded_min, maxEncoded: metric.encoded_max };
}

function displayRangeStorageKey(metric: MetricTransport): string {
  return `${DISPLAY_RANGE_STORAGE_PREFIX}${metric.id}`;
}

function readStoredDisplayRange(metric: MetricTransport): DisplayRange {
  const full = fullDisplayRange(metric);
  try {
    const raw = window.localStorage.getItem(displayRangeStorageKey(metric));
    if (!raw) return full;
    const parsed = JSON.parse(raw) as Partial<DisplayRange>;
    if (!Number.isFinite(parsed.minEncoded) || !Number.isFinite(parsed.maxEncoded)) return full;
    const minEncoded = Math.max(full.minEncoded, Math.min(full.maxEncoded, Math.round(parsed.minEncoded as number)));
    const maxEncoded = Math.max(full.minEncoded, Math.min(full.maxEncoded, Math.round(parsed.maxEncoded as number)));
    if (minEncoded > maxEncoded || (full.minEncoded < full.maxEncoded && minEncoded === maxEncoded)) return full;
    return { minEncoded, maxEncoded };
  } catch {
    return full;
  }
}

function writeStoredDisplayRange(metric: MetricTransport, range: DisplayRange) {
  try {
    window.localStorage.setItem(displayRangeStorageKey(metric), JSON.stringify(range));
  } catch {
    // Controls still work without persistence.
  }
}

function clearStoredDisplayRange(metric: MetricTransport) {
  try {
    window.localStorage.removeItem(displayRangeStorageKey(metric));
  } catch {
    // Controls still work without persistence.
  }
}

function decodeBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function littleEndianUint16(bytes: Uint8Array): Uint16Array {
  if (bytes.byteLength % 2 !== 0) throw new Error("Metric blob has an odd byte length");
  const valueCount = bytes.byteLength / 2;
  const nativeLittleEndian = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;
  if (nativeLittleEndian && bytes.byteOffset % 2 === 0) {
    // fflate returns a Uint8Array containing the decompressed bytes. On normal
    // little-endian browser platforms the encoded uint16 representation is
    // already native, so make a view over that buffer rather than copying it.
    return new Uint16Array(bytes.buffer, bytes.byteOffset, valueCount);
  }
  const result = new Uint16Array(valueCount);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let index = 0; index < valueCount; index += 1) result[index] = view.getUint16(index * 2, true);
  return result;
}

const metricById = new Map(data.metrics.map((metric) => [metric.id, metric]));
const metricRuntimeCache = new Map<string, MetricRuntime>();

function decodeMetric(metric: MetricTransport): MetricRuntime {
  const cached = metricRuntimeCache.get(metric.id);
  if (cached) return cached;
  if (metric.blob_encoding !== "gzip+uint16le") {
    throw new Error(`Unsupported metric encoding ${metric.blob_encoding}`);
  }
  const started = performance.now();
  const compressed = decodeBase64(metric.blob_base64);
  const rawBytes = gunzipSync(compressed);
  if (rawBytes.byteLength !== metric.raw_bytes) {
    throw new Error(`Metric ${metric.id} decoded to ${rawBytes.byteLength} bytes; expected ${metric.raw_bytes}`);
  }
  const values = littleEndianUint16(rawBytes);
  const levels: RasterLevel[] = metric.levels.map((level, index) => {
    if (level.level !== index) throw new Error(`Unexpected LOD index ${level.level} for ${metric.id}`);
    if (level.value_count !== level.width * level.height) {
      throw new Error(`LOD ${index} dimensions do not match value count for ${metric.id}`);
    }
    const start = level.value_offset;
    const stop = start + level.value_count;
    if (stop > values.length) throw new Error(`LOD ${index} exceeds decoded metric buffer for ${metric.id}`);
    if (index === 0) {
      if (
        level.width !== data.grid.width ||
        level.height !== data.grid.height ||
        level.cell_size_m !== data.grid.cell_size_m
      ) {
        throw new Error(`LOD0 does not match base grid for ${metric.id}`);
      }
    } else {
      const previous = metric.levels[index - 1];
      if (level.width !== Math.ceil(previous.width / 2) || level.height !== Math.ceil(previous.height / 2)) {
        throw new Error(`LOD ${index} dimensions are not half the previous level for ${metric.id}`);
      }
    }
    return {
      level: level.level,
      width: level.width,
      height: level.height,
      cell_size_m: level.cell_size_m,
      values: values.subarray(start, stop),
    };
  });
  const runtime = { transport: metric, levels };
  metricRuntimeCache.set(metric.id, runtime);
  metric.blob_base64 = "";
  const elapsed = performance.now() - started;
  console.info(`Goldilocks metric decode ${metric.id}: ${elapsed.toFixed(1)} ms`);
  return runtime;
}

const LEGACY_METRIC_ID_MAP: Record<string, string> = {
  days_tmax_gt_25: "heat_days_tmax_gt_25",
  days_tmax_gt_28: "heat_days_tmax_gt_28",
  days_tmax_gt_30: "heat_days_tmax_gt_30",
  summer_tmax_p95: "heat_summer_tmax_p95",
  summer_tmax_p99: "heat_summer_tmax_p99",
  longest_run_tmax_gt_25: "heat_longest_run_tmax_gt_25",
  tropical_nights_tmin_gt_20: "heat_tropical_nights_tmin_gt_20",
};
const storedMetricId = readStoredString(SELECTED_METRIC_STORAGE_KEY);
const migratedStoredMetricId = storedMetricId ? (LEGACY_METRIC_ID_MAP[storedMetricId] ?? storedMetricId) : null;
if (storedMetricId && migratedStoredMetricId && migratedStoredMetricId !== storedMetricId) {
  writeStoredString(SELECTED_METRIC_STORAGE_KEY, migratedStoredMetricId);
}
const initialMetric =
  (migratedStoredMetricId ? metricById.get(migratedStoredMetricId) : undefined) ??
  metricById.get(data.default_metric_id) ??
  data.metrics[0];
let activeMetric = initialMetric;
let activeRuntime = decodeMetric(activeMetric);
let lodLevels = activeRuntime.levels;
let baseRasterValues = lodLevels[0].values;
let activeDisplayRange = readStoredDisplayRange(activeMetric);

function decodedMetricValue(metric: MetricTransport, encoded: number): number {
  return encoded * metric.scale + metric.offset;
}

function formatMetricValue(metric: MetricTransport, encoded: number): string {
  return `${decodedMetricValue(metric, encoded).toFixed(metric.decimals)} ${metric.units}`.trim();
}

function paletteBinForValue(value: number): number {
  const min = activeDisplayRange.minEncoded;
  const max = activeDisplayRange.maxEncoded;
  if (max === min) return Math.floor((PALETTE_BIN_COUNT - 1) / 2);
  const ratio = Math.max(0, Math.min(1, (value - min) / (max - min)));
  const visualRatio = activeMetric.palette_reverse ? 1 - ratio : ratio;
  return Math.min(PALETTE_BIN_COUNT - 1, Math.floor(visualRatio * PALETTE_BIN_COUNT));
}

function colorForPaletteBin(bin: number): string {
  const ratio = PALETTE_BIN_COUNT <= 1 ? 0.5 : bin / (PALETTE_BIN_COUNT - 1);
  const hue = 220 - ratio * 220;
  const lightness = 48 - 10 * Math.pow(1 - ratio, 2);
  return `hsl(${hue.toFixed(0)} 78% ${lightness.toFixed(1)}%)`;
}

function colorForValue(value: number): string {
  return colorForPaletteBin(paletteBinForValue(value));
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
  if (column < 0 || column >= data.grid.width || row < 0 || row >= data.grid.height) return null;
  const index = rasterIndex(row, column, data.grid.width);
  const encodedValue = baseRasterValues[index];
  if (encodedValue === activeMetric.nodata) return null;
  const centerEasting = data.grid.west + (column + 0.5) * data.grid.cell_size_m;
  const centerNorthing = data.grid.south + (row + 0.5) * data.grid.cell_size_m;
  const center = bngToLatLng(centerEasting, centerNorthing);
  return {
    row,
    column,
    index,
    encodedValue,
    easting: Math.round(centerEasting),
    northing: Math.round(centerNorthing),
    lat: center.lat,
    lon: center.lng,
  };
}

function popupHtml(cell: RasterCell): string {
  return `
    <strong>${activeMetric.label}</strong>
    <dl>
      <dt>Value</dt><dd>${formatMetricValue(activeMetric, cell.encodedValue)}</dd>
      <dt>Period</dt><dd>${activeMetric.period}</dd>
      <dt>Cell</dt><dd>BNG-${cell.easting}-${cell.northing}</dd>
      <dt>Raster</dt><dd>row ${cell.row}, column ${cell.column}</dd>
      <dt>BNG</dt><dd>E ${cell.easting.toLocaleString()}, N ${cell.northing.toLocaleString()}</dd>
      <dt>WGS84</dt><dd>${cell.lat.toFixed(5)}, ${cell.lon.toFixed(5)}</dd>
    </dl>`;
}

const map = L.map("map", {
  // Leaflet normally fades every newly ready GridLayer tile from opacity 0 to 1
  // over 200 ms. Our climate canvases render synchronously, so that fade only
  // makes redraws/zoom tile replacement look like a distracting white flash.
  fadeAnimation: false,
  zoomControl: false,
});
L.control.zoom({ position: "bottomright" }).addTo(map);
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

const lodReferenceBasePixelsAtZoom0 = (() => {
  const reference = L.latLng(LOD_REFERENCE_LAT, LOD_REFERENCE_LON);
  const [referenceEasting, referenceNorthing] = latLngToBng(reference);
  const baseCellEast = bngToLatLng(referenceEasting + data.grid.cell_size_m, referenceNorthing);
  return map.project(reference, 0).distanceTo(map.project(baseCellEast, 0));
})();

function chooseLodLevel(zoom = map.getZoom()): RasterLevel {
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
  const south = Math.max(bounds.getSouth(), gridWgs84Envelope.south);
  const north = Math.min(bounds.getNorth(), gridWgs84Envelope.north);
  const west = Math.max(bounds.getWest(), gridWgs84Envelope.west);
  const east = Math.min(bounds.getEast(), gridWgs84Envelope.east);
  if (south > north || west > east) return null;

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
  const rawColMin = Math.floor((Math.min(...eastings) - data.grid.west) / level.cell_size_m);
  const rawColMax = Math.floor((Math.max(...eastings) - data.grid.west) / level.cell_size_m);
  const rawRowMin = Math.floor((Math.min(...northings) - data.grid.south) / level.cell_size_m);
  const rawRowMax = Math.floor((Math.max(...northings) - data.grid.south) / level.cell_size_m);
  if (rawColMax < 0 || rawRowMax < 0 || rawColMin >= level.width || rawRowMin >= level.height) return null;
  return {
    colMin: Math.max(0, rawColMin - marginCells),
    rowMin: Math.max(0, rawRowMin - marginCells),
    colMax: Math.min(level.width - 1, rawColMax + marginCells),
    rowMax: Math.min(level.height - 1, rawRowMax + marginCells),
  };
}

const BASE_CORNER_STRIDE = data.grid.width + 1;
const BASE_CORNER_COUNT = BASE_CORNER_STRIDE * (data.grid.height + 1);

function baseCornerIndexForLodEdge(level: RasterLevel, rowEdge: number, columnEdge: number): number {
  const baseCellStep = Math.round(level.cell_size_m / data.grid.cell_size_m);
  const baseRow = Math.min(data.grid.height, rowEdge * baseCellStep);
  const baseColumn = Math.min(data.grid.width, columnEdge * baseCellStep);
  return baseRow * BASE_CORNER_STRIDE + baseColumn;
}

function worldPixelAtZoom0(lon: number, lat: number): [number, number] {
  const latitudeRadians = lat * Math.PI / 180;
  const x = WEB_MERCATOR_WORLD_SIZE_Z0 * (lon + 180) / 360;
  const y = WEB_MERCATOR_WORLD_SIZE_Z0 * (1 - Math.asinh(Math.tan(latitudeRadians)) / Math.PI) / 2;
  return [x, y];
}

function buildRasterProjectionLookup(): RasterProjectionLookup {
  const started = performance.now();
  const needed = new Uint8Array(BASE_CORNER_COUNT);
  for (const level of lodLevels) {
    for (let row = 0; row < level.height; row += 1) {
      const rowOffset = row * level.width;
      for (let column = 0; column < level.width; column += 1) {
        if (level.values[rowOffset + column] === activeMetric.nodata) continue;
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
): TilePoint {
  const index = baseCornerIndexForLodEdge(level, rowEdge, columnEdge);
  return {
    x: rasterProjectionLookup.worldX[index] * worldScale - tileOriginX,
    y: rasterProjectionLookup.worldY[index] * worldScale - tileOriginY,
  };
}

function appendCellPath(
  path: Path2D,
  southWest: TilePoint,
  southEast: TilePoint,
  northEast: TilePoint,
  northWest: TilePoint,
) {
  path.moveTo(southWest.x, southWest.y);
  path.lineTo(southEast.x, southEast.y);
  path.lineTo(northEast.x, northEast.y);
  path.lineTo(northWest.x, northWest.y);
  path.closePath();
}

const RasterGridLayer = L.GridLayer.extend({
  initialize(this: any, options: any) {
    L.GridLayer.prototype.initialize.call(this, options);
    this._showGridLines = options.showGridLines !== false;
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
    const level = chooseLodLevel(coords.z);
    canvas.dataset.lod = String(level.level);
    canvas.dataset.lodCellSizeM = String(level.cell_size_m);
    this._drawTile(context, coords, tileSize, level);
    return canvas;
  },

  setShowGridLines(this: any, show: boolean) {
    if (this._showGridLines === show) return;
    this._showGridLines = show;
    if (this._map) this.redraw();
  },

  repaintVisibleTiles(this: any) {
    if (!this._map || !this._tiles) return;
    const tileSize = this.getTileSize();
    for (const tile of Object.values(this._tiles) as Array<{ el: HTMLElement; coords: any }>) {
      if (!(tile.el instanceof HTMLCanvasElement)) continue;
      const context = tile.el.getContext("2d");
      if (!context) continue;
      context.clearRect(0, 0, tileSize.x, tileSize.y);
      const level = chooseLodLevel(tile.coords.z);
      tile.el.dataset.lod = String(level.level);
      tile.el.dataset.lodCellSizeM = String(level.cell_size_m);
      this._drawTile(context, tile.coords, tileSize, level);
    }
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
    const fillPaths = new Map<number, Path2D>();
    const gridPath = this._showGridLines ? new Path2D() : null;

    for (let row = range.rowMin; row <= range.rowMax; row += 1) {
      for (let column = range.colMin; column <= range.colMax; column += 1) {
        const index = rasterIndex(row, column, level.width);
        const value = level.values[index];
        if (value === activeMetric.nodata) continue;
        const southWest = cachedCornerTilePoint(level, row, column, worldScale, tileOriginX, tileOriginY);
        const southEast = cachedCornerTilePoint(level, row, column + 1, worldScale, tileOriginX, tileOriginY);
        const northEast = cachedCornerTilePoint(level, row + 1, column + 1, worldScale, tileOriginX, tileOriginY);
        const northWest = cachedCornerTilePoint(level, row + 1, column, worldScale, tileOriginX, tileOriginY);
        const bin = paletteBinForValue(value);
        let fillPath = fillPaths.get(bin);
        if (!fillPath) {
          fillPath = new Path2D();
          fillPaths.set(bin, fillPath);
        }
        appendCellPath(fillPath, southWest, southEast, northEast, northWest);
        if (gridPath) appendCellPath(gridPath, southWest, southEast, northEast, northWest);
      }
    }

    for (const [bin, path] of fillPaths) {
      context.fillStyle = colorForPaletteBin(bin);
      context.fill(path);
    }
    if (gridPath) {
      context.strokeStyle = "rgba(38,50,56,0.72)";
      context.lineWidth = 0.7;
      context.stroke(gridPath);
    }
  },
});

const initialGridLinesVisible = readStoredBoolean(GRIDLINES_STORAGE_KEY, false);
const initialLayerOpacity = readStoredLayerOpacity();
const rasterLayer = new RasterGridLayer({
  tileSize: 256,
  pane: "overlayPane",
  opacity: initialLayerOpacity,
  bounds: L.latLngBounds(data.grid.bounds_wgs84),
  noWrap: true,
  updateWhenIdle: false,
  updateWhenZooming: false,
  updateInterval: 100,
  keepBuffer: 2,
  className: "goldilocks-raster-grid",
  showGridLines: initialGridLinesVisible,
});
rasterLayer.addTo(map);


const selectionPane = map.createPane("goldilocks-selection");
selectionPane.style.zIndex = "450";
selectionPane.style.pointerEvents = "none";
const selectionOutline = L.polygon([], {
  pane: "goldilocks-selection",
  fill: false,
  color: "#111",
  weight: 2.2,
  opacity: 1,
  interactive: false,
}).addTo(map);

function setSelectedCell(cell: RasterCell | null) {
  if (!cell) {
    selectionOutline.setLatLngs([]);
    return;
  }
  const west = data.grid.west + cell.column * data.grid.cell_size_m;
  const south = data.grid.south + cell.row * data.grid.cell_size_m;
  const east = west + data.grid.cell_size_m;
  const north = south + data.grid.cell_size_m;
  selectionOutline.setLatLngs([
    bngToLatLng(west, south),
    bngToLatLng(east, south),
    bngToLatLng(east, north),
    bngToLatLng(west, north),
  ]);
}
map.fitBounds(L.latLngBounds(data.grid.bounds_wgs84), { padding: [18, 18] });

map.on("click", (event: any) => {
  const cell = cellAtLatLng(event.latlng);
  setSelectedCell(cell);
  if (!cell) return;
  L.popup().setLatLng(bngToLatLng(cell.easting, cell.northing)).setContent(popupHtml(cell)).openOn(map);
});

function displayRangeSampleValues(): number[] {
  const min = activeDisplayRange.minEncoded;
  const max = activeDisplayRange.maxEncoded;
  const steps = Math.min(5, Math.max(1, max - min + 1));
  const values: number[] = [];
  for (let index = 0; index < steps; index += 1) {
    values.push(Math.round(min + ((max - min) * index) / Math.max(1, steps - 1)));
  }
  return [...new Set(values)];
}

function formatMetricNumber(metric: MetricTransport, encoded: number): string {
  return decodedMetricValue(metric, encoded).toFixed(metric.decimals);
}

function displayRangePercent(metric: MetricTransport, encoded: number): number {
  const span = metric.encoded_max - metric.encoded_min;
  if (span <= 0) return 50;
  return ((encoded - metric.encoded_min) / span) * 100;
}

function sourceDescription(): string {
  const bits = [data.sources.historical_release, data.sources.provisional_release, data.sources.resolution].filter(Boolean);
  return bits.join("; ");
}

function pruningDescription(): string {
  return data.data_pruning.map((item) =>
    `QC pruning: ${item.cells.length} 1 km cells in ${item.area} excluded offline after severe Tmin/Tmax interpolation inconsistencies.`
  ).join("<br>");
}

const mapPanel = L.control({ position: "topleft" });
mapPanel.onAdd = () => {
  const div = L.DomUtil.create("section", "map-panel");
  const initiallyCollapsed = readStoredBoolean(
    PANEL_COLLAPSED_STORAGE_KEY,
    window.matchMedia("(max-width: 600px)").matches,
  );
  if (initiallyCollapsed) div.classList.add("collapsed");

  const metricGroups = [...data.categories]
    .sort((left, right) => left.order - right.order)
    .map((category) => {
      const rows = data.metrics
        .filter((metric) => metric.category_id === category.id)
        .map((metric) => `
          <label class="metric-option">
            <input type="radio" name="climate-metric" value="${metric.id}" ${metric.id === activeMetric.id ? "checked" : ""}>
            <span>${metric.label}</span>
          </label>`).join("");
      if (!rows) return "";
      return `
        <fieldset class="metric-category" data-category-id="${category.id}">
          <legend class="metric-category-title">${category.label}</legend>
          ${rows}
        </fieldset>`;
    }).join("");

  div.innerHTML = `
    <div class="map-panel-header">
      <div class="map-panel-heading">
        <h1 class="map-panel-title">Goldilocks Map</h1>
        <div class="map-panel-subtitle"></div>
      </div>
      <button class="map-panel-toggle" type="button" aria-label="${initiallyCollapsed ? "Expand" : "Collapse"} map panel" aria-expanded="${!initiallyCollapsed}">${initiallyCollapsed ? "+" : "−"}</button>
    </div>
    <div class="map-panel-body">
      ${data.preview_partial_sources ? '<div class="preview-warning">Preview build: metrics use only source months downloaded so far.</div>' : ""}
      <div class="panel-section">
        <strong class="panel-section-title">Climate measure</strong>
        <div class="metric-list">${metricGroups}</div>
      </div>
      <div class="panel-section">
        <label class="panel-option">
          <input class="gridlines-toggle" type="checkbox" ${initialGridLinesVisible ? "checked" : ""}>
          <span>Show gridlines</span>
        </label>
      </div>
      <div class="panel-section opacity-control">
        <div class="opacity-heading">
          <strong class="panel-section-title">Layer opacity</strong>
          <output class="opacity-output">${Math.round(initialLayerOpacity * 100)}%</output>
        </div>
        <input class="opacity-input" type="range" min="0" max="100" step="1" value="${Math.round(initialLayerOpacity * 100)}" aria-label="Climate layer opacity" aria-valuetext="${Math.round(initialLayerOpacity * 100)}%">
      </div>
      <div class="panel-section">
        <div class="range-heading">
          <strong class="panel-section-title">Display range</strong>
          <button class="range-reset" type="button">Reset</button>
        </div>
        <div class="display-range-control">
          <div class="range-current-values">
            <output class="range-min-output"></output>
            <output class="range-max-output"></output>
          </div>
          <div class="dual-range">
            <div class="range-track"></div>
            <div class="range-active"></div>
            <input class="range-input range-input-min" type="range" step="1" aria-label="Minimum display value">
            <input class="range-input range-input-max" type="range" step="1" aria-label="Maximum display value">
          </div>
          <div class="range-legend"></div>
          <div class="range-unit"></div>
        </div>
      </div>
      <div class="panel-section">
        <strong class="panel-section-title">About</strong>
        <div class="metric-description"></div>
      </div>
      <div class="panel-section">
        <strong class="panel-section-title">Data provenance</strong>
        <div class="metric-source"></div>
      </div>
    </div>`;

  const subtitle = div.querySelector(".map-panel-subtitle") as HTMLElement;
  const description = div.querySelector(".metric-description") as HTMLElement;
  const source = div.querySelector(".metric-source") as HTMLElement;
  const rangeMin = div.querySelector(".range-input-min") as HTMLInputElement;
  const rangeMax = div.querySelector(".range-input-max") as HTMLInputElement;
  const rangeMinOutput = div.querySelector(".range-min-output") as HTMLOutputElement;
  const rangeMaxOutput = div.querySelector(".range-max-output") as HTMLOutputElement;
  const rangeActive = div.querySelector(".range-active") as HTMLElement;
  const rangeLegend = div.querySelector(".range-legend") as HTMLElement;
  const rangeUnit = div.querySelector(".range-unit") as HTMLElement;
  const rangeReset = div.querySelector(".range-reset") as HTMLButtonElement;
  const opacityInput = div.querySelector(".opacity-input") as HTMLInputElement;
  const opacityOutput = div.querySelector(".opacity-output") as HTMLOutputElement;

  function refreshDisplayRangeControl() {
    const full = fullDisplayRange(activeMetric);
    const minPercent = displayRangePercent(activeMetric, activeDisplayRange.minEncoded);
    const maxPercent = displayRangePercent(activeMetric, activeDisplayRange.maxEncoded);
    for (const input of [rangeMin, rangeMax]) {
      input.min = String(full.minEncoded);
      input.max = String(full.maxEncoded);
      input.disabled = full.minEncoded === full.maxEncoded;
    }
    rangeMin.value = String(activeDisplayRange.minEncoded);
    rangeMax.value = String(activeDisplayRange.maxEncoded);
    rangeMin.setAttribute("aria-valuetext", formatMetricValue(activeMetric, activeDisplayRange.minEncoded));
    rangeMax.setAttribute("aria-valuetext", formatMetricValue(activeMetric, activeDisplayRange.maxEncoded));
    rangeMinOutput.textContent = formatMetricNumber(activeMetric, activeDisplayRange.minEncoded);
    rangeMaxOutput.textContent = formatMetricNumber(activeMetric, activeDisplayRange.maxEncoded);
    rangeActive.style.left = `${minPercent}%`;
    rangeActive.style.right = `${100 - maxPercent}%`;

    const samples = displayRangeSampleValues();
    const gradientStops = samples.map((value, index) => {
      const percent = samples.length <= 1 ? 50 : (index / (samples.length - 1)) * 100;
      return `${colorForValue(value)} ${percent}%`;
    });
    rangeActive.style.background = gradientStops.length > 1
      ? `linear-gradient(to right, ${gradientStops.join(", ")})`
      : (samples.length ? colorForValue(samples[0]) : "#888");
    rangeLegend.style.gridTemplateColumns = `repeat(${Math.max(1, samples.length)}, minmax(0, 1fr))`;
    rangeLegend.innerHTML = samples.map((value, index) => {
      const prefix = index === 0 && value > activeMetric.encoded_min
        ? "≤"
        : (index === samples.length - 1 && value < activeMetric.encoded_max ? "≥" : "");
      return `<div class="range-legend-item">
        <span class="range-legend-swatch" style="background:${colorForValue(value)}"></span>
        <span>${prefix}${formatMetricNumber(activeMetric, value)}</span>
      </div>`;
    }).join("");
    rangeUnit.textContent = activeMetric.units;
    rangeReset.disabled = activeDisplayRange.minEncoded === full.minEncoded && activeDisplayRange.maxEncoded === full.maxEncoded;
  }

  function refreshMetricText() {
    subtitle.textContent = `${activeMetric.label} · ${activeMetric.period}`;
    refreshDisplayRangeControl();
    description.textContent = activeMetric.definition;
    const pruning = pruningDescription();
    source.innerHTML = `
      ${data.sources.provider ?? "Met Office HadUK-Grid"}; ${sourceDescription()}.<br>
      Variable: <code>${activeMetric.source_variable}</code>.<br>
      ${data.sources.note ?? ""}${pruning ? `<br>${pruning}` : ""}`;
  }

  const panelButton = div.querySelector(".map-panel-toggle") as HTMLButtonElement;
  panelButton.addEventListener("click", () => {
    const collapsed = div.classList.toggle("collapsed");
    panelButton.textContent = collapsed ? "+" : "−";
    panelButton.setAttribute("aria-expanded", String(!collapsed));
    panelButton.setAttribute("aria-label", `${collapsed ? "Expand" : "Collapse"} map panel`);
    writeStoredBoolean(PANEL_COLLAPSED_STORAGE_KEY, collapsed);
  });

  const gridlinesCheckbox = div.querySelector(".gridlines-toggle") as HTMLInputElement;
  gridlinesCheckbox.addEventListener("change", () => {
    const show = gridlinesCheckbox.checked;
    rasterLayer.setShowGridLines(show);
    writeStoredBoolean(GRIDLINES_STORAGE_KEY, show);
  });

  opacityInput.addEventListener("input", () => {
    const percent = Math.max(0, Math.min(100, Math.round(Number(opacityInput.value))));
    const opacity = percent / 100;
    rasterLayer.setOpacity(opacity);
    opacityOutput.textContent = `${percent}%`;
    opacityInput.setAttribute("aria-valuetext", `${percent}%`);
    writeStoredLayerOpacity(opacity);
  });

  let pendingRangeRepaint: number | null = null;
  function scheduleRangeRepaint() {
    if (pendingRangeRepaint !== null) return;
    pendingRangeRepaint = window.requestAnimationFrame(() => {
      pendingRangeRepaint = null;
      rasterLayer.repaintVisibleTiles();
    });
  }

  function setActiveDisplayRange(range: DisplayRange, persist: boolean) {
    activeDisplayRange = range;
    if (persist) writeStoredDisplayRange(activeMetric, range);
    refreshDisplayRangeControl();
    scheduleRangeRepaint();
  }

  rangeMin.addEventListener("input", () => {
    const full = fullDisplayRange(activeMetric);
    const gap = full.minEncoded < full.maxEncoded ? 1 : 0;
    const requested = Math.round(Number(rangeMin.value));
    const minEncoded = Math.max(full.minEncoded, Math.min(requested, activeDisplayRange.maxEncoded - gap));
    if (minEncoded === activeDisplayRange.minEncoded) {
      rangeMin.value = String(minEncoded);
      return;
    }
    setActiveDisplayRange({ minEncoded, maxEncoded: activeDisplayRange.maxEncoded }, true);
  });

  rangeMax.addEventListener("input", () => {
    const full = fullDisplayRange(activeMetric);
    const gap = full.minEncoded < full.maxEncoded ? 1 : 0;
    const requested = Math.round(Number(rangeMax.value));
    const maxEncoded = Math.min(full.maxEncoded, Math.max(requested, activeDisplayRange.minEncoded + gap));
    if (maxEncoded === activeDisplayRange.maxEncoded) {
      rangeMax.value = String(maxEncoded);
      return;
    }
    setActiveDisplayRange({ minEncoded: activeDisplayRange.minEncoded, maxEncoded }, true);
  });

  rangeReset.addEventListener("click", () => {
    activeDisplayRange = fullDisplayRange(activeMetric);
    clearStoredDisplayRange(activeMetric);
    refreshDisplayRangeControl();
    scheduleRangeRepaint();
  });

  const metricInputs = Array.from(div.querySelectorAll('input[name="climate-metric"]') as NodeListOf<HTMLInputElement>);
  for (const input of metricInputs) {
    input.addEventListener("change", () => {
      if (!input.checked || input.value === activeMetric.id) return;
      const next = metricById.get(input.value);
      if (!next) return;
      const runtime = decodeMetric(next);
      activeMetric = next;
      activeRuntime = runtime;
      lodLevels = runtime.levels;
      baseRasterValues = lodLevels[0].values;
      activeDisplayRange = readStoredDisplayRange(next);
      writeStoredString(SELECTED_METRIC_STORAGE_KEY, next.id);
      map.closePopup();
      setSelectedCell(null);
      refreshMetricText();
      rasterLayer.redraw();
    });
  }

  refreshMetricText();
  L.DomEvent.disableClickPropagation(div);
  L.DomEvent.disableScrollPropagation(div);
  return div;
};
mapPanel.addTo(map);
