import { gunzipSync } from "fflate";

import { readStoredBoolean, writeStoredBoolean } from "./storage";
import type { ServiceCategory, ServiceTransport } from "./types";

declare const L: any;

type ServiceTuple = [
  id: string,
  categoryIndex: number,
  latEncoded: number,
  lonEncoded: number,
  name: string,
  osmRef: string,
];

type ServicePayload = {
  buckets: Record<string, ServiceTuple[]>;
};

const SERVICE_STORAGE_PREFIX = "goldilocks.serviceVisible.";
const OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

function decodeBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function markerInner(categoryId: string): string {
  if (categoryId === "supermarket") {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h2l1.4 9h9.3l1.5-6.5H7.1M9 18a1.3 1.3 0 1 0 0 .01M16 18a1.3 1.3 0 1 0 0 .01"/></svg>';
  }
  if (categoryId === "post_office") {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14v10H5zM5.5 8l6.5 5 6.5-5"/></svg>';
  }
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
}

function markerHtml(categoryId: string, legend = false): string {
  const legendClass = legend ? " service-marker-legend" : "";
  return `<span class="service-marker service-marker-${categoryId}${legendClass}">${markerInner(categoryId)}</span>`;
}

function osmElementUrl(osmRef: string): string | null {
  const match = /^(n|w|r)\/(\d+)$/.exec(osmRef);
  if (!match) return null;
  const type = match[1] === "n" ? "node" : (match[1] === "w" ? "way" : "relation");
  return `https://www.openstreetmap.org/${type}/${match[2]}`;
}

function servicePopup(name: string, category: ServiceCategory, osmRef: string): HTMLElement {
  const root = document.createElement("div");
  root.className = "service-popup";
  const heading = document.createElement("strong");
  heading.textContent = name;
  const type = document.createElement("div");
  type.textContent = category.id === "supermarket" ? "Supermarket" : (category.id === "post_office" ? "Post office" : "Pharmacy");
  const source = document.createElement("div");
  source.className = "service-popup-source";
  source.append("Source: ");
  const url = osmElementUrl(osmRef);
  if (url) {
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "OpenStreetMap";
    source.append(link);
  } else {
    source.append("OpenStreetMap");
  }
  root.append(heading, type, source);
  return root;
}

export class ServicesController {
  readonly categories: ServiceCategory[];
  readonly minZoom: number;

  private payload: ServicePayload | null = null;
  private readonly enabled = new Map<string, boolean>();
  private readonly layerGroups = new Map<string, any>();
  private readonly visibleMarkers = new Map<string, Map<string, any>>();
  private readonly statusListeners = new Set<(text: string) => void>();
  private refreshScheduled = false;

  constructor(private readonly map: any, readonly transport: ServiceTransport) {
    if (transport.format_version !== 1 || transport.payload_encoding !== "gzip+json") {
      throw new Error("Unsupported Goldilocks service POI transport");
    }
    this.categories = [...transport.categories].sort((left, right) => left.order - right.order);
    this.minZoom = transport.min_zoom;
    for (const category of this.categories) {
      const layer = L.layerGroup();
      this.layerGroups.set(category.id, layer);
      this.visibleMarkers.set(category.id, new Map());
      const isEnabled = readStoredBoolean(`${SERVICE_STORAGE_PREFIX}${category.id}`, false);
      this.enabled.set(category.id, isEnabled);
      if (isEnabled) layer.addTo(map);
    }
    map.attributionControl?.addAttribution(OSM_ATTRIBUTION);
    map.on("moveend", () => this.scheduleRefresh());
    map.on("zoomend", () => this.scheduleRefresh());
    this.scheduleRefresh();
  }

  isEnabled(categoryId: string): boolean {
    return this.enabled.get(categoryId) ?? false;
  }

  setEnabled(categoryId: string, enabled: boolean) {
    if (!this.enabled.has(categoryId)) return;
    if (this.enabled.get(categoryId) === enabled) return;
    this.enabled.set(categoryId, enabled);
    writeStoredBoolean(`${SERVICE_STORAGE_PREFIX}${categoryId}`, enabled);
    const layer = this.layerGroups.get(categoryId);
    if (enabled) {
      if (layer && !this.map.hasLayer(layer)) layer.addTo(this.map);
    } else {
      if (layer && this.map.hasLayer(layer)) layer.removeFrom(this.map);
      this.clearCategory(categoryId);
    }
    this.scheduleRefresh();
  }

  legendIconHtml(categoryId: string): string {
    return markerHtml(categoryId, true);
  }

  onStatusChange(listener: (text: string) => void) {
    this.statusListeners.add(listener);
    listener(this.statusText());
  }

  sourceSummary(): string {
    const date = this.transport.source.extract_date;
    return date ? `OpenStreetMap · ${date}` : "OpenStreetMap";
  }

