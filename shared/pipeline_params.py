"""Stable public facade for the pipeline parameter contract."""

from shared.pipeline_param_catalog import (
    FEATURE_TYPES as FEATURE_TYPES,
    MATCHER_TYPES as MATCHER_TYPES,
    PARAMETER_METADATA as PARAMETER_METADATA,
    PARAM_OVERRIDE_KEYS as PARAM_OVERRIDE_KEYS,
    PIPELINE_DEFAULTS as PIPELINE_DEFAULTS,
    SAM3_BACKEND_ALIASES as SAM3_BACKEND_ALIASES,
)
from shared.pipeline_param_normalization import (
    coerce_param_value as coerce_param_value,
    merge_mission_pipeline_params as merge_mission_pipeline_params,
    merge_pipeline_params as merge_pipeline_params,
    normalize_ai_backend as normalize_ai_backend,
    normalize_feature_type as normalize_feature_type,
    normalize_matcher_type as normalize_matcher_type,
)
