import type { LocationsController } from "./locations";
import { MetricState } from "./metrics";
import { NARROW_PANEL_MEDIA_QUERY, syncNarrowPanelCorner } from "./panel-layout";
import { RasterView } from "./raster";
import type { ServicesController } from "./services";
import { readStoredBoolean, readStoredString, writeStoredBoolean, writeStoredString } from "./storage";
import type { DataSource, GoldilocksData } from "./types";

declare const L: any;

const PANEL_COLLAPSED_STORAGE_KEY = "goldilocks.infoPanelCollapsed";
const GRIDLINES_STORAGE_KEY = "goldilocks.showGridLines";
const LOCATIONS_VISIBLE_STORAGE_KEY = "goldilocks.showLocationPins";
const LAYER_OPACITY_STORAGE_KEY = "goldilocks.layerOpacity";
const DEFAULT_LAYER_OPACITY = 0.62;

export type MapPanelPreferences = {
  gridLinesVisible: boolean;
  locationsVisible: boolean;
  layerOpacity: number;
};

export function readMapPanelPreferences(): MapPanelPreferences {
  const storedOpacity = readStoredString(LAYER_OPACITY_STORAGE_KEY);
  const value = storedOpacity === null ? DEFAULT_LAYER_OPACITY : Number(storedOpacity);
  return {
    gridLinesVisible: readStoredBoolean(GRIDLINES_STORAGE_KEY, false),
    locationsVisible: readStoredBoolean(LOCATIONS_VISIBLE_STORAGE_KEY, true),
    layerOpacity: Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : DEFAULT_LAYER_OPACITY,
  };
}

function writeStoredLayerOpacity(value: number) {
  writeStoredString(LAYER_OPACITY_STORAGE_KEY, Math.max(0, Math.min(1, value)).toFixed(2));
}

function sourceDescription(source: DataSource): string {
  return [source.dataset, source.resolution].filter(Boolean).join("; ");
}

