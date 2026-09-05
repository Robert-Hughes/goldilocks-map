import proj4 from "proj4";

import { MetricState } from "./metrics";
import type { GoldilocksData, RasterCell, RasterLevel } from "./types";

declare const L: any;

type TilePoint = { x: number; y: number };

type RasterProjectionLookup = {
  worldX: Float64Array;
  worldY: Float64Array;
  projectedCornerCount: number;
  initMs: number;
};

type RasterViewOptions = {
  showGridLines: boolean;
  opacity: number;
};

const LOD_MIN_CELL_PIXELS = 4;
const LOD_REFERENCE_LAT = 54.5;
const LOD_REFERENCE_LON = -2.0;
const WEB_MERCATOR_WORLD_SIZE_Z0 = 256;

export class RasterView {
  readonly layer: any;

  private readonly lodReferenceBasePixelsAtZoom0: number;
  private readonly gridWgs84Envelope: { south: number; north: number; west: number; east: number };
  private readonly baseCornerStride: number;
  private readonly baseCornerCount: number;
  private readonly rasterProjectionLookup: RasterProjectionLookup;
  private readonly selectionOutline: any;

  constructor(
    readonly map: any,
    readonly data: GoldilocksData,
    readonly metrics: MetricState,
    options: RasterViewOptions,
  ) {
    proj4.defs(data.grid.crs, data.grid.proj4);

    const reference = L.latLng(LOD_REFERENCE_LAT, LOD_REFERENCE_LON);
    const [referenceEasting, referenceNorthing] = this.latLngToBng(reference);
    const baseCellEast = this.bngToLatLng(referenceEasting + data.grid.cell_size_m, referenceNorthing);
    this.lodReferenceBasePixelsAtZoom0 = map.project(reference, 0).distanceTo(map.project(baseCellEast, 0));

    const latitudes = data.grid.bounds_wgs84.map(([lat]) => lat);
    const longitudes = data.grid.bounds_wgs84.map(([, lon]) => lon);
    this.gridWgs84Envelope = {
      south: Math.min(...latitudes),
      north: Math.max(...latitudes),
      west: Math.min(...longitudes),
      east: Math.max(...longitudes),
    };

    this.baseCornerStride = data.grid.width + 1;
    this.baseCornerCount = this.baseCornerStride * (data.grid.height + 1);
    this.rasterProjectionLookup = this.buildRasterProjectionLookup();

    const owner = this;
    const RasterGridLayer = L.GridLayer.extend({
      initialize(this: any, layerOptions: any) {
        L.GridLayer.prototype.initialize.call(this, layerOptions);
        this._showGridLines = layerOptions.showGridLines !== false;
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
        const level = owner.chooseLodLevel(coords.z);
        canvas.dataset.lod = String(level.level);
        canvas.dataset.lodCellSizeM = String(level.cell_size_m);
        canvas.dataset.pixelRatio = String(pixelRatio);
        this._drawTile(context, coords, tileSize, level, pixelRatio);
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
          const pixelRatio = Number(tile.el.dataset.pixelRatio) || 1;
          context.clearRect(0, 0, tileSize.x, tileSize.y);
          const level = owner.chooseLodLevel(tile.coords.z);
          tile.el.dataset.lod = String(level.level);
          tile.el.dataset.lodCellSizeM = String(level.cell_size_m);
          this._drawTile(context, tile.coords, tileSize, level, pixelRatio);
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
        pixelRatio: number,
      ) {
        const range = owner.rasterRangeForBounds(this._tileBounds(coords, tileSize), level, 1);
        if (!range) return;
        const worldScale = Math.pow(2, coords.z);
        const tileOriginX = coords.x * tileSize.x;
        const tileOriginY = coords.y * tileSize.y;
        // Slightly overlap adjacent fills to hide Canvas 2D anti-aliasing seams.
        const fillExpansionCssPx = 0.5 / pixelRatio;
        const fillPaths = new Map<number, Path2D>();
        const gridPath = this._showGridLines ? new Path2D() : null;

        for (let row = range.rowMin; row <= range.rowMax; row += 1) {
          for (let column = range.colMin; column <= range.colMax; column += 1) {
            const index = owner.rasterIndex(row, column, level.width);
            const value = level.values[index];
            if (value === owner.metrics.activeMetric.nodata) continue;
            const southWest = owner.cachedCornerTilePoint(level, row, column, worldScale, tileOriginX, tileOriginY);
            const southEast = owner.cachedCornerTilePoint(level, row, column + 1, worldScale, tileOriginX, tileOriginY);
            const northEast = owner.cachedCornerTilePoint(level, row + 1, column + 1, worldScale, tileOriginX, tileOriginY);
            const northWest = owner.cachedCornerTilePoint(level, row + 1, column, worldScale, tileOriginX, tileOriginY);
            const bin = owner.metrics.paletteBinForValue(value);
            let fillPath = fillPaths.get(bin);
            if (!fillPath) {
              fillPath = new Path2D();
              fillPaths.set(bin, fillPath);
            }
            owner.appendExpandedCellPath(
              fillPath,
              southWest,
              southEast,
              northEast,
              northWest,
              fillExpansionCssPx,
            );
            if (gridPath) owner.appendCellPath(gridPath, southWest, southEast, northEast, northWest);
          }
        }

        for (const [bin, path] of fillPaths) {
          context.fillStyle = owner.metrics.colorForPaletteBin(bin);
          context.fill(path);
        }
        if (gridPath) {
          context.strokeStyle = "rgba(38,50,56,0.72)";
          context.lineWidth = 0.7;
          context.stroke(gridPath);
        }
      },
    });

    this.layer = new RasterGridLayer({
      tileSize: 256,
      pane: "overlayPane",
      opacity: options.opacity,
      bounds: L.latLngBounds(data.grid.bounds_wgs84),
      noWrap: true,
      updateWhenIdle: false,
      updateWhenZooming: false,
      updateInterval: 100,
      keepBuffer: 2,
      className: "goldilocks-raster-grid",
      showGridLines: options.showGridLines,
    });
    this.layer.addTo(map);

    const selectionPane = map.createPane("goldilocks-selection");
    selectionPane.style.zIndex = "450";
    selectionPane.style.pointerEvents = "none";
    this.selectionOutline = L.polygon([], {
      pane: "goldilocks-selection",
      fill: false,
      color: "#111",
      weight: 2.2,
      opacity: 1,
      interactive: false,
    }).addTo(map);
  }

