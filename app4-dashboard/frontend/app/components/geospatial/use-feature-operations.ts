"use client";

import { useCallback, useEffect, useState } from "react";
import type { Feature } from "geojson";
import {
  deleteMapFeature,
  mutateMapFeaturesBulk,
  searchMapFeatures,
  updateMapFeature,
} from "../../lib/api";
import { useI18n } from "../../lib/i18n/provider";
import type { FeatureBulkAction } from "../../lib/types";

export function useFeatureOperations(
  missionId: string | null,
  onRefresh: () => void,
  onNotice: (message: string) => void,
  onError: (message: string) => void,
) {
  const { t } = useI18n();
  const [selectedFeature, setSelectedFeature] = useState<Feature | null>(null);
  const [searchText, setSearchText] = useState("");
  const [searchSource, setSearchSource] = useState("");
  const [searchRun, setSearchRun] = useState("");
  const [searchReviewed, setSearchReviewed] = useState("");
  const [searchDeleted, setSearchDeleted] = useState("false");
  const [searchResults, setSearchResults] = useState<Feature[]>([]);
  const [selectedSearchIds, setSelectedSearchIds] = useState<string[]>([]);
  const [focusBounds, setFocusBounds] = useState<
    [number, number, number, number] | null
  >(null);
  const [busySearch, setBusySearch] = useState(false);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setSelectedFeature(null);
      setSelectedSearchIds([]);
      setSearchResults([]);
      setFocusBounds(null);
      setSearchText("");
      setSearchSource("");
      setSearchRun("");
      setSearchReviewed("");
      setSearchDeleted("false");
      onError("");
      if (!missionId) {
        setBusySearch(false);
        return;
      }
      setBusySearch(true);
      void searchMapFeatures(missionId, { deleted: false })
        .then((response) => {
          if (!active) return;
          setSearchResults(response.features);
          if (response.bounds) setFocusBounds(response.bounds);
        })
        .catch((reason) => {
          if (!active) return;
          onError(
            reason instanceof Error
              ? reason.message
              : t("explorer.searchFailed"),
          );
        })
        .finally(() => {
          if (active) setBusySearch(false);
        });
    });
    return () => {
      active = false;
    };
  }, [missionId, onError, t]);

  const runSearch = useCallback(async () => {
    if (!missionId) return;
    setBusySearch(true);
    onError("");
    try {
      const response = await searchMapFeatures(missionId, {
        q: searchText,
        source: searchSource || undefined,
        runId: searchRun || undefined,
        reviewed:
          searchReviewed === "" ? undefined : searchReviewed === "true",
        deleted: searchDeleted === "true",
      });
      setSearchResults(response.features);
      setSelectedSearchIds([]);
      if (response.bounds) setFocusBounds(response.bounds);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : t("explorer.searchFailed"));
    } finally {
      setBusySearch(false);
    }
  }, [missionId, onError, searchDeleted, searchReviewed, searchRun, searchSource, searchText, t]);

  const mutateBulk = useCallback(async (action: FeatureBulkAction) => {
    if (!missionId || selectedSearchIds.length === 0) return;
    const expectedVersions = Object.fromEntries(
      searchResults.flatMap((feature) => {
        const featureId = String(feature.properties?.feature_id ?? "");
        const version = Number(feature.properties?.version ?? 0);
        return selectedSearchIds.includes(featureId) && version > 0
          ? [[featureId, version]]
          : [];
      }),
    );
    try {
      const result = await mutateMapFeaturesBulk(missionId, {
        action,
        feature_ids: selectedSearchIds,
        expected_versions: expectedVersions,
        reason: "Correction groupée depuis l’explorateur",
      });
      setSelectedSearchIds([]);
      setSelectedFeature(null);
      onRefresh();
      onNotice(t("explorer.bulkUpdated", { count: result.changed_count }));
      await runSearch();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : t("explorer.bulkFailed"));
    }
  }, [missionId, onError, onNotice, onRefresh, runSearch, searchResults, selectedSearchIds, t]);

  const featureId = String(
    selectedFeature?.properties?.feature_id ?? selectedFeature?.id ?? "",
  );
  const removeSelected = useCallback(async () => {
    if (!missionId || !featureId) return;
    try {
      await deleteMapFeature(missionId, featureId, "Retrait depuis l’explorateur");
      setSelectedFeature(null);
      onRefresh();
      onNotice(t("explorer.annotationDeleted"));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : t("explorer.annotationDeleteFailed"));
    }
  }, [featureId, missionId, onError, onNotice, onRefresh, t]);

  const reviewSelected = useCallback(async (reviewed: boolean) => {
    if (!missionId || !featureId || !selectedFeature) return;
    try {
      const result = await mutateMapFeaturesBulk(missionId, {
        action: reviewed ? "review" : "unreview",
        feature_ids: [featureId],
        expected_versions: {
          [featureId]: Number(selectedFeature.properties?.version ?? 1),
        },
      });
      if (result.features[0]) setSelectedFeature(result.features[0]);
      onRefresh();
      onNotice(reviewed
        ? t("explorer.featureReviewed")
        : t("explorer.featureUnreviewed"));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : t("explorer.bulkFailed"));
    }
  }, [featureId, missionId, onError, onNotice, onRefresh, selectedFeature, t]);

  const saveSelected = useCallback(async () => {
    if (!missionId || !selectedFeature || !featureId) return;
    try {
      const updated = await updateMapFeature(missionId, featureId, {
        geometry: selectedFeature.geometry,
        name: selectedFeature.properties?.name || "Annotation",
        description: selectedFeature.properties?.description || "",
        color: selectedFeature.properties?.color || "#10b981",
        tags: selectedFeature.properties?.tags || [],
        version: selectedFeature.properties?.version || 1,
      });
      setSelectedFeature(updated);
      onRefresh();
      onNotice(t("explorer.annotationUpdated"));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : t("explorer.annotationUpdateFailed"));
    }
  }, [featureId, missionId, onError, onNotice, onRefresh, selectedFeature, t]);

  return {
    selectedFeature,
    setSelectedFeature,
    searchText,
    setSearchText,
    searchSource,
    setSearchSource,
    searchRun,
    setSearchRun,
    searchReviewed,
    setSearchReviewed,
    searchDeleted,
    setSearchDeleted,
    searchResults,
    selectedSearchIds,
    setSelectedSearchIds,
    focusBounds,
    setFocusBounds,
    busySearch,
    runSearch,
    mutateBulk,
    removeSelected,
    reviewSelected,
    saveSelected,
  };
}
