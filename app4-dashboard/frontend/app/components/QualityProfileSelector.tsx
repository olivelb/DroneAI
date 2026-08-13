"use client";

import { Gauge } from "lucide-react";
import type { MessageKey } from "../lib/i18n/catalog";
import { useI18n } from "../lib/i18n/provider";
import { useStore } from "../lib/store";
import type { QualityProfileId } from "../lib/types";

const DESCRIPTION_KEYS: Record<QualityProfileId, MessageKey> = {
  "fast-v1": "profile.fast.description",
  "normal-v1": "profile.normal.description",
  "high-quality-v1": "profile.highQuality.description",
  "normal-v2": "profile.normal.description",
  "high-quality-v2": "profile.highQuality.description",
  "normal-v3": "profile.normal.description",
  "high-quality-v3": "profile.highQuality.description",
};

const formatInteger = (value: unknown) =>
  Number(value ?? 0).toLocaleString("en-US");

export default function QualityProfileSelector() {
  const { t } = useI18n();
  const { parameterSchema, qualityProfileId, setQualityProfile } = useStore();
  const profiles = parameterSchema?.quality_profiles ?? [];

  if (profiles.length === 0) return null;

  return (
    <section className="surface p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-[#edf3f1] text-[#0f766e]">
          <Gauge size={17} />
        </span>
        <div>
          <div className="eyebrow">{t("profile.title")}</div>
          <p className="mt-1 text-xs leading-5 text-[#77847f]">
            {t("profile.description")}
          </p>
        </div>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        {profiles.map((profile) => {
          const selected = profile.id === qualityProfileId;
          const resident =
            profile.parameters.gs_resident_partitioning === true ||
            String(profile.parameters.gs_resident_partitioning)
              .trim()
              .toLowerCase() === "true";
          return (
            <button
              key={profile.id}
              type="button"
              aria-pressed={selected}
              onClick={() => setQualityProfile(profile.id)}
              className={`rounded-2xl border p-4 text-left transition ${
                selected
                  ? "border-[#54ad9d] bg-[#edf9f6]"
                  : "border-[#dce5e1] bg-[#fafcfb] hover:border-[#aac3bb]"
              }`}
            >
              <span className="text-sm font-bold text-[#26332f]">
                {profile.name}
              </span>
              <span className="mt-1 block text-[10px] font-semibold uppercase tracking-wide text-[#82908b]">
                {profile.id}
              </span>
              <span className="mt-2 block min-h-10 text-xs leading-5 text-[#6f7d78]">
                {t(DESCRIPTION_KEYS[profile.id])}
              </span>
              <span className="mt-3 flex flex-wrap gap-1.5 text-[10px] font-semibold text-[#47645d]">
                <span>{t("profile.imageSize", { value: formatInteger(profile.parameters.feature_max_image_size) })}</span>
                <span>{t("profile.features", { value: formatInteger(profile.parameters.feature_max_num_features) })}</span>
                <span>{t("profile.iterations", { value: formatInteger(profile.parameters.gs_iterations) })}</span>
                <span>
                  {t(
                    resident
                      ? "profile.gaussiansResident"
                      : profile.parameters.gs_capacity_mode === "adaptive"
                      ? "profile.gaussiansAdaptive"
                      : "profile.gaussians",
                    { value: formatInteger(profile.parameters.gs_cap_max) },
                  )}
                </span>
                {resident && (
                  <span>
                    {t("profile.gaussianSpacing", {
                      value: String(
                        profile.parameters.gs_target_gaussian_spacing_pixels,
                      ),
                    })}
                  </span>
                )}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
