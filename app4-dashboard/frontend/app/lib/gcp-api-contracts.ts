import type {
  GcpAuditEvent,
  GcpBundle,
  GcpCollection,
  GcpFeature,
  GcpObservation,
  GcpSetDetail,
  GcpSetSummary,
} from "./types";
import {
  arrayOf,
  decoder,
  integerValue,
  nonEmptyString,
  nullish,
  numberValue,
  objectWith,
  oneOf,
  recordValue,
  stringValue,
  tupleOf,
  type Validator,
} from "./contract-decoder";
import {
  featureCollectionValue,
  geometryValue,
} from "./map-api-contracts";

const observation: Validator = objectWith({
  observation_id: nonEmptyString,
  image_name: nonEmptyString,
  status: oneOf("candidate", "marked", "skipped"),
  version: integerValue,
  updated_at: nonEmptyString,
}, {
  image_s3_key: nullish(stringValue),
  pixel_x: nullish(numberValue),
  pixel_y: nullish(numberValue),
  candidate_distance_m: nullish(numberValue),
  candidate_method: nullish(oneOf(
    "camera-projection",
    "exif-distance",
    "imported-observation",
  )),
  projected_pixel_x: nullish(numberValue),
  projected_pixel_y: nullish(numberValue),
  image_width_px: nullish(integerValue),
  image_height_px: nullish(integerValue),
  image_longitude: nullish(numberValue),
  image_latitude: nullish(numberValue),
});

const pointProperties: Validator = objectWith({
  point_id: nonEmptyString,
  set_id: nonEmptyString,
  set_name: nonEmptyString,
  external_id: nonEmptyString,
  altitude_m: numberValue,
  source_coordinates: tupleOf(numberValue, numberValue, numberValue),
  role: oneOf("adjustment", "checkpoint", "disabled"),
  horizontal_accuracy_m: numberValue,
  vertical_accuracy_m: numberValue,
  image_accuracy_px: numberValue,
  observation_summary: objectWith({
    candidate: integerValue,
    marked: integerValue,
    skipped: integerValue,
  }),
  observations: arrayOf(observation),
  properties: recordValue,
  version: integerValue,
  updated_at: nonEmptyString,
});

const gcpFeature: Validator = (value, path) => {
  objectWith({
    type: oneOf("Feature"),
    geometry: geometryValue,
    properties: pointProperties,
  })(value, path);
  const feature = value as Record<string, unknown>;
  const geometry = feature.geometry as Record<string, unknown>;
  oneOf("Point")(geometry.type, `${path}.geometry.type`);
};

const setSummary: Validator = objectWith({
  set_id: nonEmptyString,
  name: nonEmptyString,
  source_filename: nonEmptyString,
  source_format: nonEmptyString,
  source_crs: nonEmptyString,
  source_sha256: nonEmptyString,
  point_count: integerValue,
  adjustment_count: integerValue,
  checkpoint_count: integerValue,
  marked_observation_count: integerValue,
  version: integerValue,
  created_at: nonEmptyString,
  updated_at: nonEmptyString,
});

const gcpCollection: Validator = (value, path) => {
  featureCollectionValue(value, path);
  objectWith({
    features: arrayOf(gcpFeature),
    gcp_sets: arrayOf(setSummary),
  })(value, path);
};

const gcpSetDetail: Validator = (value, path) => {
  featureCollectionValue(value, path);
  setSummary(value, path);
  objectWith({ features: arrayOf(gcpFeature) })(value, path);
};

const bundleFile = objectWith({
  key: nonEmptyString,
  size: integerValue,
  sha256: nonEmptyString,
});

const bundle: Validator = objectWith({
  schema_version: oneOf(1),
  set_id: nonEmptyString,
  source_sha256: nonEmptyString,
  gcp_list: bundleFile,
  accuracy_csv: bundleFile,
  quality: objectWith({
    adjustment_points: integerValue,
    checkpoint_points: integerValue,
    marked_observations: integerValue,
    verification: oneOf(
      "independent-checkpoints",
      "adjustment-only-unverified",
    ),
  }),
});

const auditEvent: Validator = objectWith({
  event_id: nonEmptyString,
  action: oneOf(
    "imported",
    "point_updated",
    "observation_updated",
    "candidates_refreshed",
    "bundle_materialized",
  ),
  actor_subject: nonEmptyString,
  created_at: nonEmptyString,
}, {
  point_id: nullish(stringValue),
  observation_id: nullish(stringValue),
  before_state: nullish(recordValue),
  after_state: nullish(recordValue),
});

export const parseGcpCollection = decoder<GcpCollection>(
  "GCP collection",
  gcpCollection,
);

export const parseGcpImport = decoder<{
  gcp_set: GcpSetSummary;
  candidate_generation: Record<string, unknown>;
}>(
  "GCP import",
  objectWith({
    gcp_set: setSummary,
    candidate_generation: recordValue,
  }),
);

export const parseGcpFeature = decoder<GcpFeature>(
  "GCP point",
  gcpFeature,
);

export const parseGcpObservation = decoder<GcpObservation>(
  "GCP observation",
  observation,
);

export const parseGcpBundle = decoder<GcpBundle>(
  "GCP bundle",
  bundle,
);

export const parseGcpCandidateRefresh = decoder<{
  gcp_set: GcpSetDetail;
  candidate_generation: { added_observation_count: number };
}>(
  "GCP candidate refresh",
  objectWith({
    gcp_set: gcpSetDetail,
    candidate_generation: objectWith({
      added_observation_count: integerValue,
    }),
  }),
);

export const parseGcpAudit = decoder<{
  set_id: string;
  events: GcpAuditEvent[];
}>(
  "GCP audit",
  objectWith({
    set_id: nonEmptyString,
    events: arrayOf(auditEvent),
  }),
);
