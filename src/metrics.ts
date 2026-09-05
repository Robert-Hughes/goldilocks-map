import { gunzipSync } from "fflate";

import { readStoredString, writeStoredString } from "./storage";
import type { DisplayRange, GoldilocksData, MetricRuntime, MetricTransport } from "./types";

const SELECTED_METRIC_STORAGE_KEY = "goldilocks.selectedMetric";
const DISPLAY_RANGE_STORAGE_PREFIX = "goldilocks.displayRange.";
const PALETTE_BIN_COUNT = 64;

const LEGACY_METRIC_ID_MAP: Record<string, string> = {
  days_tmax_gt_25: "heat_days_tmax_gt_25",
  days_tmax_gt_28: "heat_days_tmax_gt_28",
  days_tmax_gt_30: "heat_days_tmax_gt_30",
  summer_tmax_p95: "heat_summer_tmax_p95",
  summer_tmax_p99: "heat_summer_tmax_p99",
  longest_run_tmax_gt_25: "heat_longest_run_tmax_gt_25",
  tropical_nights_tmin_gt_20: "heat_tropical_nights_tmin_gt_20",
};

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

function parseHexColour(colour: string): [number, number, number] | null {
  const match = /^#([0-9a-f]{6})$/i.exec(colour);
  if (!match) return null;
  const value = Number.parseInt(match[1], 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

export class MetricState {
  readonly metricById: Map<string, MetricTransport>;
  readonly geometryReference: MetricTransport;
  readonly geometryRuntime: MetricRuntime;

  activeMetric: MetricTransport;
  activeRuntime: MetricRuntime;
  activeDisplayRange: DisplayRange;

  private readonly runtimeCache = new Map<string, MetricRuntime>();
  private readonly paletteCache = new Map<string, string[]>();

  constructor(readonly data: GoldilocksData) {
    this.metricById = new Map(data.metrics.map((metric) => [metric.id, metric]));
    this.geometryReference = data.metrics[0];

    // The first metric defines the canonical land geometry used for cached map projection.
    // Individual datasets may contain additional nodata cells, so this must not depend on
    // whichever metric happened to be restored from localStorage at startup.
    this.geometryRuntime = this.decodeMetric(this.geometryReference);

    const storedMetricId = readStoredString(SELECTED_METRIC_STORAGE_KEY);
    const migratedStoredMetricId = storedMetricId ? (LEGACY_METRIC_ID_MAP[storedMetricId] ?? storedMetricId) : null;
    if (storedMetricId && migratedStoredMetricId && migratedStoredMetricId !== storedMetricId) {
      writeStoredString(SELECTED_METRIC_STORAGE_KEY, migratedStoredMetricId);
    }
    const initialMetric =
      (migratedStoredMetricId ? this.metricById.get(migratedStoredMetricId) : undefined) ??
      this.metricById.get(data.default_metric_id) ??
      data.metrics[0];
    this.activeMetric = initialMetric;
    this.activeRuntime = this.decodeMetric(initialMetric);
    this.activeDisplayRange = this.readStoredDisplayRange(initialMetric);
  }

  get lodLevels() {
    return this.activeRuntime.levels;
  }

  get baseRasterValues() {
    return this.activeRuntime.levels[0].values;
  }

  selectMetric(metricId: string): boolean {
    if (metricId === this.activeMetric.id) return false;
    const next = this.metricById.get(metricId);
    if (!next) return false;
    this.activeMetric = next;
    this.activeRuntime = this.decodeMetric(next);
    this.activeDisplayRange = this.readStoredDisplayRange(next);
    writeStoredString(SELECTED_METRIC_STORAGE_KEY, next.id);
    return true;
  }

  decodedMetricValue(metric: MetricTransport, encoded: number): number {
    return encoded * metric.scale + metric.offset;
  }

  formatMetricValue(metric: MetricTransport, encoded: number): string {
    return `${this.decodedMetricValue(metric, encoded).toFixed(metric.decimals)} ${metric.units}`.trim();
  }

  formatMetricNumber(metric: MetricTransport, encoded: number): string {
    return this.decodedMetricValue(metric, encoded).toFixed(metric.decimals);
  }

  fullDisplayRange(metric: MetricTransport): DisplayRange {
    if (metric.display_range) {
      const minEncoded = Math.round((metric.display_range.min - metric.offset) / metric.scale);
      const maxEncoded = Math.round((metric.display_range.max - metric.offset) / metric.scale);
      if (
        Number.isFinite(minEncoded) && Number.isFinite(maxEncoded) &&
        minEncoded >= 0 && maxEncoded < metric.nodata && minEncoded < maxEncoded
      ) {
        return { minEncoded, maxEncoded };
      }
    }
    return { minEncoded: metric.encoded_min, maxEncoded: metric.encoded_max };
  }

  setActiveDisplayRange(range: DisplayRange, persist: boolean) {
    this.activeDisplayRange = range;
    if (persist) this.writeStoredDisplayRange(this.activeMetric, range);
  }

  resetActiveDisplayRange() {
    this.activeDisplayRange = this.fullDisplayRange(this.activeMetric);
    try {
      window.localStorage.removeItem(this.displayRangeStorageKey(this.activeMetric));
    } catch {
      // Controls still work without persistence.
    }
  }

  displayRangeSampleValues(): number[] {
    const min = this.activeDisplayRange.minEncoded;
    const max = this.activeDisplayRange.maxEncoded;
    const steps = Math.min(5, Math.max(1, max - min + 1));
    const values: number[] = [];
    for (let index = 0; index < steps; index += 1) {
      values.push(Math.round(min + ((max - min) * index) / Math.max(1, steps - 1)));
    }
    return [...new Set(values)];
  }

  displayRangePercent(metric: MetricTransport, encoded: number): number {
    const full = this.fullDisplayRange(metric);
    const span = full.maxEncoded - full.minEncoded;
    if (span <= 0) return 50;
    return ((encoded - full.minEncoded) / span) * 100;
  }

  paletteBinForValue(value: number): number {
    const min = this.activeDisplayRange.minEncoded;
    const max = this.activeDisplayRange.maxEncoded;
    if (max === min) return Math.floor((PALETTE_BIN_COUNT - 1) / 2);
    const ratio = Math.max(0, Math.min(1, (value - min) / (max - min)));
    return Math.min(PALETTE_BIN_COUNT - 1, Math.floor(ratio * PALETTE_BIN_COUNT));
  }

  colorForPaletteBin(bin: number): string {
    return this.paletteColours(this.activeMetric)[Math.max(0, Math.min(PALETTE_BIN_COUNT - 1, bin))];
  }

  colorForValue(value: number): string {
    return this.colorForPaletteBin(this.paletteBinForValue(value));
  }

  private displayRangeStorageKey(metric: MetricTransport): string {
    return `${DISPLAY_RANGE_STORAGE_PREFIX}${metric.id}`;
  }

  private readStoredDisplayRange(metric: MetricTransport): DisplayRange {
    const full = this.fullDisplayRange(metric);
    try {
      const raw = window.localStorage.getItem(this.displayRangeStorageKey(metric));
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

  private writeStoredDisplayRange(metric: MetricTransport, range: DisplayRange) {
    try {
      window.localStorage.setItem(this.displayRangeStorageKey(metric), JSON.stringify(range));
    } catch {
      // Controls still work without persistence.
    }
  }

  private decodeMetric(metric: MetricTransport): MetricRuntime {
    const cached = this.runtimeCache.get(metric.id);
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
    const levels = metric.levels.map((level, index) => {
      if (level.level !== index) throw new Error(`Unexpected LOD index ${level.level} for ${metric.id}`);
      if (level.value_count !== level.width * level.height) {
        throw new Error(`LOD ${index} dimensions do not match value count for ${metric.id}`);
      }
      const start = level.value_offset;
      const stop = start + level.value_count;
      if (stop > values.length) throw new Error(`LOD ${index} exceeds decoded metric buffer for ${metric.id}`);
      if (index === 0) {
        if (
          level.width !== this.data.grid.width ||
          level.height !== this.data.grid.height ||
          level.cell_size_m !== this.data.grid.cell_size_m
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
    const runtime: MetricRuntime = { transport: metric, levels };
    this.runtimeCache.set(metric.id, runtime);
    metric.blob_base64 = "";
    const elapsed = performance.now() - started;
    console.info(`Goldilocks metric decode ${metric.id}: ${elapsed.toFixed(1)} ms`);
    return runtime;
  }

  private paletteColours(metric: MetricTransport): string[] {
    const cached = this.paletteCache.get(metric.id);
    if (cached) return cached;

    const stops = metric.palette?.map(parseHexColour) ?? [];
    let colours: string[];
    if (stops.length >= 2 && stops.every((stop): stop is [number, number, number] => stop !== null)) {
      colours = Array.from({ length: PALETTE_BIN_COUNT }, (_, bin) => {
        const ratio = PALETTE_BIN_COUNT <= 1 ? 0.5 : bin / (PALETTE_BIN_COUNT - 1);
        const position = ratio * (stops.length - 1);
        const lowerIndex = Math.floor(position);
        const upperIndex = Math.min(stops.length - 1, lowerIndex + 1);
        const mix = position - lowerIndex;
        const lower = stops[lowerIndex];
        const upper = stops[upperIndex];
        const channels = lower.map((channel, index) => Math.round(channel + (upper[index] - channel) * mix));
        return `rgb(${channels[0]} ${channels[1]} ${channels[2]})`;
      });
    } else {
      // Compatibility with pre-palette bundles.
      colours = Array.from({ length: PALETTE_BIN_COUNT }, (_, bin) => {
        const rawRatio = PALETTE_BIN_COUNT <= 1 ? 0.5 : bin / (PALETTE_BIN_COUNT - 1);
        const ratio = metric.palette_reverse ? 1 - rawRatio : rawRatio;
        const hue = 220 - ratio * 220;
        const lightness = 48 - 10 * Math.pow(1 - ratio, 2);
        return `hsl(${hue.toFixed(0)} 78% ${lightness.toFixed(1)}%)`;
      });
    }
    this.paletteCache.set(metric.id, colours);
    return colours;
  }
}
