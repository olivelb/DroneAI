"use client";

import { useCallback, useEffect, useState } from "react";
import { createRasterStyle, fetchRasterStyles } from "../../lib/api";
import type {
  RasterLayerStyle,
  RasterMetadata,
  RasterStyleRecipe,
} from "../../lib/types";
import type { ViewerLayer } from "./workspace-config";

const defaultRecipe = (layer: ViewerLayer): RasterStyleRecipe => ({
  bands: layer === "depth" ? [1] : [1, 2, 3],
  display_ranges: [],
  palette: layer === "depth" ? "terrain" : "none",
  opacity: 1,
  stretch: "global-percentile",
});

export function useRasterStyles(
  missionId: string | null,
  layer: ViewerLayer,
  onError: (message: string) => void,
  onSaved: () => void,
) {
  const [metadata, setMetadata] = useState<RasterMetadata | null>(null);
  const [recipe, setRecipe] = useState<RasterStyleRecipe>(() => defaultRecipe(layer));
  const [savedStyles, setSavedStyles] = useState<RasterLayerStyle[]>([]);
  const [styleName, setStyleName] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!missionId) return;
    let active = true;
    fetchRasterStyles(missionId, layer)
      .then(({ styles }) => {
        if (!active) return;
        const availableStyles = Array.isArray(styles) ? styles : [];
        setSavedStyles(availableStyles);
        const configuredDefault = availableStyles.find((style) => style.is_default);
        setRecipe(configuredDefault?.style ?? defaultRecipe(layer));
      })
      .catch((reason: Error) => {
        if (active) onError(reason.message);
      });
    return () => {
      active = false;
    };
  }, [layer, missionId, onError]);

  const handleMetadata = useCallback((next: RasterMetadata | null) => {
    setMetadata(next);
    if (!next) return;
    setRecipe((current) => {
      if (current.bands.every((band) => band <= next.bands)) return current;
      return {
        ...current,
        bands: [1],
        display_ranges: [],
        palette: "gray",
      };
    });
  }, []);

  const save = useCallback(async () => {
    if (!missionId || !styleName.trim()) return;
    setSaving(true);
    try {
      const created = await createRasterStyle(missionId, layer, {
        name: styleName.trim(),
        style: recipe,
      });
      setSavedStyles((current) => [...current, created]);
      setStyleName("");
      onSaved();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Raster style save failed");
    } finally {
      setSaving(false);
    }
  }, [layer, missionId, onError, onSaved, recipe, styleName]);

  return {
    metadata,
    recipe,
    savedStyles,
    styleName,
    saving,
    setRecipe,
    setStyleName,
    applySavedStyle: (style: RasterLayerStyle) => setRecipe(style.style),
    handleMetadata,
    save,
  };
}