  setShowGridLines(show: boolean) {
    this.layer.setShowGridLines(show);
  }

  setOpacity(opacity: number) {
    this.layer.setOpacity(opacity);
  }

  repaintVisibleTiles() {
    this.layer.repaintVisibleTiles();
  }

  redraw() {
    this.layer.redraw();
  }

  latLngToBng(latlng: any): [number, number] {
    return proj4("EPSG:4326", this.data.grid.crs, [latlng.lng, latlng.lat]) as [number, number];
  }

  bngToLatLng(easting: number, northing: number): any {
    const [lon, lat] = proj4(this.data.grid.crs, "EPSG:4326", [easting, northing]) as [number, number];
    return L.latLng(lat, lon);
  }

  cellAtLatLng(latlng: any): RasterCell | null {
    const [easting, northing] = this.latLngToBng(latlng);
    const column = Math.floor((easting - this.data.grid.west) / this.data.grid.cell_size_m);
    const row = Math.floor((northing - this.data.grid.south) / this.data.grid.cell_size_m);
    if (column < 0 || column >= this.data.grid.width || row < 0 || row >= this.data.grid.height) return null;
    const index = this.rasterIndex(row, column, this.data.grid.width);
    const encodedValue = this.metrics.baseRasterValues[index];
    if (encodedValue === this.metrics.activeMetric.nodata) return null;
    const centerEasting = this.data.grid.west + (column + 0.5) * this.data.grid.cell_size_m;
    const centerNorthing = this.data.grid.south + (row + 0.5) * this.data.grid.cell_size_m;
    const center = this.bngToLatLng(centerEasting, centerNorthing);
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

  popupHtml(cell: RasterCell): string {
    return `
      <strong>${this.metrics.activeMetric.label}</strong>
      <dl>
        <dt>Value</dt><dd>${this.metrics.formatMetricValue(this.metrics.activeMetric, cell.encodedValue)}</dd>
        <dt>Period</dt><dd>${this.metrics.activeMetric.period}</dd>
        <dt>Cell</dt><dd>BNG-${cell.easting}-${cell.northing}</dd>
        <dt>Raster</dt><dd>row ${cell.row}, column ${cell.column}</dd>
        <dt>BNG</dt><dd>E ${cell.easting.toLocaleString()}, N ${cell.northing.toLocaleString()}</dd>
        <dt>WGS84</dt><dd>${cell.lat.toFixed(5)}, ${cell.lon.toFixed(5)}</dd>
      </dl>`;
  }

  setSelectedCell(cell: RasterCell | null) {
    if (!cell) {
      this.selectionOutline.setLatLngs([]);
      return;
    }
    const west = this.data.grid.west + cell.column * this.data.grid.cell_size_m;
    const south = this.data.grid.south + cell.row * this.data.grid.cell_size_m;
    const east = west + this.data.grid.cell_size_m;
    const north = south + this.data.grid.cell_size_m;
    this.selectionOutline.setLatLngs([
      this.bngToLatLng(west, south),
      this.bngToLatLng(east, south),
      this.bngToLatLng(east, north),
      this.bngToLatLng(west, north),
    ]);
  }

  private chooseLodLevel(zoom = this.map.getZoom()): RasterLevel {
    const basePixels = this.lodReferenceBasePixelsAtZoom0 * Math.pow(2, zoom);
    let levelIndex = 0;
    while (
      levelIndex + 1 < this.metrics.lodLevels.length &&
      basePixels * Math.pow(2, levelIndex) < LOD_MIN_CELL_PIXELS
    ) {
      levelIndex += 1;
    }
    return this.metrics.lodLevels[levelIndex];
  }

  private rasterRangeForBounds(
    bounds: any,
    level: RasterLevel,
    marginCells = 1,
  ): { rowMin: number; rowMax: number; colMin: number; colMax: number } | null {
    const south = Math.max(bounds.getSouth(), this.gridWgs84Envelope.south);
    const north = Math.min(bounds.getNorth(), this.gridWgs84Envelope.north);
    const west = Math.max(bounds.getWest(), this.gridWgs84Envelope.west);
    const east = Math.min(bounds.getEast(), this.gridWgs84Envelope.east);
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
    ].map((latlng) => this.latLngToBng(latlng));
    const eastings = projected.map(([easting]) => easting);
    const northings = projected.map(([, northing]) => northing);
    const rawColMin = Math.floor((Math.min(...eastings) - this.data.grid.west) / level.cell_size_m);
    const rawColMax = Math.floor((Math.max(...eastings) - this.data.grid.west) / level.cell_size_m);
    const rawRowMin = Math.floor((Math.min(...northings) - this.data.grid.south) / level.cell_size_m);
    const rawRowMax = Math.floor((Math.max(...northings) - this.data.grid.south) / level.cell_size_m);
    if (rawColMax < 0 || rawRowMax < 0 || rawColMin >= level.width || rawRowMin >= level.height) return null;
    return {
      colMin: Math.max(0, rawColMin - marginCells),
      rowMin: Math.max(0, rawRowMin - marginCells),
      colMax: Math.min(level.width - 1, rawColMax + marginCells),
      rowMax: Math.min(level.height - 1, rawRowMax + marginCells),
    };
  }

