"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchGroundControl,
  importGroundControl,
  prepareGroundControlBundle,
  refreshGroundControlCandidates,
  updateGroundControlObservation,
  updateGroundControlPoint,
} from "../../lib/api";
import { useI18n } from "../../lib/i18n/provider";
import type {
  GcpCollection,
  GcpFeature,
  GcpImportOptions,
  GcpObservation,
} from "../../lib/types";

interface GcpWorkspaceOptions {
  setNotice: (message: string) => void;
  setError: (message: string) => void;
  onPointActivated: () => void;
}

export function useGcpWorkspace(
  missionId: string | null,
  { setNotice, setError, onPointActivated }: GcpWorkspaceOptions,
) {
  const { t } = useI18n();
  const [collection, setCollection] = useState<GcpCollection | null>(null);
  const [selectedPoint, setSelectedPoint] = useState<GcpFeature | null>(null);
  const [busy, setBusy] = useState(false);
  const [photoMarker, setPhotoMarker] = useState<{
    point: GcpFeature;
    observation: GcpObservation;
  } | null>(null);

  const refresh = useCallback(
    async (preferredPointId?: string) => {
      if (!missionId) return null;
      const payload = await fetchGroundControl(missionId);
      setCollection(payload);
      const selected = preferredPointId
        ? payload.features.find(
            (point) => point.properties.point_id === preferredPointId,
          ) ?? null
        : null;
      if (preferredPointId) setSelectedPoint(selected);
      return selected;
    },
    [missionId],
  );

  useEffect(() => {
    if (!missionId) return;
    let active = true;
    void fetchGroundControl(missionId)
      .then((payload) => {
        if (!active) return;
        setCollection(payload);
        setSelectedPoint(null);
        setPhotoMarker(null);
      })
      .catch(() => {
        if (active) setCollection(null);
      });
    return () => {
      active = false;
    };
  }, [missionId]);

  const selectPoint = useCallback(
    (point: GcpFeature) => {
      if (selectedPoint?.properties.point_id === point.properties.point_id) {
        setSelectedPoint(null);
        return;
      }
      setSelectedPoint(point);
      onPointActivated();
    },
    [onPointActivated, selectedPoint?.properties.point_id],
  );

  const importSet = async (file: File, options: GcpImportOptions) => {
    if (!missionId) return;
    setBusy(true);
    setError("");
    try {
      await importGroundControl(missionId, file, options);
      await refresh();
      setNotice(t("gcp.imported"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("gcp.importFailed"));
    } finally {
      setBusy(false);
    }
  };

  const updatePoint = async (
    point: GcpFeature,
    request: Record<string, unknown>,
  ) => {
    if (!missionId) return;
    setBusy(true);
    setError("");
    try {
      await updateGroundControlPoint(missionId, point.properties.point_id, request);
      await refresh(point.properties.point_id);
      setNotice(t("gcp.pointSaved"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("gcp.pointSaveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const prepareBundle = async (point: GcpFeature) => {
    if (!missionId) return;
    setBusy(true);
    setError("");
    try {
      const bundle = await prepareGroundControlBundle(
        missionId,
        point.properties.set_id,
      );
      setNotice(t("gcp.bundleReady", {
        adjustment: bundle.quality.adjustment_points,
        checkpoints: bundle.quality.checkpoint_points,
        observations: bundle.quality.marked_observations,
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("gcp.bundleFailed"));
    } finally {
      setBusy(false);
    }
  };

  const refreshCandidates = async (point: GcpFeature) => {
    if (!missionId) return;
    setBusy(true);
    setError("");
    try {
      const result = await refreshGroundControlCandidates(
        missionId,
        point.properties.set_id,
      );
      await refresh(point.properties.point_id);
      setNotice(t("gcp.candidatesRefreshed", {
        count: result.candidate_generation.added_observation_count,
      }));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : t("gcp.candidateRefreshFailed"),
      );
    } finally {
      setBusy(false);
    }
  };

  const finishPhoto = async (
    status: "marked" | "skipped",
    pixel?: { x: number; y: number },
  ) => {
    if (!missionId || !photoMarker) return;
    setBusy(true);
    setError("");
    try {
      await updateGroundControlObservation(
        missionId,
        photoMarker.observation.observation_id,
        {
          status,
          ...(pixel ? { pixel_x: pixel.x, pixel_y: pixel.y } : {}),
          version: photoMarker.observation.version,
        },
      );
      const refreshed = await refresh(photoMarker.point.properties.point_id);
      const nextObservation = refreshed?.properties.observations.find(
        (item) =>
          item.status === "candidate" &&
          item.observation_id !== photoMarker.observation.observation_id,
      );
      setPhotoMarker(
        refreshed && nextObservation
          ? { point: refreshed, observation: nextObservation }
          : null,
      );
      setNotice(status === "marked" ? t("gcp.photoMarked") : t("gcp.photoSkipped"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("gcp.photoSaveFailed"));
    } finally {
      setBusy(false);
    }
  };

  return {
    collection,
    selectedPoint,
    setSelectedPoint,
    busy,
    photoMarker,
    setPhotoMarker,
    selectPoint,
    importSet,
    updatePoint,
    prepareBundle,
    refreshCandidates,
    finishPhoto,
  };
}