export function addInfoPanel(
  map: any,
  data: GoldilocksData,
  metrics: MetricState,
  raster: RasterView,
  services: ServicesController,
  locations: LocationsController,
  thirdPartyNotices: string,
  preferences: MapPanelPreferences,
) {
  function pruningDescription(): string {
    return data.data_pruning
      .filter((item) => !item.source_id || item.source_id === metrics.activeMetric.source_id)
      .map((item) => `QC pruning: ${item.action}`)
      .join("<br>");
  }

  const narrowPanelMedia = window.matchMedia(NARROW_PANEL_MEDIA_QUERY);
  const mapPanel = L.control({ position: "topleft" });
  mapPanel.onAdd = () => {
    const div = L.DomUtil.create("section", "map-panel") as HTMLElement;
    const initiallyCollapsed = readStoredBoolean(
      PANEL_COLLAPSED_STORAGE_KEY,
      narrowPanelMedia.matches,
    );
    if (initiallyCollapsed) div.classList.add("collapsed");

    const metricGroups = [...data.categories]
      .sort((left, right) => left.order - right.order)
      .map((category) => {
        const rows = data.metrics
          .filter((metric) => metric.category_id === category.id)
          .map((metric) => `
            <label class="metric-option">
              <input type="radio" name="climate-metric" value="${metric.id}" ${metric.id === metrics.activeMetric.id ? "checked" : ""}>
              <span>${metric.label}</span>
            </label>`).join("");
        if (!rows) return "";
        return `
          <fieldset class="metric-category" data-category-id="${category.id}">
            <legend class="metric-category-title">${category.label}</legend>
            ${rows}
          </fieldset>`;
      }).join("");


    const serviceRows = services.categories.map((category) => `
      <label class="service-option">
        <input type="checkbox" data-service-category="${category.id}" ${services.isEnabled(category.id) ? "checked" : ""}>
        ${services.legendIconHtml(category.id)}
        <span>${category.label}</span>
      </label>`).join("");

    div.innerHTML = `
      <div class="map-panel-header">
        <div class="map-panel-heading">
          <h1 class="map-panel-title">Goldilocks Map</h1>
          <div class="map-panel-subtitle"></div>
        </div>
        <button class="map-panel-toggle" type="button" aria-label="${initiallyCollapsed ? "Expand" : "Collapse"} map panel" aria-expanded="${!initiallyCollapsed}">${initiallyCollapsed ? "+" : "−"}</button>
      </div>
      <div class="map-panel-body">
        ${data.preview_partial_sources ? '<div class="preview-warning">Preview build: one or more metrics use only the source data currently available.</div>' : ""}
        <div class="panel-section">
          <strong class="panel-section-title">Measure</strong>
          <div class="metric-list">${metricGroups}</div>
        </div>

        <div class="panel-section services-control">
          <strong class="panel-section-title">Services</strong>
          <div class="service-list">${serviceRows}</div>
          <div class="service-status" aria-live="polite"></div>
          <div class="service-source">${services.sourceSummary()}</div>
        </div>
        <div class="panel-section">
          <label class="panel-option">
            <input class="gridlines-toggle" type="checkbox" ${preferences.gridLinesVisible ? "checked" : ""}>
            <span>Show gridlines</span>
          </label>
          <label class="panel-option">
            <input class="locations-toggle" type="checkbox" ${preferences.locationsVisible ? "checked" : ""}>
            <span>Show location pins</span>
          </label>
        </div>
        <div class="panel-section opacity-control">
          <div class="opacity-heading">
            <strong class="panel-section-title">Layer opacity</strong>
            <output class="opacity-output">${Math.round(preferences.layerOpacity * 100)}%</output>
          </div>
          <input class="opacity-input" type="range" min="0" max="100" step="1" value="${Math.round(preferences.layerOpacity * 100)}" aria-label="Data layer opacity" aria-valuetext="${Math.round(preferences.layerOpacity * 100)}%">
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
        <div class="panel-section">
          <details class="legal-notices">
            <summary>Third-party software licences</summary>
            <pre></pre>
          </details>
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
    const serviceStatus = div.querySelector(".service-status") as HTMLElement;
    const legalNotices = div.querySelector(".legal-notices") as HTMLDetailsElement;
    const legalNoticesPre = div.querySelector(".legal-notices pre") as HTMLPreElement;
    legalNoticesPre.textContent = thirdPartyNotices;
    if (!thirdPartyNotices) legalNotices.hidden = true;

    function refreshDisplayRangeControl() {
      const activeMetric = metrics.activeMetric;
      const activeDisplayRange = metrics.activeDisplayRange;
      const full = metrics.fullDisplayRange(activeMetric);
      const minPercent = metrics.displayRangePercent(activeMetric, activeDisplayRange.minEncoded);
      const maxPercent = metrics.displayRangePercent(activeMetric, activeDisplayRange.maxEncoded);
      for (const input of [rangeMin, rangeMax]) {
        input.min = String(full.minEncoded);
        input.max = String(full.maxEncoded);
        input.disabled = full.minEncoded === full.maxEncoded;
      }
      rangeMin.value = String(activeDisplayRange.minEncoded);
      rangeMax.value = String(activeDisplayRange.maxEncoded);
      rangeMin.setAttribute("aria-valuetext", metrics.formatMetricValue(activeMetric, activeDisplayRange.minEncoded));
      rangeMax.setAttribute("aria-valuetext", metrics.formatMetricValue(activeMetric, activeDisplayRange.maxEncoded));
      rangeMinOutput.textContent = metrics.formatMetricNumber(activeMetric, activeDisplayRange.minEncoded);
      rangeMaxOutput.textContent = metrics.formatMetricNumber(activeMetric, activeDisplayRange.maxEncoded);
      rangeActive.style.left = `${minPercent}%`;
      rangeActive.style.right = `${100 - maxPercent}%`;

      const samples = metrics.displayRangeSampleValues();
      const gradientStops = samples.map((value, index) => {
        const percent = samples.length <= 1 ? 50 : (index / (samples.length - 1)) * 100;
        return `${metrics.colorForValue(value)} ${percent}%`;
      });
      rangeActive.style.background = gradientStops.length > 1
        ? `linear-gradient(to right, ${gradientStops.join(", ")})`
        : (samples.length ? metrics.colorForValue(samples[0]) : "#888");
      rangeLegend.style.gridTemplateColumns = `repeat(${Math.max(1, samples.length)}, minmax(0, 1fr))`;
      rangeLegend.innerHTML = samples.map((value, index) => {
        const prefix = index === 0 && value > activeMetric.encoded_min
          ? "≤"
          : (index === samples.length - 1 && value < activeMetric.encoded_max ? "≥" : "");
        return `<div class="range-legend-item">
          <span class="range-legend-swatch" style="background:${metrics.colorForValue(value)}"></span>
          <span>${prefix}${metrics.formatMetricNumber(activeMetric, value)}</span>
        </div>`;
      }).join("");
      rangeUnit.textContent = activeMetric.units;
      rangeReset.disabled = activeDisplayRange.minEncoded === full.minEncoded && activeDisplayRange.maxEncoded === full.maxEncoded;
    }

    function refreshMetricText() {
      const activeMetric = metrics.activeMetric;
      subtitle.textContent = `${activeMetric.label} · ${activeMetric.period}`;
      refreshDisplayRangeControl();
      description.textContent = activeMetric.definition;
      const sourceMeta = data.sources[activeMetric.source_id];
      const pruning = pruningDescription();
      const licence = sourceMeta.licence_name && sourceMeta.licence_url
        ? `<a href="${sourceMeta.licence_url}" target="_blank" rel="noopener">${sourceMeta.licence_name}</a>`
        : (sourceMeta.licence_name ?? "Open Government Licence");
      const datasetLabel = sourceMeta.homepage_url
        ? `<a href="${sourceMeta.homepage_url}" target="_blank" rel="noopener">${sourceMeta.dataset ?? activeMetric.source_id}</a>`
        : (sourceMeta.dataset ?? activeMetric.source_id);
      const citation = sourceMeta.citation
        ? (sourceMeta.citation_url
            ? `<a href="${sourceMeta.citation_url}" target="_blank" rel="noopener">${sourceMeta.citation}</a>`
            : sourceMeta.citation)
        : "";
      const methodCitation = sourceMeta.method_citation
        ? (sourceMeta.method_url
            ? `<a href="${sourceMeta.method_url}" target="_blank" rel="noopener">${sourceMeta.method_citation}</a>`
            : sourceMeta.method_citation)
        : "";
      const releases = (sourceMeta.releases ?? []).map((release) => {
        const label = release.url
          ? `<a href="${release.url}" target="_blank" rel="noopener">${release.label}</a>`
          : release.label;
        return `<div><strong>Release:</strong> ${label}${release.status ? ` (${release.status})` : ""}</div>`;
      }).join("");
      source.innerHTML = `
        ${sourceMeta.provider ?? "Data source"}; ${sourceDescription(sourceMeta)}.<br>
        Dataset: ${datasetLabel}.<br>
        Variable: <code>${activeMetric.source_variable}</code>.<br>
        Licence: ${licence}.<br>
        ${sourceMeta.attribution ? `Attribution: ${sourceMeta.attribution}<br>` : ""}
        ${sourceMeta.note ?? ""}${pruning ? `<br>${pruning}` : ""}
        <details class="provenance-details">
          <summary>Dataset details and status</summary>
          ${citation ? `<div><strong>Citation:</strong> ${citation}</div>` : ""}
          ${methodCitation ? `<div><strong>Method:</strong> ${methodCitation}</div>` : ""}
          ${releases}
          ${sourceMeta.derived_product_notice ? `<div><strong>Derived product:</strong> ${sourceMeta.derived_product_notice}</div>` : ""}
        </details>`;
    }

    const panelButton = div.querySelector(".map-panel-toggle") as HTMLButtonElement;
    function setPanelCollapsed(collapsed: boolean, persist: boolean) {
      if (!collapsed && narrowPanelMedia.matches) locations.setPanelCollapsed(true, true);
      div.classList.toggle("collapsed", collapsed);
      panelButton.textContent = collapsed ? "+" : "−";
      panelButton.setAttribute("aria-expanded", String(!collapsed));
      panelButton.setAttribute("aria-label", `${collapsed ? "Expand" : "Collapse"} map panel`);
      syncNarrowPanelCorner(div, !collapsed, narrowPanelMedia);
      if (persist) writeStoredBoolean(PANEL_COLLAPSED_STORAGE_KEY, collapsed);
    }

    locations.setNarrowPanelPeerCollapse(() => setPanelCollapsed(true, true));
    panelButton.addEventListener("click", () => setPanelCollapsed(!div.classList.contains("collapsed"), true));
    narrowPanelMedia.addEventListener("change", (event) => {
      if (event.matches && !div.classList.contains("collapsed")) locations.setPanelCollapsed(true, true);
      syncNarrowPanelCorner(div, !div.classList.contains("collapsed"), narrowPanelMedia);
    });
    if (narrowPanelMedia.matches && !initiallyCollapsed) locations.setPanelCollapsed(true, true);

    const gridlinesCheckbox = div.querySelector(".gridlines-toggle") as HTMLInputElement;
    gridlinesCheckbox.addEventListener("change", () => {
      const show = gridlinesCheckbox.checked;
      raster.setShowGridLines(show);
      writeStoredBoolean(GRIDLINES_STORAGE_KEY, show);
    });

    const locationsCheckbox = div.querySelector(".locations-toggle") as HTMLInputElement;
    locationsCheckbox.addEventListener("change", () => {
      const show = locationsCheckbox.checked;
      locations.setVisible(show);
      writeStoredBoolean(LOCATIONS_VISIBLE_STORAGE_KEY, show);
    });


    const serviceInputs = Array.from(div.querySelectorAll('input[data-service-category]') as NodeListOf<HTMLInputElement>);
    for (const input of serviceInputs) {
      input.addEventListener("change", () => {
        const categoryId = input.dataset.serviceCategory;
        if (!categoryId) return;
        services.setEnabled(categoryId, input.checked);
      });
    }
    services.onStatusChange((text) => {
      serviceStatus.textContent = text;
      serviceStatus.hidden = !text;
    });

    opacityInput.addEventListener("input", () => {
      const percent = Math.max(0, Math.min(100, Math.round(Number(opacityInput.value))));
      const opacity = percent / 100;
      raster.setOpacity(opacity);
      opacityOutput.textContent = `${percent}%`;
      opacityInput.setAttribute("aria-valuetext", `${percent}%`);
      writeStoredLayerOpacity(opacity);
    });

    let pendingRangeRepaint: number | null = null;
    function scheduleRangeRepaint() {
      if (pendingRangeRepaint !== null) return;
      pendingRangeRepaint = window.requestAnimationFrame(() => {
        pendingRangeRepaint = null;
        raster.repaintVisibleTiles();
      });
    }

    function setActiveDisplayRange(minEncoded: number, maxEncoded: number) {
      metrics.setActiveDisplayRange({ minEncoded, maxEncoded }, true);
      refreshDisplayRangeControl();
      scheduleRangeRepaint();
    }

    rangeMin.addEventListener("input", () => {
      const full = metrics.fullDisplayRange(metrics.activeMetric);
      const gap = full.minEncoded < full.maxEncoded ? 1 : 0;
      const requested = Math.round(Number(rangeMin.value));
      const minEncoded = Math.max(full.minEncoded, Math.min(requested, metrics.activeDisplayRange.maxEncoded - gap));
      if (minEncoded === metrics.activeDisplayRange.minEncoded) {
        rangeMin.value = String(minEncoded);
        return;
      }
      setActiveDisplayRange(minEncoded, metrics.activeDisplayRange.maxEncoded);
    });

    rangeMax.addEventListener("input", () => {
      const full = metrics.fullDisplayRange(metrics.activeMetric);
      const gap = full.minEncoded < full.maxEncoded ? 1 : 0;
      const requested = Math.round(Number(rangeMax.value));
      const maxEncoded = Math.min(full.maxEncoded, Math.max(requested, metrics.activeDisplayRange.minEncoded + gap));
      if (maxEncoded === metrics.activeDisplayRange.maxEncoded) {
        rangeMax.value = String(maxEncoded);
        return;
      }
      setActiveDisplayRange(metrics.activeDisplayRange.minEncoded, maxEncoded);
    });

    rangeReset.addEventListener("click", () => {
      metrics.resetActiveDisplayRange();
      refreshDisplayRangeControl();
      scheduleRangeRepaint();
    });

    const metricInputs = Array.from(div.querySelectorAll('input[name="climate-metric"]') as NodeListOf<HTMLInputElement>);
    for (const input of metricInputs) {
      input.addEventListener("change", () => {
        if (!input.checked || !metrics.selectMetric(input.value)) return;
        map.closePopup();
        raster.setSelectedCell(null);
        refreshMetricText();
        raster.redraw();
      });
    }

    refreshMetricText();
    L.DomEvent.disableClickPropagation(div);
    L.DomEvent.disableScrollPropagation(div);
    window.requestAnimationFrame(() => {
      syncNarrowPanelCorner(div, !div.classList.contains("collapsed"), narrowPanelMedia);
    });
    return div;
  };
  mapPanel.addTo(map);
}
