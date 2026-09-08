import { gunzipSync } from "fflate";

import { readStoredBoolean, writeStoredBoolean } from "./storage";
import type { MilitaryAreaCategory, MilitaryAreaCategoryId, MilitaryAreaTransport } from "./types";

declare const L: any;

type MilitaryFeatureTuple = [
  categoryIndex: number,
  name: string,
  osmRef: string,
  geometry: { type: "Polygon" | "MultiPolygon"; coordinates: unknown },
];

type MilitaryPayload = { features: MilitaryFeatureTuple[] };

const STORAGE_PREFIX = "goldilocks.militaryAreaVisible.";

function decodeBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function osmUrl(reference: string): string | null {
  const match = /^(n|w|r)\/(\d+)$/.exec(reference);
  if (!match) return null;
  const type = match[1] === "n" ? "node" : match[1] === "w" ? "way" : "relation";
  return `https://www.openstreetmap.org/${type}/${match[2]}`;
}

function polygonStyle(categoryId: MilitaryAreaCategoryId) {
  if (categoryId === "dangerous") {
    return {
      pane: "goldilocks-military-dangerous",
      color: "#a52a2a",
      weight: 2,
      opacity: 0.9,
      fillColor: "#d55a3a",
      fillOpacity: 0.14,
      dashArray: "7 4",
      bubblingMouseEvents: false,
    };
  }
  return {
    pane: "goldilocks-military-unspecified",
    color: "#65546f",
    weight: 1.5,
    opacity: 0.8,
    fillColor: "#7b6a83",
    fillOpacity: 0.09,
    dashArray: "3 4",
    bubblingMouseEvents: false,
  };
}

function popupContent(name: string, category: MilitaryAreaCategory, osmRef: string): HTMLElement {
  const root = document.createElement("div");
  root.className = "military-area-popup";
  const heading = document.createElement("strong");
  heading.textContent = name || (category.id === "dangerous" ? "Dangerous military area" : "Military area");
  const type = document.createElement("div");
  type.textContent = category.id === "dangerous" ? "Dangerous military area" : "Military area — danger status unspecified";
  root.append(heading, type);
  const source = document.createElement("div");
  source.className = "military-area-popup-source";
  source.append("Source: ");
  const url = osmUrl(osmRef);
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
  root.append(source);
  return root;
}

export class MilitaryAreasController {
  readonly categories: MilitaryAreaCategory[];

  private payload: MilitaryPayload | null = null;
  private readonly enabled = new Map<MilitaryAreaCategoryId, boolean>();
  private readonly layers = new Map<MilitaryAreaCategoryId, any>();
  private readonly populated = new Set<MilitaryAreaCategoryId>();

  constructor(private readonly map: any, readonly transport: MilitaryAreaTransport) {
    if (transport.format_version !== 1 || transport.payload_encoding !== "gzip+json") {
      throw new Error("Unsupported Goldilocks military-area transport");
    }
    this.categories = [...transport.categories].sort((left, right) => left.order - right.order);
    const unspecifiedPane = map.getPane("goldilocks-military-unspecified") ?? map.createPane("goldilocks-military-unspecified");
    unspecifiedPane.style.zIndex = "410";
    const dangerousPane = map.getPane("goldilocks-military-dangerous") ?? map.createPane("goldilocks-military-dangerous");
    dangerousPane.style.zIndex = "420";
    for (const category of this.categories) {
      const layer = L.layerGroup();
      this.layers.set(category.id, layer);
      const isEnabled = readStoredBoolean(`${STORAGE_PREFIX}${category.id}`, false);
      this.enabled.set(category.id, isEnabled);
    }
    for (const category of this.categories) {
      if (this.isEnabled(category.id)) this.showCategory(category.id);
    }
  }

  isEnabled(categoryId: MilitaryAreaCategoryId): boolean {
    return this.enabled.get(categoryId) ?? false;
  }

  setEnabled(categoryId: MilitaryAreaCategoryId, enabled: boolean) {
    if (!this.enabled.has(categoryId) || this.enabled.get(categoryId) === enabled) return;
    this.enabled.set(categoryId, enabled);
    writeStoredBoolean(`${STORAGE_PREFIX}${categoryId}`, enabled);
    if (enabled) this.showCategory(categoryId);
    else {
      const layer = this.layers.get(categoryId);
      if (layer && this.map.hasLayer(layer)) layer.removeFrom(this.map);
    }
  }

  private ensurePayload(): MilitaryPayload {
    if (this.payload) return this.payload;
    const raw = gunzipSync(decodeBase64(this.transport.blob_base64));
    if (raw.byteLength !== this.transport.raw_bytes) {
      throw new Error(`Military-area payload decoded to ${raw.byteLength} bytes; expected ${this.transport.raw_bytes}`);
    }
    const payload = JSON.parse(new TextDecoder().decode(raw)) as MilitaryPayload;
    if (!Array.isArray(payload?.features)) throw new Error("Military-area payload is malformed");
    this.payload = payload;
    return payload;
  }

  private populateCategory(categoryId: MilitaryAreaCategoryId) {
    if (this.populated.has(categoryId)) return;
    const categoryIndex = this.categories.findIndex((category) => category.id === categoryId);
    const category = this.categories[categoryIndex];
    const layer = this.layers.get(categoryId);
    if (!category || !layer || categoryIndex < 0) return;

    const features = this.ensurePayload().features
      .filter((tuple) => tuple[0] === categoryIndex)
      .map((tuple) => ({
        type: "Feature",
        properties: { name: tuple[1], osmRef: tuple[2] },
        geometry: tuple[3],
      }));
    const polygons = L.geoJSON(
      { type: "FeatureCollection", features },
      {
        style: polygonStyle(categoryId),
        onEachFeature: (feature: any, featureLayer: any) => {
          featureLayer.bindPopup(popupContent(feature.properties.name, category, feature.properties.osmRef));
        },
      },
    );
    polygons.addTo(layer);
    this.populated.add(categoryId);
  }

  private showCategory(categoryId: MilitaryAreaCategoryId) {
    this.populateCategory(categoryId);
    const layer = this.layers.get(categoryId);
    if (layer && !this.map.hasLayer(layer)) layer.addTo(this.map);
  }
}
