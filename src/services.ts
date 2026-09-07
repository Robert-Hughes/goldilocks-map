import { gunzipSync } from "fflate";

import { readStoredBoolean, writeStoredBoolean } from "./storage";
import type { ServiceCategory, ServiceCategoryId, ServiceSource, ServiceTransport } from "./types";

declare const L: any;

type ServiceTuple = [
  id: string,
  categoryIndex: number,
  latEncoded: number,
  lonEncoded: number,
  name: string,
  sourceIndex: number,
  sourceRef: string,
];

type ServicePayload = { buckets: Record<string, ServiceTuple[]> };

const SERVICE_STORAGE_PREFIX = "goldilocks.serviceVisible.";
const OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

function decodeBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function markerInner(categoryId: ServiceCategoryId): string {
  if (categoryId === "supermarket") return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h2l1.4 9h9.3l1.5-6.5H7.1M9 18a1.3 1.3 0 1 0 0 .01M16 18a1.3 1.3 0 1 0 0 .01"/></svg>';
  if (categoryId === "post_office") return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14v10H5zM5.5 8l6.5 5 6.5-5"/></svg>';
  if (categoryId === "pharmacy") return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
  if (categoryId === "gp") return '<span class="service-marker-letters">GP</span>';
  if (categoryId === "dentist") return '<span class="service-marker-letters">D</span>';
  if (categoryId === "hospital_community") return '<span class="service-marker-letters">C</span>';
  return '<span class="service-marker-letters">H</span>';
}

function markerHtml(categoryId: ServiceCategoryId, legend = false): string {
  const legendClass = legend ? " service-marker-legend" : "";
  return `<span class="service-marker service-marker-${categoryId}${legendClass}">${markerInner(categoryId)}</span>`;
}

function categoryLabel(category: ServiceCategory): string {
  if (category.id === "supermarket") return "Supermarket";
  if (category.id === "post_office") return "Post office";
  if (category.id === "pharmacy") return "Pharmacy";
  if (category.id === "gp") return "GP practice";
  if (category.id === "dentist") return "NHS dental practice";
  if (category.id === "hospital_general") return "General hospital";
  return "Community hospital";
}

function sourceLink(source: ServiceSource, sourceId: string, sourceRef: string): string | null {
  if (sourceId === "osm") {
    const match = /^(n|w|r)\/(\d+)$/.exec(sourceRef);
    if (match) {
      const type = match[1] === "n" ? "node" : match[1] === "w" ? "way" : "relation";
      return `https://www.openstreetmap.org/${type}/${match[2]}`;
    }
  }
  return source.homepage_url ?? null;
}

