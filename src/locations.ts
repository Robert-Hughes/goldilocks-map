import { NARROW_PANEL_MEDIA_QUERY, syncNarrowPanelCorner } from "./panel-layout";
import { readStoredBoolean, writeStoredBoolean } from "./storage";

declare const L: any;

export type SavedLocation = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  notes: string;
};

type LocationsExport = {
  format: "goldilocks-locations";
  version: 1;
  locations: SavedLocation[];
};

type LocationDraft = {
  id: string | null;
  name: string;
  lat: string;
  lon: string;
  notes: string;
};

type ParsedLocations = {
  locations: SavedLocation[];
  generatedIds: boolean;
  normalizedWhitespaceCount?: number;
};

const LOCATIONS_STORAGE_KEY = "goldilocks.locations.v1";
const LOCATIONS_PANEL_COLLAPSED_STORAGE_KEY = "goldilocks.locationsPanelCollapsed";
const EXPORT_FORMAT = "goldilocks-locations";
const EXPORT_VERSION = 1;
const MAX_LOCATIONS = 1000;
const MAX_NAME_LENGTH = 200;
const MAX_NOTES_LENGTH = 20000;

function makeLocationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `loc-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function locationExport(locations: SavedLocation[]): LocationsExport {
  return {
    format: EXPORT_FORMAT,
    version: EXPORT_VERSION,
    locations: locations.map((location) => ({ ...location })),
  };
}

function parseLocationsPayload(value: unknown): ParsedLocations {
  let rawLocations: unknown;
  if (Array.isArray(value)) {
    // Accept a bare array as a convenience for hand-authored/shared data.
    rawLocations = value;
  } else if (value && typeof value === "object") {
    const payload = value as Record<string, unknown>;
    if (payload.format !== EXPORT_FORMAT || payload.version !== EXPORT_VERSION || !Array.isArray(payload.locations)) {
      throw new Error(`Expected ${EXPORT_FORMAT} JSON version ${EXPORT_VERSION}.`);
    }
    rawLocations = payload.locations;
  } else {
    throw new Error("Expected a locations JSON object or array.");
  }

  const items = rawLocations as unknown[];
  if (items.length > MAX_LOCATIONS) throw new Error(`At most ${MAX_LOCATIONS} locations can be imported.`);

  const usedIds = new Set<string>();
  let generatedIds = false;
  const locations = items.map((item, index): SavedLocation => {
    if (!item || typeof item !== "object") throw new Error(`Location ${index + 1} is not an object.`);
    const raw = item as Record<string, unknown>;
    if (typeof raw.name !== "string" || !raw.name.trim()) throw new Error(`Location ${index + 1} needs a name.`);
    const name = raw.name.trim();
    if (name.length > MAX_NAME_LENGTH) throw new Error(`Location ${index + 1} name is too long.`);
    if (typeof raw.notes !== "string" && raw.notes !== undefined) throw new Error(`Location ${index + 1} notes must be text.`);
    const notes = typeof raw.notes === "string" ? raw.notes : "";
    if (notes.length > MAX_NOTES_LENGTH) throw new Error(`Location ${index + 1} notes are too long.`);

    const lat = Number(raw.lat);
    const lon = Number(raw.lon);
    if (!Number.isFinite(lat) || lat < -90 || lat > 90) throw new Error(`Location ${index + 1} has an invalid latitude.`);
    if (!Number.isFinite(lon) || lon < -180 || lon > 180) throw new Error(`Location ${index + 1} has an invalid longitude.`);

    let id = typeof raw.id === "string" && raw.id.trim() ? raw.id.trim() : "";
    if (!id || usedIds.has(id) || id.length > 200) {
      do id = makeLocationId(); while (usedIds.has(id));
      generatedIds = true;
    }
    usedIds.add(id);
    return { id, name, lat, lon, notes };
  });
  return { locations, generatedIds };
}

type JsonWhitespaceNormalization = {
  text: string;
  count: number;
  firstDescription: string;
  firstLine: number;
  firstColumn: number;
};

function jsonParseMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "Unknown JSON parser error.";
}

function describeNonStandardWhitespace(character: string): string {
  const codePoint = character.codePointAt(0) ?? 0;
  if (codePoint === 0x00a0) return "a non-breaking space (U+00A0)";
  if (codePoint === 0xfeff) return "a byte-order mark / zero-width no-break space (U+FEFF)";
  return `non-standard whitespace (U+${codePoint.toString(16).toUpperCase().padStart(4, "0")})`;
}

function normalizeNonStandardJsonWhitespace(text: string): JsonWhitespaceNormalization {
  let normalized = "";
  let count = 0;
  let inString = false;
  let escaped = false;
  let line = 1;
  let column = 1;
  let firstDescription = "";
  let firstLine = 0;
  let firstColumn = 0;

  for (const character of text) {
    let output = character;
    if (inString) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') inString = false;
    } else if (character === '"') {
      inString = true;
    } else if (
      /\s/u.test(character)
      && character !== " "
      && character !== "\t"
      && character !== "\r"
      && character !== "\n"
    ) {
      output = " ";
      count += 1;
      if (!firstDescription) {
        firstDescription = describeNonStandardWhitespace(character);
        firstLine = line;
        firstColumn = column;
      }
    }

    normalized += output;
    if (character === "\n") {
      line += 1;
      column = 1;
    } else {
      column += 1;
    }
  }

  return { text: normalized, count, firstDescription, firstLine, firstColumn };
}

function parseLocationsJson(text: string): ParsedLocations {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    const normalization = normalizeNonStandardJsonWhitespace(text);
    if (normalization.count > 0) {
      try {
        parsed = JSON.parse(normalization.text);
      } catch (normalizedError) {
        const replacements = `${normalization.count} non-standard whitespace character${normalization.count === 1 ? "" : "s"}`;
        throw new Error(
          `The locations text is not valid JSON. Found ${replacements}; the first is ${normalization.firstDescription} at line ${normalization.firstLine}, column ${normalization.firstColumn}. After normalizing that copied formatting, the JSON parser still reports: ${jsonParseMessage(normalizedError)}`,
        );
      }
      const result = parseLocationsPayload(parsed);
      return { ...result, normalizedWhitespaceCount: normalization.count };
    }
    throw new Error(`The locations text is not valid JSON. JSON parser: ${jsonParseMessage(error)}`);
  }
  return parseLocationsPayload(parsed);
}

function locationLabelIcon(location: SavedLocation): any {
  const label = document.createElement("span");
  label.className = "location-marker-label";
  label.dataset.locationId = location.id;
  label.textContent = location.name;
  return L.divIcon({
    className: "goldilocks-location-marker-icon",
    html: label,
    iconSize: [0, 0],
    iconAnchor: [0, 0],
  });
}

function button(text: string, className = "location-button"): HTMLButtonElement {
  const element = document.createElement("button");
  element.type = "button";
  element.className = className;
  element.textContent = text;
  return element;
}

export class LocationsController {
  private locations: SavedLocation[] = [];
  private readonly markerLayer: any;
  private readonly markers = new Map<string, any>();
  private readonly narrowPanelMedia = window.matchMedia(NARROW_PANEL_MEDIA_QUERY);
  private panelRoot: HTMLElement | null = null;
  private panelBody: HTMLElement | null = null;
  private panelCount: HTMLElement | null = null;
  private panelToggle: HTMLButtonElement | null = null;
  private listElement: HTMLElement | null = null;
  private emptyElement: HTMLElement | null = null;
  private statusElement: HTMLElement | null = null;
  private importTextarea: HTMLTextAreaElement | null = null;
  private draft: LocationDraft | null = null;
  private storageMessage = "";
  private actionMessage = "";
  private narrowPeerCollapse: (() => void) | null = null;

  constructor(private readonly map: any, initiallyVisible = true) {
    const loaded = this.loadLocations();
    this.locations = loaded.locations;
    if (loaded.message) this.storageMessage = loaded.message;
    this.markerLayer = L.layerGroup();
    if (initiallyVisible) this.markerLayer.addTo(map);
    this.addPanel();
    this.narrowPanelMedia.addEventListener("change", () => this.syncPanelCorner());
    this.renderMarkers();
    this.installContextMenus();
    if (loaded.generatedIds && this.locations.length) this.persistLocations();
  }

  setVisible(visible: boolean) {
    const isVisible = this.map.hasLayer(this.markerLayer);
    if (visible === isVisible) return;
    if (visible) this.markerLayer.addTo(this.map);
    else this.markerLayer.removeFrom(this.map);
  }

  setNarrowPanelPeerCollapse(callback: () => void) {
    this.narrowPeerCollapse = callback;
  }

  private loadLocations(): { locations: SavedLocation[]; generatedIds: boolean; message: string } {
    let raw: string | null;
    try {
      raw = window.localStorage.getItem(LOCATIONS_STORAGE_KEY);
    } catch {
      return {
        locations: [],
        generatedIds: false,
        message: "Saved locations cannot be read because browser storage is unavailable.",
      };
    }
    if (!raw) return { locations: [], generatedIds: false, message: "" };
    try {
      const parsed = parseLocationsJson(raw);
      return { ...parsed, message: "" };
    } catch (error) {
      const detail = error instanceof Error ? error.message : "Unknown storage error.";
      return {
        locations: [],
        generatedIds: false,
        message: `Saved locations could not be loaded: ${detail}`,
      };
    }
  }

  private persistLocations(): boolean {
    try {
      window.localStorage.setItem(LOCATIONS_STORAGE_KEY, JSON.stringify(locationExport(this.locations)));
      this.storageMessage = "";
      this.refreshStatus();
      return true;
    } catch {
      this.storageMessage = "Locations changed in this tab, but browser storage could not save them.";
      this.refreshStatus();
      return false;
    }
  }

  private addPanel() {
    const control = L.control({ position: "topright" });
    control.onAdd = () => {
      const root = L.DomUtil.create("section", "location-panel") as HTMLElement;
      this.panelRoot = root;
      const initiallyCollapsed = readStoredBoolean(
        LOCATIONS_PANEL_COLLAPSED_STORAGE_KEY,
        this.narrowPanelMedia.matches,
      );
      if (initiallyCollapsed) root.classList.add("collapsed");

      const header = document.createElement("div");
      header.className = "location-panel-header";
      const heading = document.createElement("div");
      heading.className = "location-panel-heading";
      const title = document.createElement("strong");
      title.className = "location-panel-title";
      title.textContent = "Locations";
      const count = document.createElement("span");
      count.className = "location-panel-count";
      this.panelCount = count;
      heading.append(title, count);

      const toggle = button(initiallyCollapsed ? "+" : "−", "location-panel-toggle");
      toggle.setAttribute("aria-label", `${initiallyCollapsed ? "Expand" : "Collapse"} locations panel`);
      toggle.setAttribute("aria-expanded", String(!initiallyCollapsed));
      toggle.addEventListener("click", () => this.setPanelCollapsed(!root.classList.contains("collapsed"), true));
      this.panelToggle = toggle;
      header.append(heading, toggle);

      const body = document.createElement("div");
      body.className = "location-panel-body";
      this.panelBody = body;

      const actions = document.createElement("div");
      actions.className = "location-panel-actions";
      const addButton = button("Add location", "location-button location-button-primary");
      addButton.addEventListener("click", () => this.beginNewLocation());
      actions.append(addButton);

      const status = document.createElement("div");
      status.className = "location-status";
      status.hidden = true;
      this.statusElement = status;

      const empty = document.createElement("div");
      empty.className = "location-empty";
      empty.textContent = "No saved locations yet. Right-click (or long-press) the map to add one at a point.";
      this.emptyElement = empty;

      const list = document.createElement("div");
      list.className = "location-list";
      this.listElement = list;

      const exportSection = document.createElement("details");
      exportSection.className = "location-transfer";
      const exportSummary = document.createElement("summary");
      exportSummary.textContent = "Export";
      const exportHelp = document.createElement("div");
      exportHelp.className = "location-transfer-help";
      exportHelp.textContent = "Copy the complete location list as shareable JSON.";
      const exportActions = document.createElement("div");
      exportActions.className = "location-transfer-actions";
      const copyButton = button("Copy to clipboard");
      copyButton.addEventListener("click", () => void this.copyExportJson());
      exportActions.append(copyButton);
      exportSection.append(exportSummary, exportHelp, exportActions);

      const importSection = document.createElement("details");
      importSection.className = "location-transfer";
      const importSummary = document.createElement("summary");
      importSummary.textContent = "Import";
      const importHelp = document.createElement("div");
      importHelp.className = "location-transfer-help";
      importHelp.textContent = "Paste locations JSON below. Import replaces the complete current list after confirmation.";
      const textarea = document.createElement("textarea");
      textarea.className = "location-json";
      textarea.rows = 7;
      textarea.spellcheck = false;
      textarea.setAttribute("aria-label", "Locations JSON to import");
      this.importTextarea = textarea;
      const importActions = document.createElement("div");
      importActions.className = "location-transfer-actions";
      const importButton = button("Import (replace)", "location-button location-button-danger");
      importButton.addEventListener("click", () => this.importJson());
      importActions.append(importButton);
      importSection.append(importSummary, importHelp, textarea, importActions);

      body.append(actions, status, empty, list, exportSection, importSection);
      root.append(header, body);

      L.DomEvent.disableClickPropagation(root);
      L.DomEvent.disableScrollPropagation(root);
      this.renderList();
      return root;
    };
    control.addTo(this.map);
    this.syncPanelCorner();
  }

  setPanelCollapsed(collapsed: boolean, persist = true) {
    if (!this.panelRoot || !this.panelToggle) return;
    if (!collapsed && this.narrowPanelMedia.matches) this.narrowPeerCollapse?.();
    this.panelRoot.classList.toggle("collapsed", collapsed);
    this.panelToggle.textContent = collapsed ? "+" : "−";
    this.panelToggle.setAttribute("aria-expanded", String(!collapsed));
    this.panelToggle.setAttribute("aria-label", `${collapsed ? "Expand" : "Collapse"} locations panel`);
    this.syncPanelCorner();
    if (persist) writeStoredBoolean(LOCATIONS_PANEL_COLLAPSED_STORAGE_KEY, collapsed);
  }

  private syncPanelCorner() {
    syncNarrowPanelCorner(this.panelRoot, !this.panelRoot?.classList.contains("collapsed"), this.narrowPanelMedia);
  }

  private installContextMenus() {
    this.map.on("contextmenu", (event: any) => {
      this.openContextMenu(event.latlng, "Add location here", () => {
        this.beginNewLocation(event.latlng);
      });
    });
  }

  private openContextMenu(latlng: any, label: string, action: () => void) {
    const container = document.createElement("div");
    container.className = "location-context-menu";
    const actionButton = button(label, "location-context-action");
    actionButton.addEventListener("click", () => {
      this.map.closePopup();
      action();
    });
    container.append(actionButton);
    L.popup({ closeButton: false, className: "location-context-popup", offset: [0, -2] })
      .setLatLng(latlng)
      .setContent(container)
      .openOn(this.map);
  }

  private renderMarkers() {
    this.markerLayer.clearLayers();
    this.markers.clear();
    for (const location of this.locations) {
      const marker = L.marker([location.lat, location.lon], {
        icon: locationLabelIcon(location),
        bubblingMouseEvents: false,
        keyboard: true,
        riseOnHover: true,
        title: location.name,
      });
      marker.on("contextmenu", (event: any) => {
        if (event.originalEvent) L.DomEvent.preventDefault(event.originalEvent);
        this.openContextMenu(event.latlng, "View / edit location", () => this.beginEditLocation(location.id));
      });
      marker.addTo(this.markerLayer);
      this.markers.set(location.id, marker);
    }
  }

  private beginNewLocation(latlng?: any) {
    this.draft = {
      id: null,
      name: "",
      lat: latlng ? Number(latlng.lat).toFixed(6) : "",
      lon: latlng ? Number(latlng.lng).toFixed(6) : "",
      notes: "",
    };
    this.actionMessage = "";
    this.setPanelCollapsed(false, true);
    this.renderList();
    this.focusDraft();
  }

  private beginEditLocation(id: string) {
    const location = this.locations.find((item) => item.id === id);
    if (!location) return;
    this.draft = {
      id: location.id,
      name: location.name,
      lat: String(location.lat),
      lon: String(location.lon),
      notes: location.notes,
    };
    this.actionMessage = "";
    this.setPanelCollapsed(false, true);
    this.renderList();
    this.focusDraft(id);
  }

  private focusDraft(id?: string) {
    window.requestAnimationFrame(() => {
      if (!this.panelRoot) return;
      const selector = id
        ? `[data-location-id="${CSS.escape(id)}"]`
        : "[data-location-draft-new]";
      const row = this.panelRoot.querySelector(selector) as HTMLElement | null;
      row?.scrollIntoView({ block: "nearest" });
      const name = row?.querySelector(".location-name-input") as HTMLInputElement | null;
      name?.focus();
    });
  }

  private renderList() {
    if (!this.listElement || !this.emptyElement || !this.panelCount) return;
    this.panelCount.textContent = `(${this.locations.length})`;
    this.listElement.replaceChildren();
    this.emptyElement.hidden = this.locations.length > 0 || this.draft?.id === null;

    if (this.draft?.id === null) {
      this.listElement.append(this.buildDraftCard(null));
    }

    this.locations.forEach((location, index) => {
      const card = document.createElement("article");
      card.className = "location-item";
      card.dataset.locationId = location.id;

      const summary = document.createElement("div");
      summary.className = "location-item-summary";
      const name = document.createElement("strong");
      name.className = "location-item-name";
      name.textContent = location.name;
      const controls = document.createElement("div");
      controls.className = "location-item-controls";

      const go = button("Go", "location-button location-button-compact");
      go.setAttribute("aria-label", `Pan map to ${location.name}`);
      go.addEventListener("click", () => this.map.panTo([location.lat, location.lon]));
      const edit = button("Edit", "location-button location-button-compact");
      edit.setAttribute("aria-label", `Edit ${location.name}`);
      edit.addEventListener("click", () => this.beginEditLocation(location.id));
      const up = button("↑", "location-button location-button-compact location-order-button");
      up.disabled = index === 0;
      up.setAttribute("aria-label", `Move ${location.name} up`);
      up.addEventListener("click", () => this.moveLocation(index, -1));
      const down = button("↓", "location-button location-button-compact location-order-button");
      down.disabled = index === this.locations.length - 1;
      down.setAttribute("aria-label", `Move ${location.name} down`);
      down.addEventListener("click", () => this.moveLocation(index, 1));
      controls.append(go, edit, up, down);
      summary.append(name, controls);
      card.append(summary);

      if (this.draft?.id === location.id) card.append(this.buildDraftForm(location));
      this.listElement?.append(card);
    });
    this.refreshStatus();
  }

  private buildDraftCard(location: SavedLocation | null): HTMLElement {
    const card = document.createElement("article");
    card.className = "location-item location-item-new";
    card.dataset.locationDraftNew = "true";
    const heading = document.createElement("strong");
    heading.className = "location-item-name";
    heading.textContent = "New location";
    card.append(heading, this.buildDraftForm(location));
    return card;
  }

  private buildDraftForm(existing: SavedLocation | null): HTMLElement {
    const draft = this.draft;
    if (!draft) return document.createElement("div");

    const form = document.createElement("form");
    form.className = "location-edit-form";
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.saveDraft(form, existing);
    });

    const nameLabel = document.createElement("label");
    nameLabel.textContent = "Name";
    const name = document.createElement("input");
    name.className = "location-name-input";
    name.type = "text";
    name.maxLength = MAX_NAME_LENGTH;
    name.required = true;
    name.value = draft.name;
    nameLabel.append(name);

    const coords = document.createElement("div");
    coords.className = "location-coordinate-row";
    const latLabel = document.createElement("label");
    latLabel.textContent = "Latitude";
    const lat = document.createElement("input");
    lat.className = "location-lat-input";
    lat.type = "number";
    lat.step = "any";
    lat.min = "-90";
    lat.max = "90";
    lat.required = true;
    lat.value = draft.lat;
    latLabel.append(lat);
    const lonLabel = document.createElement("label");
    lonLabel.textContent = "Longitude";
    const lon = document.createElement("input");
    lon.className = "location-lon-input";
    lon.type = "number";
    lon.step = "any";
    lon.min = "-180";
    lon.max = "180";
    lon.required = true;
    lon.value = draft.lon;
    lonLabel.append(lon);
    coords.append(latLabel, lonLabel);

    const notesLabel = document.createElement("label");
    notesLabel.textContent = "Notes";
    const notes = document.createElement("textarea");
    notes.className = "location-notes-input";
    notes.rows = 4;
    notes.maxLength = MAX_NOTES_LENGTH;
    notes.value = draft.notes;
    notesLabel.append(notes);

    const syncDraft = () => {
      if (!this.draft) return;
      this.draft = {
        ...this.draft,
        name: name.value,
        lat: lat.value,
        lon: lon.value,
        notes: notes.value,
      };
    };
    name.addEventListener("input", syncDraft);
    lat.addEventListener("input", syncDraft);
    lon.addEventListener("input", syncDraft);
    notes.addEventListener("input", syncDraft);

    const error = document.createElement("div");
    error.className = "location-form-error";
    error.hidden = true;

    const actions = document.createElement("div");
    actions.className = "location-edit-actions";
    const save = button("Save", "location-button location-button-primary");
    save.type = "submit";
    const cancel = button("Cancel");
    cancel.addEventListener("click", () => {
      this.draft = null;
      this.actionMessage = "";
      this.renderList();
    });
    actions.append(save, cancel);
    if (existing) {
      const remove = button("Delete", "location-button location-button-danger");
      remove.addEventListener("click", () => this.deleteLocation(existing));
      actions.append(remove);
    }

    form.append(nameLabel, coords, notesLabel, error, actions);
    return form;
  }

  private saveDraft(form: HTMLElement, existing: SavedLocation | null) {
    const nameInput = form.querySelector(".location-name-input") as HTMLInputElement;
    const latInput = form.querySelector(".location-lat-input") as HTMLInputElement;
    const lonInput = form.querySelector(".location-lon-input") as HTMLInputElement;
    const notesInput = form.querySelector(".location-notes-input") as HTMLTextAreaElement;
    const error = form.querySelector(".location-form-error") as HTMLElement;
    const name = nameInput.value.trim();
    const lat = Number(latInput.value);
    const lon = Number(lonInput.value);
    const notes = notesInput.value;

    let message = "";
    if (!name) message = "Name is required.";
    else if (name.length > MAX_NAME_LENGTH) message = `Name must be at most ${MAX_NAME_LENGTH} characters.`;
    else if (!latInput.value || !Number.isFinite(lat) || lat < -90 || lat > 90) message = "Latitude must be between -90 and 90.";
    else if (!lonInput.value || !Number.isFinite(lon) || lon < -180 || lon > 180) message = "Longitude must be between -180 and 180.";
    else if (notes.length > MAX_NOTES_LENGTH) message = `Notes must be at most ${MAX_NOTES_LENGTH} characters.`;
    if (message) {
      error.textContent = message;
      error.hidden = false;
      return;
    }

    if (existing) {
      const index = this.locations.findIndex((item) => item.id === existing.id);
      if (index < 0) return;
      this.locations[index] = { id: existing.id, name, lat, lon, notes };
      this.actionMessage = `Saved ${name}.`;
    } else {
      const location = { id: makeLocationId(), name, lat, lon, notes };
      this.locations.push(location);
      this.actionMessage = `Added ${name}.`;
    }
    this.draft = null;
    this.persistLocations();
    this.renderMarkers();
    this.renderList();
  }

  private deleteLocation(location: SavedLocation) {
    if (!window.confirm(`Delete “${location.name}”?`)) return;
    this.locations = this.locations.filter((item) => item.id !== location.id);
    this.draft = null;
    this.actionMessage = `Deleted ${location.name}.`;
    this.persistLocations();
    this.renderMarkers();
    this.renderList();
  }

  private moveLocation(index: number, delta: -1 | 1) {
    const nextIndex = index + delta;
    if (nextIndex < 0 || nextIndex >= this.locations.length) return;
    const [location] = this.locations.splice(index, 1);
    this.locations.splice(nextIndex, 0, location);
    this.actionMessage = "Location order saved.";
    this.persistLocations();
    this.renderList();
  }

  private async copyExportJson() {
    const text = JSON.stringify(locationExport(this.locations), null, 2);
    let copied = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        copied = true;
      }
    } catch {
      // Fall through to a temporary selection-based copy attempt below.
    }
    if (!copied) {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.append(textarea);
      textarea.select();
      try {
        copied = document.execCommand("copy");
      } catch {
        copied = false;
      }
      textarea.remove();
    }
    this.actionMessage = copied
      ? "Locations JSON copied to the clipboard."
      : "The browser could not copy the locations JSON to the clipboard.";
    this.refreshStatus(!copied);
  }

  private importJson() {
    if (!this.importTextarea) return;
    let parsed: ParsedLocations;
    try {
      parsed = parseLocationsJson(this.importTextarea.value);
    } catch (error) {
      this.actionMessage = error instanceof Error ? `Import failed: ${error.message}` : "Import failed.";
      this.refreshStatus(true);
      return;
    }

    const oldCount = this.locations.length;
    const newCount = parsed.locations.length;
    const warning = `Import ${newCount} location${newCount === 1 ? "" : "s"}?\n\nThis will replace all ${oldCount} existing location${oldCount === 1 ? "" : "s"}. Export the current list first if you may need it later.`;
    if (!window.confirm(warning)) return;

    this.locations = parsed.locations;
    this.draft = null;
    const whitespaceNote = parsed.normalizedWhitespaceCount
      ? ` Normalized ${parsed.normalizedWhitespaceCount} copied non-standard whitespace character${parsed.normalizedWhitespaceCount === 1 ? "" : "s"}.`
      : "";
    this.actionMessage = `Imported ${newCount} location${newCount === 1 ? "" : "s"}, replacing the previous list.${whitespaceNote}`;
    this.persistLocations();
    this.renderMarkers();
    this.renderList();
  }

  private refreshStatus(forceError = false) {
    if (!this.statusElement) return;
    const message = this.storageMessage || this.actionMessage;
    this.statusElement.hidden = !message;
    this.statusElement.textContent = message;
    this.statusElement.classList.toggle("location-status-error", Boolean(this.storageMessage) || forceError || this.actionMessage.startsWith("Import failed:"));
  }
}
