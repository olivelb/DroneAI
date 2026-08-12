"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  cancelAnalysis,
  createAnalysis,
  fetchAnalyses,
  fetchBrowse,
  retryAnalysis,
} from "../../lib/api";
import { useI18n } from "../../lib/i18n/provider";
import type { AnalysisCreate, AnalysisRun } from "../../lib/types";
import { DEFAULT_ANALYSIS, retainKnownRunIds } from "./workspace-config";

interface AnalysisWorkspaceOptions {
  setNotice: (message: string) => void;
  setError: (message: string) => void;
}

export function useAnalysisWorkspace(
  missionId: string | null,
  workspacePrefix: string | null,
  { setNotice, setError }: AnalysisWorkspaceOptions,
) {
  const { t } = useI18n();
  const [availableFiles, setAvailableFiles] = useState<string[]>([]);
  const [analyses, setAnalyses] = useState<AnalysisRun[]>([]);
  const [visibleRuns, setVisibleRuns] = useState<string[]>([]);
  const [form, setForm] = useState<AnalysisCreate>(DEFAULT_ANALYSIS);
  const [formVisible, setFormVisible] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    if (!missionId || !workspacePrefix) return;
    const payload = await fetchAnalyses(missionId);
    setAnalyses(payload.runs);
    setVisibleRuns((current) =>
      retainKnownRunIds(current, payload.runs.map((run) => run.run_id)),
    );
  }, [missionId, workspacePrefix]);

  useEffect(() => {
    if (!missionId || !workspacePrefix) return;
    let cancelled = false;
    Promise.all([
      fetchBrowse(`${workspacePrefix}/`).catch(() => []),
      fetchAnalyses(missionId).catch(() => ({ runs: [] })),
    ]).then(([files, runs]) => {
      if (cancelled) return;
      setAvailableFiles(
        files.map(
          (item) => item.path ?? item.name ?? "",
        ),
      );
      setAnalyses(runs.runs);
      setVisibleRuns(
        runs.runs
          .filter((run) => run.status === "completed")
          .map((run) => run.run_id),
      );
    });
    const timer = window.setInterval(() => {
      void refresh().catch(() => undefined);
    }, 4_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [missionId, refresh, workspacePrefix]);

  const visibleAnalyses = useMemo(
    () => analyses.filter(
      (run) =>
        visibleRuns.includes(run.run_id) &&
        (run.status === "completed" || run.tiles_completed > 0),
    ),
    [analyses, visibleRuns],
  );
  const hasDepth = availableFiles.some(
    (file) =>
      file.endsWith("orthomosaic.height.tif") ||
      file.endsWith("facade_orthophoto.height.tif"),
  );

  const submit = async () => {
    if (!missionId) return;
    setSubmitting(true);
    setError("");
    try {
      const created = await createAnalysis(missionId, form);
      setAnalyses((current) => [created, ...current]);
      setVisibleRuns((current) => [...current, created.run_id]);
      setFormVisible(false);
      setNotice(t("explorer.analysisQueued"));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : t("explorer.analysisLaunchFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const retry = async (runId: string) => {
    if (!missionId) return;
    await retryAnalysis(missionId, runId);
    await refresh();
  };
  const cancel = async (runId: string) => {
    if (!missionId) return;
    await cancelAnalysis(missionId, runId);
    await refresh();
  };

  return {
    availableFiles,
    analyses,
    visibleRuns,
    setVisibleRuns,
    form,
    setForm,
    formVisible,
    setFormVisible,
    submitting,
    visibleAnalyses,
    hasDepth,
    submit,
    retry,
    cancel,
  };
}