function servicePopup(
  name: string,
  category: ServiceCategory,
  source: ServiceSource,
  sourceId: string,
  sourceRef: string,
): HTMLElement {
  const root = document.createElement("div");
  root.className = "service-popup";
  const heading = document.createElement("strong");
  heading.textContent = name;
  const type = document.createElement("div");
  type.textContent = categoryLabel(category);
  root.append(heading, type);
  const sourceRow = document.createElement("div");
  sourceRow.className = "service-popup-source";
  sourceRow.append("Source: ");
  const url = sourceLink(source, sourceId, sourceRef);
  if (url) {
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = source.provider;
    sourceRow.append(link);
  } else {
    sourceRow.append(source.provider);
  }
  root.append(sourceRow);
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
    if (transport.format_version !== 2 || transport.payload_encoding !== "gzip+json") throw new Error("Unsupported Goldilocks service POI transport");
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

  isEnabled(categoryId: ServiceCategoryId): boolean {
    return this.enabled.get(categoryId) ?? false;
  }

  setEnabled(categoryId: ServiceCategoryId, enabled: boolean) {
    if (!this.enabled.has(categoryId) || this.enabled.get(categoryId) === enabled) return;
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

  legendIconHtml(categoryId: ServiceCategoryId): string {
    return markerHtml(categoryId, true);
  }

  onStatusChange(listener: (text: string) => void) {
    this.statusListeners.add(listener);
    listener(this.statusText());
  }

  private ensurePayload(): ServicePayload {
    if (this.payload) return this.payload;
    const raw = gunzipSync(decodeBase64(this.transport.blob_base64));
    if (raw.byteLength !== this.transport.raw_bytes) throw new Error(`Service POI payload decoded to ${raw.byteLength} bytes; expected ${this.transport.raw_bytes}`);
    const parsed = JSON.parse(new TextDecoder().decode(raw)) as ServicePayload;
    if (!parsed?.buckets || typeof parsed.buckets !== "object") throw new Error("Service POI payload is malformed");
    this.payload = parsed;
    return parsed;
  }

  private scheduleRefresh() {
    if (this.refreshScheduled) return;
    this.refreshScheduled = true;
    window.requestAnimationFrame(() => { this.refreshScheduled = false; this.refreshVisibleMarkers(); });
  }

  private refreshVisibleMarkers() {
    const activeCategories = this.categories.filter((category) => this.isEnabled(category.id));
    if (!activeCategories.length || this.map.getZoom() < this.minZoom) {
      for (const category of this.categories) this.clearCategory(category.id);
      this.emitStatus();
      return;
    }
    const payload = this.ensurePayload();
    const categoryByIndex = new Map(this.categories.map((category, index) => [index, category]));
    const activeIds = new Set(activeCategories.map((category) => category.id));
    const desired = new Map<string, Map<string, ServiceTuple>>();
    for (const category of activeCategories) desired.set(category.id, new Map());
    const bounds = this.map.getBounds().pad(0.12);
    const scale = this.transport.bucket_scale;
    for (let y = Math.floor(bounds.getSouth() * scale); y <= Math.floor(bounds.getNorth() * scale); y += 1) {
      for (let x = Math.floor(bounds.getWest() * scale); x <= Math.floor(bounds.getEast() * scale); x += 1) {
        for (const tuple of payload.buckets[`${y}:${x}`] ?? []) {
          const category = categoryByIndex.get(tuple[1]);
          if (!category || !activeIds.has(category.id)) continue;
          const lat = tuple[2] / this.transport.coordinate_scale;
          const lon = tuple[3] / this.transport.coordinate_scale;
          if (bounds.contains([lat, lon])) desired.get(category.id)?.set(tuple[0], tuple);
        }
      }
    }
    for (const category of activeCategories) {
      const markers = this.visibleMarkers.get(category.id)!;
      const wanted = desired.get(category.id)!;
      const layer = this.layerGroups.get(category.id);
      for (const [id, marker] of markers) if (!wanted.has(id)) { layer.removeLayer(marker); markers.delete(id); }
      for (const [id, tuple] of wanted) {
        if (markers.has(id)) continue;
        const sourceId = this.transport.source_order[tuple[5]];
        const source = this.transport.sources[sourceId];
        if (!source) continue;
        const marker = L.marker([tuple[2] / this.transport.coordinate_scale, tuple[3] / this.transport.coordinate_scale], {
          icon: L.divIcon({ className: "goldilocks-service-marker-icon", html: markerHtml(category.id), iconSize: [26, 26], iconAnchor: [13, 13] }),
          bubblingMouseEvents: false, keyboard: true, riseOnHover: true, title: tuple[4],
        });
        marker.bindPopup(servicePopup(tuple[4], category, source, sourceId, tuple[6]));
        marker.addTo(layer);
        markers.set(id, marker);
      }
    }
    this.emitStatus();
  }

  private clearCategory(categoryId: string) {
    const layer = this.layerGroups.get(categoryId); const markers = this.visibleMarkers.get(categoryId);
    if (!layer || !markers?.size) return;
    layer.clearLayers(); markers.clear();
  }

  private visibleMarkerCount(): number {
    return this.categories.reduce((count, category) => count + (this.isEnabled(category.id) ? this.visibleMarkers.get(category.id)?.size ?? 0 : 0), 0);
  }

  private statusText(): string {
    if (!this.categories.some((category) => this.isEnabled(category.id))) return "";
    if (this.map.getZoom() < this.minZoom) return `Zoom in to level ${this.minZoom}+ to show services.`;
    const visible = this.visibleMarkerCount();
    return visible ? `Showing ${visible.toLocaleString()} service location${visible === 1 ? "" : "s"} in view.` : "No selected services in this view.";
  }

  private emitStatus() {
    const text = this.statusText();
    for (const listener of this.statusListeners) listener(text);
  }
}
