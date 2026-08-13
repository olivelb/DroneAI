"use client";

import { useCallback, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type {
  ParameterConfigResponse,
  ParamValue,
  QualityProfileId,
} from "./types";

type ParameterSetter = Dispatch<
  SetStateAction<Record<string, ParamValue>>
>;

export const initialParameterValues = (schema: ParameterConfigResponse) => ({
  ...(schema.pipelines.modern ?? {}),
  ...(schema.quality_profiles.find(
    (profile) => profile.id === schema.quality_profile_default,
  )?.parameters ?? {}),
});

export const qualityProfileParameters = (
  schema: ParameterConfigResponse,
  profileId: QualityProfileId,
) =>
  schema.quality_profiles.find((profile) => profile.id === profileId)
    ?.parameters ?? {};

export function useQualityProfileState(
  schema: ParameterConfigResponse | null,
  setParameters: ParameterSetter,
) {
  const [qualityProfileId, setQualityProfileId] =
    useState<QualityProfileId>("normal-v3");

  const synchronizeQualityProfile = useCallback(
    (nextSchema: ParameterConfigResponse) => {
      setQualityProfileId((current) =>
        nextSchema.quality_profiles.some((profile) => profile.id === current)
          ? current
          : nextSchema.quality_profile_default,
      );
    },
    [],
  );

  const setQualityProfile = useCallback(
    (profileId: QualityProfileId) => {
      if (!schema) return;
      const parameters = qualityProfileParameters(schema, profileId);
      if (Object.keys(parameters).length === 0) return;
      setQualityProfileId(profileId);
      setParameters((current) => ({ ...current, ...parameters }));
    },
    [schema, setParameters],
  );

  return { qualityProfileId, setQualityProfile, synchronizeQualityProfile };
}
