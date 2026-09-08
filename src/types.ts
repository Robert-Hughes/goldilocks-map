export type RasterLevelMeta = {
  level: number;
  width: number;
  height: number;
  cell_size_m: number;
  value_offset: number;
  value_count: number;
};

export type RasterLevel = Omit<RasterLevelMeta, "value_offset" | "value_count"> & {
  values: Uint16Array;
};

export type MetricCategory = {
  id: string;
  label: string;
  order: number;
};

export type MetricTransport = {
  id: string;
  category_id: string;
  label: string;
  units: string;
  period: string;
  definition: string;
  source_id: string;
  source_variable: string;
  scale: number;
  offset: number;
  decimals: number;
  nodata: number;
  encoded_min: number;
  encoded_max: number;
  summary: { min: number; max: number };
  coverage: Record<string, unknown>;
  palette?: string[];
  display_range?: { min: number; max: number };
  palette_reverse?: boolean;
  levels: RasterLevelMeta[];
  blob_encoding: "gzip+uint16le";
  raw_bytes: number;
  compressed_bytes: number;
  sha256_raw: string;
  blob_base64: string;
};

export type DataSource = {
  provider?: string;
  dataset?: string;
  resolution?: string;
  homepage_url?: string;
  licence_name?: string;
  licence_url?: string;
  attribution?: string;
  citation?: string;
  citation_url?: string;
  method_citation?: string;
  method_url?: string;
  derived_product_notice?: string;
  note?: string;
  releases?: Array<{ label: string; status?: string; url?: string }>;
};

export type ServiceCategoryId =
  | "supermarket"
  | "post_office"
  | "pharmacy"
  | "gp"
  | "dentist"
  | "hospital_general"
  | "hospital_community";

export type ServiceCategory = {
  id: ServiceCategoryId;
  label: string;
  order: number;
  count: number;
};

export type ServiceSource = {
  id: string;
  provider: string;
  distributor?: string;
  dataset: string;
  release?: string;
  extract_date?: string;
  homepage_url?: string;
  licence_name: string;
  licence_url: string;
  attribution: string;
};

export type ServiceTransport = {
  format_version: 3;
  max_visible_markers: number;
  bucket_scale: number;
  coordinate_scale: number;
  categories: ServiceCategory[];
  source_order: string[];
  sources: Record<string, ServiceSource>;
  payload_encoding: "gzip+json";
  raw_bytes: number;
  compressed_bytes: number;
  sha256_raw: string;
  blob_base64: string;
};

export type MilitaryAreaCategoryId = "dangerous" | "unspecified";

export type MilitaryAreaCategory = {
  id: MilitaryAreaCategoryId;
  label: string;
  order: number;
  count: number;
};

export type MilitaryAreaTransport = {
  format_version: 1;
  categories: MilitaryAreaCategory[];
  source: ServiceSource;
  payload_encoding: "gzip+json";
  raw_bytes: number;
  compressed_bytes: number;
  sha256_raw: string;
  blob_base64: string;
};

export type GoldilocksData = {
  format_version: 6;
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
  sources: Record<string, DataSource>;
  services: ServiceTransport;
  military_areas: MilitaryAreaTransport;
  data_pruning: Array<{
    id: string;
    source_id?: string;
    area: string;
    action: string;
    reason: string;
    audit_period?: string;
    cells?: Array<{ row: number; column: number; easting: number; northing: number }>;
  }>;
};

export type MetricRuntime = {
  transport: MetricTransport;
  levels: RasterLevel[];
};

export type RasterCell = {
  row: number;
  column: number;
  index: number;
  encodedValue: number;
  easting: number;
  northing: number;
  lat: number;
  lon: number;
};

export type DisplayRange = {
  minEncoded: number;
  maxEncoded: number;
};
