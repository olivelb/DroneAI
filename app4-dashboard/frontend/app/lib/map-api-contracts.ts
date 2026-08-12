import type { Feature, FeatureCollection, Geometry } from "geojson";

import type {
  AnalysisRun,
  FeatureBulkAction,
  RasterLayerStyle,
  RasterMetadata,
} from "./types";
import {
  anyOf,
  arrayOf,
  booleanValue,
  decoder,
  integerValue,
  nonEmptyString,
  nullable,
  nullish,
  numberValue,
  objectWith,
  oneOf,
  recordOf,
  recordValue,
  stringValue,
  tupleOf,
  type Validator,
} from "./contract-decoder";

const coordinates: Validator = (value, path) => {
  if (typeof value === "number" && Number.isFinite(value)) return;
  if (!Array.isArray(value) || value.length === 0) {
    numberValue(value, path);
    return;
  }
  value.forEach((item, index) => coordinates(item, `${path}[${index}]`));
};

export const geometryValue: Validator = (value, path) => {
  const base = objectWith({ type: nonEmptyString });
  base(value, path);
  const geometry = value as Record<string, unknown>;
  if (geometry.type === "GeometryCollection") {
    objectWith({ geometries: arrayOf(geometryValue) })(value, path);
    return;
  }
  if (![
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
  ].includes(String(geometry.type))) {
    oneOf(
      "Point",
      "MultiPoint",
      "LineString",
      "MultiLineString",
      "Polygon",
      "MultiPolygon",
      "GeometryCollection",
    )(geometry.type, `${path}.type`);
  }
  objectWith({ coordinates })(value, path);
};

export const featureValue: Validator = objectWith({
  type: oneOf("Feature"),
  geometry: nullable(geometryValue),
  properties: nullable(recordValue),
}, {
  id: anyOf(stringValue, numberValue),
  bbox: arrayOf(numberValue),
});

export const featureCollectionValue: Validator = objectWith({
  type: oneOf("FeatureCollection"),
  features: arrayOf(featureValue),
}, {
  bounds: nullish(tupleOf(numberValue, numberValue, numberValue, numberValue)),
  bbox: arrayOf(numberValue),
});

const rasterRecipe: Validator = objectWith({
  bands: arrayOf(integerValue),
  display_ranges: arrayOf(nullable(tupleOf(numberValue, numberValue))),
  palette: oneOf("none", "gray", "depth", "terrain", "viridis"),
  opacity: numberValue,
  stretch: oneOf("global-percentile", "fixed"),
});

const rasterStyle: Validator = objectWith({
  style_id: nonEmptyString,
  layer: oneOf("ortho", "depth"),
  name: nonEmptyString,
  style: rasterRecipe,
  is_default: booleanValue,
  version: integerValue,
  created_by: nonEmptyString,
  updated_by: nonEmptyString,
}, {
  artifact_id: nullish(stringValue),
  created_at: nullish(stringValue),
  updated_at: nullish(stringValue),
});

const analysisRun: Validator = objectWith({
  run_id: nonEmptyString,
  vol_id: nonEmptyString,
  name: nonEmptyString,
  description: stringValue,
  color: stringValue,
  tags: arrayOf(stringValue),
  backend: oneOf("yolo", "sam3"),
  classes: arrayOf(stringValue),
  confidence: numberValue,
  tile_size: integerValue,
  persist_results: booleanValue,
  status: nonEmptyString,
  phase: nonEmptyString,
  progress: numberValue,
  total_tiles: integerValue,
  tiles_completed: integerValue,
  detection_count: integerValue,
  retry_count: integerValue,
}, {
  model_variant: nullish(stringValue),
  prompt: stringValue,
  error_message: nullish(stringValue),
  result_s3_key: nullish(stringValue),
  model_manifest: nullish(objectWith({
    schema: nonEmptyString,
    backend: oneOf("yolo", "sam3"),
    identity: objectWith({
      repository: nonEmptyString,
      revision: nonEmptyString,
      artifact: nonEmptyString,
      artifact_sha256: nonEmptyString,
    }),
    libraries: recordOf(stringValue),
    runtime: recordValue,
    inference: recordValue,
  })),
  created_at: nullish(stringValue),
  updated_at: nullish(stringValue),
});

export const parseRasterMetadata = decoder<RasterMetadata>(
  "raster metadata",
  objectWith({
    bounds: objectWith({
      wgs84: tupleOf(numberValue, numberValue, numberValue, numberValue),
    }),
    bands: integerValue,
    min_zoom: integerValue,
    max_zoom: integerValue,
  }, {
    display_ranges: arrayOf(nullable(tupleOf(numberValue, numberValue))),
  }),
);

export const parseFeatureCollection = decoder<FeatureCollection>(
  "GeoJSON feature collection",
  featureCollectionValue,
);

export const parseSearchFeatureCollection = decoder<
  FeatureCollection & { bounds?: [number, number, number, number] | null }
>("map search", featureCollectionValue);

export const parseFeature = decoder<Feature<Geometry>>(
  "GeoJSON feature",
  featureValue,
);

export const parseAnalysisList = decoder<{ runs: AnalysisRun[] }>(
  "analysis list",
  objectWith({ runs: arrayOf(analysisRun) }),
);

export const parseAnalysisRun = decoder<AnalysisRun>(
  "analysis run",
  analysisRun,
);

export const parseBulkFeatureResponse = decoder<{
  action: FeatureBulkAction;
  requested_count: number;
  changed_count: number;
  features: Feature[];
}>(
  "bulk feature mutation",
  objectWith({
    action: oneOf("review", "unreview", "delete", "restore"),
    requested_count: integerValue,
    changed_count: integerValue,
    features: arrayOf(featureValue),
  }),
);

export const parseRasterStyleList = decoder<{
  layer: string;
  styles: RasterLayerStyle[];
}>(
  "raster style list",
  objectWith({
    layer: oneOf("ortho", "depth"),
    styles: arrayOf(rasterStyle),
  }),
);

export const parseRasterStyle = decoder<RasterLayerStyle>(
  "raster style",
  rasterStyle,
);