  private rasterIndex(row: number, column: number, width: number): number {
    return row * width + column;
  }

  private baseCornerIndexForLodEdge(level: RasterLevel, rowEdge: number, columnEdge: number): number {
    const baseCellStep = Math.round(level.cell_size_m / this.data.grid.cell_size_m);
    const baseRow = Math.min(this.data.grid.height, rowEdge * baseCellStep);
    const baseColumn = Math.min(this.data.grid.width, columnEdge * baseCellStep);
    return baseRow * this.baseCornerStride + baseColumn;
  }

  private worldPixelAtZoom0(lon: number, lat: number): [number, number] {
    const latitudeRadians = lat * Math.PI / 180;
    const x = WEB_MERCATOR_WORLD_SIZE_Z0 * (lon + 180) / 360;
    const y = WEB_MERCATOR_WORLD_SIZE_Z0 * (1 - Math.asinh(Math.tan(latitudeRadians)) / Math.PI) / 2;
    return [x, y];
  }

  private buildRasterProjectionLookup(): RasterProjectionLookup {
    const started = performance.now();
    const needed = new Uint8Array(this.baseCornerCount);
    for (const level of this.metrics.geometryRuntime.levels) {
      for (let row = 0; row < level.height; row += 1) {
        const rowOffset = row * level.width;
        for (let column = 0; column < level.width; column += 1) {
          if (level.values[rowOffset + column] === this.metrics.geometryReference.nodata) continue;
          needed[this.baseCornerIndexForLodEdge(level, row, column)] = 1;
          needed[this.baseCornerIndexForLodEdge(level, row, column + 1)] = 1;
          needed[this.baseCornerIndexForLodEdge(level, row + 1, column)] = 1;
          needed[this.baseCornerIndexForLodEdge(level, row + 1, column + 1)] = 1;
        }
      }
    }

    const worldX = new Float64Array(this.baseCornerCount);
    const worldY = new Float64Array(this.baseCornerCount);
    worldX.fill(Number.NaN);
    worldY.fill(Number.NaN);
    let projectedCornerCount = 0;
    for (let index = 0; index < this.baseCornerCount; index += 1) {
      if (!needed[index]) continue;
      const baseRow = Math.floor(index / this.baseCornerStride);
      const baseColumn = index - baseRow * this.baseCornerStride;
      const easting = this.data.grid.west + baseColumn * this.data.grid.cell_size_m;
      const northing = this.data.grid.south + baseRow * this.data.grid.cell_size_m;
      const [lon, lat] = proj4(this.data.grid.crs, "EPSG:4326", [easting, northing]) as [number, number];
      const [x, y] = this.worldPixelAtZoom0(lon, lat);
      worldX[index] = x;
      worldY[index] = y;
      projectedCornerCount += 1;
    }
    const initMs = performance.now() - started;
    document.documentElement.dataset.goldilocksProjectionCorners = String(projectedCornerCount);
    document.documentElement.dataset.goldilocksProjectionInitMs = initMs.toFixed(1);
    console.info(
      `Goldilocks projection lookup: ${projectedCornerCount.toLocaleString()} / ${this.baseCornerCount.toLocaleString()} corners in ${initMs.toFixed(1)} ms`,
    );
    return { worldX, worldY, projectedCornerCount, initMs };
  }

