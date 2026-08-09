"use client";

import { Save } from "lucide-react";
import { useI18n } from "../../lib/i18n/provider";
import type {
  RasterLayerStyle,
  RasterMetadata,
  RasterPalette,
  RasterStyleRecipe,
} from "../../lib/types";

interface RasterStyleControlsProps {
  metadata: RasterMetadata | null;
  recipe: RasterStyleRecipe;
  savedStyles: RasterLayerStyle[];
  styleName: string;
  saving: boolean;
  onRecipeChange: (recipe: RasterStyleRecipe) => void;
  onStyleNameChange: (name: string) => void;
  onSavedStyleApply: (style: RasterLayerStyle) => void;
  onSave: () => void;
}

const bandOptions = (count: number) =>
  Array.from({ length: Math.max(1, count) }, (_, index) => index + 1);

export default function RasterStyleControls({
  metadata,
  recipe,
  savedStyles,
  styleName,
  saving,
  onRecipeChange,
  onStyleNameChange,
  onSavedStyleApply,
  onSave,
}: RasterStyleControlsProps) {
  const { t } = useI18n();
  const options = bandOptions(metadata?.bands ?? 3);
  const singleBand = recipe.bands.length === 1;
  const setMode = (mode: "single" | "rgb") => {
    const bands = mode === "single" ? [1] : [1, 2, 3];
    onRecipeChange({
      ...recipe,
      bands,
      palette: mode === "single" ? "gray" : "none",
      display_ranges: [],
      stretch: "global-percentile",
    });
  };
  const setBand = (index: number, band: number) => {
    const bands = [...recipe.bands];
    bands[index] = band;
    if (new Set(bands).size !== bands.length) return;
    onRecipeChange({ ...recipe, bands, display_ranges: [] });
  };
  const setFixedRange = (position: 0 | 1, value: number) => {
    const current = recipe.display_ranges[0]
      ?? metadata?.display_ranges?.[recipe.bands[0] - 1]
      ?? [0, 1];
    const range: [number, number] = [...current];
    range[position] = value;
    onRecipeChange({
      ...recipe,
      stretch: "fixed",
      display_ranges: [range],
    });
  };

  return (
    <div className="space-y-3 rounded-xl border border-[#dce4e1] p-3">
      <div className="eyebrow">{t("layers.rasterStyle")}</div>
      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => setMode("rgb")}
          disabled={(metadata?.bands ?? 3) < 3}
          className={`min-h-9 rounded-lg border text-xs ${
            !singleBand ? "border-[#68bfae] bg-[#edf9f6]" : "border-[#dce4e1]"
          } disabled:opacity-35`}
        >
          RGB
        </button>
        <button
          type="button"
          onClick={() => setMode("single")}
          className={`min-h-9 rounded-lg border text-xs ${
            singleBand ? "border-[#68bfae] bg-[#edf9f6]" : "border-[#dce4e1]"
          }`}
        >
          {t("layers.singleBand")}
        </button>
      </div>
      <div className={`grid gap-2 ${singleBand ? "grid-cols-1" : "grid-cols-3"}`}>
        {recipe.bands.map((band, index) => (
          <label key={index} className="text-[10px] text-[#66736f]">
            {singleBand ? t("layers.band") : ["R", "G", "B"][index]}
            <select
              value={band}
              onChange={(event) => setBand(index, Number(event.target.value))}
              className="input-control mt-1 text-xs"
            >
              {options.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {singleBand && (
        <label className="block text-[10px] text-[#66736f]">
          {t("layers.palette")}
          <select
            value={recipe.palette}
            onChange={(event) => onRecipeChange({
              ...recipe,
              palette: event.target.value as RasterPalette,
            })}
            className="input-control mt-1 text-xs"
          >
            {(["gray", "depth", "terrain", "viridis"] as RasterPalette[]).map(
              (palette) => <option key={palette} value={palette}>{palette}</option>,
            )}
          </select>
        </label>
      )}
      <label className="block text-[10px] text-[#66736f]">
        {t("layers.stretch")}
        <select
          value={recipe.stretch}
          onChange={(event) => onRecipeChange({
            ...recipe,
            stretch: event.target.value as RasterStyleRecipe["stretch"],
            display_ranges: event.target.value === "fixed"
              ? recipe.display_ranges
              : [],
          })}
          className="input-control mt-1 text-xs"
        >
          <option value="global-percentile">{t("layers.globalPercentile")}</option>
          <option value="fixed">{t("layers.fixedRange")}</option>
        </select>
      </label>
      {singleBand && recipe.stretch === "fixed" && (
        <div className="grid grid-cols-2 gap-2">
          {[0, 1].map((position) => (
            <input
              key={position}
              type="number"
              step="any"
              value={
                recipe.display_ranges[0]?.[position]
                ?? metadata?.display_ranges?.[recipe.bands[0] - 1]?.[position]
                ?? position
              }
              onChange={(event) => setFixedRange(
                position as 0 | 1,
                Number(event.target.value),
              )}
              aria-label={position === 0 ? t("layers.minimum") : t("layers.maximum")}
              className="input-control text-xs"
            />
          ))}
        </div>
      )}
      <label className="block text-xs text-[#66736f]">
        {t("layers.opacity", { percent: Math.round(recipe.opacity * 100) })}
        <input
          type="range"
          min="0.1"
          max="1"
          step="0.05"
          value={recipe.opacity}
          onChange={(event) => onRecipeChange({
            ...recipe,
            opacity: Number(event.target.value),
          })}
          className="mt-2 w-full accent-[#0f766e]"
        />
      </label>
      {!!savedStyles.length && (
        <select
          defaultValue=""
          onChange={(event) => {
            const selected = savedStyles.find(
              (style) => style.style_id === event.target.value,
            );
            if (selected) onSavedStyleApply(selected);
          }}
          className="input-control text-xs"
        >
          <option value="">{t("layers.applyNamedStyle")}</option>
          {savedStyles.map((style) => (
            <option key={style.style_id} value={style.style_id}>
              {style.name}{style.is_default ? " ★" : ""}
            </option>
          ))}
        </select>
      )}
      <div className="flex gap-2">
        <input
          value={styleName}
          onChange={(event) => onStyleNameChange(event.target.value)}
          placeholder={t("layers.styleName")}
          className="input-control min-w-0 flex-1 text-xs"
        />
        <button
          type="button"
          disabled={!styleName.trim() || saving}
          onClick={onSave}
          className="flex min-h-10 items-center gap-1 rounded-xl bg-[#0f766e] px-3 text-xs font-semibold text-white disabled:opacity-40"
        >
          <Save size={13} /> {t("common.save")}
        </button>
      </div>
    </div>
  );
}