  private ensurePayload(): ServicePayload {
    if (this.payload) return this.payload;
    const compressed = decodeBase64(this.transport.blob_base64);
    const raw = gunzipSync(compressed);
    if (raw.byteLength !== this.transport.raw_bytes) {
      throw new Error(`Service POI payload decoded to ${raw.byteLength} bytes; expected ${this.transport.raw_bytes}`);
    }
    const parsed = JSON.parse(new TextDecoder().decode(raw)) as ServicePayload;
    if (!parsed || typeof parsed !== "object" || !parsed.buckets || typeof parsed.buckets !== "object") {
      throw new Error("Service POI payload is malformed");
    }
    this.payload = parsed;
    return parsed;
  }

  private scheduleRefresh() {
    if (this.refreshScheduled) return;
    this.refreshScheduled = true;
    window.requestAnimationFrame(() => {
      this.refreshScheduled = false;
      this.refreshVisibleMarkers();
    });
  }

  private refreshVisibleMarkers() {
    const enabledCategories = this.categories.filter((category) => this.isEnabled(category.id));
    if (!enabledCategories.length || this.map.getZoom() < this.minZoom) {
      for (const category of this.categories) this.clearCategory(category.id);
      this.emitStatus();
      return;
    }

    const payload = this.ensurePayload();
    const categoryByIndex = new Map(this.categories.map((category, index) => [index, category]));
    const enabledIds = new Set(enabledCategories.map((category) => category.id));
    const desired = new Map<string, Map<string, ServiceTuple>>();
    for (const category of enabledCategories) desired.set(category.id, new Map());

    const bounds = this.map.getBounds().pad(0.12);
    const bucketScale = this.transport.bucket_scale;
    const minLatBucket = Math.floor(bounds.getSouth() * bucketScale);
    const maxLatBucket = Math.floor(bounds.getNorth() * bucketScale);
    const minLonBucket = Math.floor(bounds.getWest() * bucketScale);
    const maxLonBucket = Math.floor(bounds.getEast() * bucketScale);
    for (let latBucket = minLatBucket; latBucket <= maxLatBucket; latBucket += 1) {
      for (let lonBucket = minLonBucket; lonBucket <= maxLonBucket; lonBucket += 1) {
        const tuples = payload.buckets[`${latBucket}:${lonBucket}`];
        if (!tuples) continue;
        for (const tuple of tuples) {
          const category = categoryByIndex.get(tuple[1]);
          if (!category || !enabledIds.has(category.id)) continue;
          const lat = tuple[2] / this.transport.coordinate_scale;
          const lon = tuple[3] / this.transport.coordinate_scale;
          if (!bounds.contains([lat, lon])) continue;
          desired.get(category.id)?.set(tuple[0], tuple);
        }
      }
    }

    for (const category of enabledCategories) {
      const markers = this.visibleMarkers.get(category.id)!;
      const wanted = desired.get(category.id)!;
      const layer = this.layerGroups.get(category.id);
      for (const [id, marker] of markers) {
        if (wanted.has(id)) continue;
        layer.removeLayer(marker);
        markers.delete(id);
      }
      for (const [id, tuple] of wanted) {
        if (markers.has(id)) continue;
        const lat = tuple[2] / this.transport.coordinate_scale;
        const lon = tuple[3] / this.transport.coordinate_scale;
        const marker = L.marker([lat, lon], {
          icon: L.divIcon({
            className: "goldilocks-service-marker-icon",
            html: markerHtml(category.id),
            iconSize: [26, 26],
            iconAnchor: [13, 13],
          }),
          bubblingMouseEvents: false,
          keyboard: true,
          riseOnHover: true,
          title: tuple[4],
        });
        marker.bindPopup(servicePopup(tuple[4], category, tuple[5]));
        marker.addTo(layer);
        markers.set(id, marker);
      }
    }
    this.emitStatus();
  }

  private clearCategory(categoryId: string) {
    const layer = this.layerGroups.get(categoryId);
    const markers = this.visibleMarkers.get(categoryId);
    if (!layer || !markers || !markers.size) return;
    layer.clearLayers();
    markers.clear();
  }

  private visibleMarkerCount(): number {
    let count = 0;
    for (const category of this.categories) {
      if (this.isEnabled(category.id)) count += this.visibleMarkers.get(category.id)?.size ?? 0;
    }
    return count;
  }

  private statusText(): string {
    const enabledCount = this.categories.filter((category) => this.isEnabled(category.id)).length;
    if (!enabledCount) return "";
    if (this.map.getZoom() < this.minZoom) return `Zoom in to level ${this.minZoom}+ to show services.`;
    const visible = this.visibleMarkerCount();
    return visible ? `Showing ${visible.toLocaleString()} service location${visible === 1 ? "" : "s"} in view.` : "No selected services in this view.";
  }

  private emitStatus() {
    const text = this.statusText();
    for (const listener of this.statusListeners) listener(text);
  }
}