  private cachedCornerTilePoint(
    level: RasterLevel,
    rowEdge: number,
    columnEdge: number,
    worldScale: number,
    tileOriginX: number,
    tileOriginY: number,
  ): TilePoint {
    const index = this.baseCornerIndexForLodEdge(level, rowEdge, columnEdge);
    return {
      x: this.rasterProjectionLookup.worldX[index] * worldScale - tileOriginX,
      y: this.rasterProjectionLookup.worldY[index] * worldScale - tileOriginY,
    };
  }

  private appendCellPath(
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

  private appendExpandedCellPath(
    path: Path2D,
    southWest: TilePoint,
    southEast: TilePoint,
    northEast: TilePoint,
    northWest: TilePoint,
    expand: number,
  ) {
    const cx = (southWest.x + southEast.x + northEast.x + northWest.x) * 0.25;
    const cy = (southWest.y + southEast.y + northEast.y + northWest.y) * 0.25;
    path.moveTo(
      southWest.x + (southWest.x < cx ? -expand : expand),
      southWest.y + (southWest.y < cy ? -expand : expand),
    );
    path.lineTo(
      southEast.x + (southEast.x < cx ? -expand : expand),
      southEast.y + (southEast.y < cy ? -expand : expand),
    );
    path.lineTo(
      northEast.x + (northEast.x < cx ? -expand : expand),
      northEast.y + (northEast.y < cy ? -expand : expand),
    );
    path.lineTo(
      northWest.x + (northWest.x < cx ? -expand : expand),
      northWest.y + (northWest.y < cy ? -expand : expand),
    );
    path.closePath();
  }
}
