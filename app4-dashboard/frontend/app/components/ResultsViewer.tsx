"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Feature, Geometry } from "geojson";
import { AlertTriangle, CheckCircle2, Map as MapIcon, X } from "lucide-react";
import {
  cancelAnalysis,
  createAnalysis,
  createMapFeature,
  fetchAnalyses,
  fetchBrowse,
  retryAnalysis,
} from "../lib/api";
import { useI18n } from "../lib/i18n/provider";
import { useMissionRuntime } from "../lib/mission-runtime";
import type { AnalysisCreate, AnalysisRun } from "../lib/types";
import type { MapTool } from "./GeospatialMap";
import {
  DraftFeatureEditor,
  SelectedFeatureEditor,
} from "./geospatial/FeatureEditors";
import ViewerHeader from "./geospatial/ViewerHeader";
import ViewerSidePanel from "./geospatial/ViewerSidePanel";
import ViewerToolbar from "./geospatial/ViewerToolbar";
import { useRasterStyles } from "./geospatial/use-raster-styles";
import { useFeatureOperations } from "./geospatial/use-feature-operations";
import {
  DEFAULT_ANALYSIS,
  retainKnownRunIds,
  splitTags,
  TOOL_SHORTCUTS,
  type ViewerLayer,
  type WorkspacePanel,
} from "./geospatial/workspace-config";

const GeospatialMap = dynamic(() => import("./GeospatialMap"), {
  ssr: false,
});

const geometryTool = (geometry?: Geometry): MapTool => {
  if (geometry?.type === "Point" || geometry?.type === "MultiPoint") {
    return "point";
  }
  if (geometry?.type === "LineString" || geometry?.type === "MultiLineString") {
    return "line";
  }
  return "polygon";
};

const isTypingTarget = (target: EventTarget | null) =>
  target instanceof HTMLElement &&
  (target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT" ||
    target.isContentEditable);

export default function ResultsViewer() {
  const { t } = useI18n();
  const { activeMission, missions } = useMissionRuntime();
  const sortedMissions = useMemo(
    () =>
      Object.values(missions).sort(
        (left, right) => right.updated_at - left.updated_at,
      ),
    [missions],
  );
  const [selectedVol, setSelectedVol] = useState<string | null>(null);
  const mission = selectedVol ? missions[selectedVol] : activeMission;
  const missionId = mission?.vol_id ?? sortedMissions[0]?.vol_id ?? null;

  const [expanded, setExpanded] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [activePanel, setActivePanel] = useState<WorkspacePanel>("layers");
  const [activeLayer, setActiveLayer] = useState<ViewerLayer>("ortho");
  const [showLegacy, setShowLegacy] = useState(true);
  const [showManual, setShowManual] = useState(true);
  const [availableFiles, setAvailableFiles] = useState<string[]>([]);
  const [analyses, setAnalyses] = useState<AnalysisRun[]>([]);
  const [visibleRuns, setVisibleRuns] = useState<string[]>([]);
  const [analysisForm, setAnalysisForm] =
    useState<AnalysisCreate>(DEFAULT_ANALYSIS);
  const [showAnalysisForm, setShowAnalysisForm] = useState(false);
  const [submittingAnalysis, setSubmittingAnalysis] = useState(false);

  const [tool, setTool] = useState<MapTool>("navigate");
  const [toolHint, setToolHint] = useState("");
  const [draftGeometry, setDraftGeometry] = useState<Geometry | null>(null);
  const [redrawingFeature, setRedrawingFeature] = useState(false);
  const [measurement, setMeasurement] = useState("");
  const [annotationName, setAnnotationName] = useState("");
  const [annotationDescription, setAnnotationDescription] = useState("");
  const [annotationColor, setAnnotationColor] = useState("#10b981");
  const [annotationTags, setAnnotationTags] = useState("terrain");
  const [refreshToken, setRefreshToken] = useState(0);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const refreshFeatures = useCallback(
    () => setRefreshToken((value) => value + 1),
    [],
  );
  const setFeatureNotice = useCallback((message: string) => setNotice(message), []);
  const setFeatureError = useCallback((message: string) => setError(message), []);
  const featureOperations = useFeatureOperations(
    missionId,
    refreshFeatures,
    setFeatureNotice,
    setFeatureError,
  );
  const {
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
  } = featureOperations;
  const setRasterError = useCallback((message: string) => setError(message), []);
  const announceRasterStyleSaved = useCallback(
    () => setNotice(t("explorer.rasterStyleSaved")),
    [t],
  );
  const rasterStyles = useRasterStyles(
    missionId,
    activeLayer,
    setRasterError,
    announceRasterStyleSaved,
  );

  const refreshAnalyses = useCallback(async () => {
    if (!missionId) return;
    const payload = await fetchAnalyses(missionId);
    setAnalyses(payload.runs);
    setVisibleRuns((current) =>
      retainKnownRunIds(
        current,
        payload.runs.map((run) => run.run_id),
      ),
    );
  }, [missionId]);

  useEffect(() => {
    if (!missionId) return;
    let cancelled = false;
    Promise.all([
      fetchBrowse(`missions/${missionId}/`).catch(() => []),
      fetchAnalyses(missionId).catch(() => ({ runs: [] })),
    ]).then(([files, runs]) => {
      if (cancelled) return;
      setAvailableFiles(
        (files as Record<string, string>[]).map(
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
      void refreshAnalyses().catch(() => undefined);
    }, 4_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [missionId, refreshAnalyses]);

  useEffect(() => {
    if (!expanded) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [expanded]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (isTypingTarget(event.target)) return;
      const key = event.key.toLocaleLowerCase();
      const shortcut = Object.entries(TOOL_SHORTCUTS).find(
        ([, value]) => value.toLocaleLowerCase() === key,
      );
      if (shortcut) {
        const nextTool = shortcut[0] as MapTool;
        setTool(nextTool);
        setRedrawingFeature(false);
        if (nextTool !== "navigate") {
          setSelectedFeature(null);
          setDraftGeometry(null);
          setMeasurement("");
        }
        return;
      }
      if (key === "f") {
        setExpanded((current) => !current);
        return;
      }
      if (key === "b") {
        setPanelOpen((current) => !current);
        return;
      }
      if (event.key === "Escape") {
        if (draftGeometry) {
          setDraftGeometry(null);
          return;
        }
        if (redrawingFeature || tool !== "navigate") {
          setRedrawingFeature(false);
          setTool("navigate");
          return;
        }
        if (expanded) setExpanded(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [draftGeometry, expanded, redrawingFeature, setSelectedFeature, tool]);

  const hasDepth = availableFiles.some((file) =>
    file.endsWith("orthomosaic.height.tif") ||
    file.endsWith("facade_orthophoto.height.tif"),
  );
  const visibleAnalyses = useMemo(
    () =>
      analyses.filter(
        (run) =>
          visibleRuns.includes(run.run_id) &&
          (run.status === "completed" || run.tiles_completed > 0),
      ),
    [analyses, visibleRuns],
  );

  const submitAnalysis = async () => {
    if (!missionId) return;
    setSubmittingAnalysis(true);
    setError("");
    try {
      const created = await createAnalysis(missionId, analysisForm);
      setAnalyses((current) => [created, ...current]);
      setVisibleRuns((current) => [...current, created.run_id]);
      setShowAnalysisForm(false);
      setNotice(t("explorer.analysisQueued"));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : t("explorer.analysisLaunchFailed"),
      );
    } finally {
      setSubmittingAnalysis(false);
    }
  };

  const showSearch = async () => {
    await featureOperations.runSearch();
    setActivePanel("search");
    setPanelOpen(true);
  };

  const geometryReady = (geometry: Geometry, result?: string) => {
    if (redrawingFeature && selectedFeature) {
      setSelectedFeature({ ...selectedFeature, geometry });
      setRedrawingFeature(false);
      setTool("navigate");
      setNotice(t("explorer.geometryReady"));
      return;
    }
    setDraftGeometry(geometry);
    setMeasurement(result ?? "");
    setTool("navigate");
    setAnnotationName(
      result
        ? t("explorer.measurementName", { measurement: result })
        : t("explorer.newAnnotation"),
    );
  };

  const saveAnnotation = async () => {
    if (!missionId || !draftGeometry || !annotationName.trim()) return;
    setError("");
    try {
      await createMapFeature(missionId, {
        geometry: draftGeometry,
        name: annotationName.trim(),
        description: annotationDescription.trim(),
        color: annotationColor,
        tags: splitTags(annotationTags),
        properties: measurement ? { measurement } : {},
      });
      setDraftGeometry(null);
      setMeasurement("");
      setRefreshToken((value) => value + 1);
      setNotice(t("explorer.annotationSaved"));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : t("explorer.annotationSaveFailed"),
      );
    }
  };

  const beginRedraw = () => {
    if (!selectedFeature?.geometry) return;
    setRedrawingFeature(true);
    setTool(geometryTool(selectedFeature.geometry));
    setNotice("");
  };

  const selectFeature = (feature: Feature) => {
    setSelectedFeature(feature);
    setDraftGeometry(null);
    setRedrawingFeature(false);
    setTool("navigate");
  };

  if (!missionId || sortedMissions.length === 0) {
    return (
      <div className="surface flex min-h-[520px] items-center justify-center text-[#87938f]">
        <div className="text-center">
          <MapIcon size={30} className="mx-auto mb-3" />
          <p className="font-semibold">
            {t("explorer.noProducts")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className={expanded ? "viewer-fullscreen" : "space-y-3"}>
      <ViewerHeader
        expanded={expanded}
        panelOpen={panelOpen}
        missionId={missionId}
        selectedMission={selectedVol ?? missionId}
        missions={sortedMissions}
        searchText={searchText}
        busySearch={busySearch}
        onMissionChange={setSelectedVol}
        onSearchTextChange={setSearchText}
        onSearch={() => void showSearch()}
        onPanelToggle={() => setPanelOpen((current) => !current)}
        onShortcutsToggle={() => setShortcutsOpen((current) => !current)}
        onExpandedToggle={() => setExpanded((current) => !current)}
      />

      {(notice || error) && (
        <div
          className={`mx-3 mt-2 flex shrink-0 items-center justify-between rounded-xl px-4 py-2.5 text-sm ${
            error
              ? "bg-rose-50 text-rose-700"
              : "bg-emerald-50 text-emerald-700"
          }`}
        >
          <span className="flex items-center gap-2">
            {error ? <AlertTriangle size={16} /> : <CheckCircle2 size={16} />}
            {error || notice}
          </span>
          <button
            type="button"
            onClick={() => {
              setError("");
              setNotice("");
            }}
            aria-label={t("common.close")}
          >
            <X size={15} />
          </button>
        </div>
      )}

      <div
        className={`min-h-0 ${
          expanded ? "flex-1 p-2 sm:p-3" : ""
        }`}
      >
        <div
          className={`relative grid min-h-0 gap-3 ${
            expanded ? "h-full" : "min-h-[720px] xl:h-[calc(100vh-220px)]"
          } ${
            panelOpen
              ? "xl:grid-cols-[320px_minmax(0,1fr)]"
              : "grid-cols-[minmax(0,1fr)]"
          }`}
        >
          {panelOpen && (
            <ViewerSidePanel
              expanded={expanded}
              activePanel={activePanel}
              onPanelChange={setActivePanel}
              layers={{
                missionId,
                activeLayer,
                hasDepth,
                availableFiles,
                rasterMetadata: rasterStyles.metadata,
                rasterStyle: rasterStyles.recipe,
                savedRasterStyles: rasterStyles.savedStyles,
                rasterStyleName: rasterStyles.styleName,
                savingRasterStyle: rasterStyles.saving,
                showLegacy,
                showManual,
                analyses,
                visibleRuns,
                onLayerChange: setActiveLayer,
                onRasterStyleChange: rasterStyles.setRecipe,
                onRasterStyleNameChange: rasterStyles.setStyleName,
                onSavedRasterStyleApply: rasterStyles.applySavedStyle,
                onRasterStyleSave: () => void rasterStyles.save(),
                onLegacyChange: setShowLegacy,
                onManualChange: setShowManual,
                onRunVisibilityChange: (runId, visible) =>
                  setVisibleRuns((current) =>
                    visible
                      ? [...new Set([...current, runId])]
                      : current.filter((id) => id !== runId),
                  ),
              }}
              analysis={{
                analyses,
                form: analysisForm,
                formVisible: showAnalysisForm,
                submitting: submittingAnalysis,
                onFormChange: setAnalysisForm,
                onFormVisibilityChange: setShowAnalysisForm,
                onSubmit: () => void submitAnalysis(),
                onRetry: (runId) =>
                  void retryAnalysis(missionId, runId).then(refreshAnalyses),
                onCancel: (runId) =>
                  void cancelAnalysis(missionId, runId).then(refreshAnalyses),
              }}
              search={{
                source: searchSource,
                runId: searchRun,
                reviewed: searchReviewed,
                deleted: searchDeleted,
                analyses,
                results: searchResults,
                selectedIds: selectedSearchIds,
                onSourceChange: setSearchSource,
                onRunChange: setSearchRun,
                onReviewedChange: setSearchReviewed,
                onDeletedChange: setSearchDeleted,
                onSelectionChange: setSelectedSearchIds,
                onBulkAction: (action) => void featureOperations.mutateBulk(action),
                onSearch: () => void showSearch(),
                onFeatureSelect: selectFeature,
                onFocus: setFocusBounds,
              }}
              exportPanel={{ missionId, hasDepth, visibleRunIds: visibleRuns }}
            />
          )}

          <main className="viewer-map-shell relative order-1 min-h-[620px] overflow-hidden rounded-[1.25rem] border border-[#263632] bg-[#16201d] shadow-[0_20px_50px_rgba(20,32,28,0.12)] xl:order-none xl:min-h-0">
            <ViewerToolbar
              tool={tool}
              toolHint={toolHint}
              redrawingFeature={redrawingFeature}
              shortcutsOpen={shortcutsOpen}
              onShortcutsClose={() => setShortcutsOpen(false)}
              onToolChange={(nextTool) => {
                setTool(nextTool);
                setRedrawingFeature(false);
                if (nextTool !== "navigate") {
                  setSelectedFeature(null);
                  setDraftGeometry(null);
                  setMeasurement("");
                }
              }}
            />

            <GeospatialMap
              missionId={missionId}
              layer={activeLayer}
              rasterStyle={rasterStyles.recipe}
              showLegacy={showLegacy}
              showManual={showManual}
              analyses={visibleAnalyses}
              tool={tool}
              focusBounds={focusBounds}
              refreshToken={refreshToken}
              onGeometryReady={geometryReady}
              onFeatureSelect={selectFeature}
              onHint={setToolHint}
              onMetadata={rasterStyles.handleMetadata}
            />
            {draftGeometry && (
              <DraftFeatureEditor
                measurement={measurement}
                name={annotationName}
                description={annotationDescription}
                color={annotationColor}
                tags={annotationTags}
                onNameChange={setAnnotationName}
                onDescriptionChange={setAnnotationDescription}
                onColorChange={setAnnotationColor}
                onTagsChange={setAnnotationTags}
                onClose={() => setDraftGeometry(null)}
                onSave={() => void saveAnnotation()}
              />
            )}
            {selectedFeature && !draftGeometry && !redrawingFeature && (
              <SelectedFeatureEditor
                feature={selectedFeature}
                onChange={setSelectedFeature}
                onClose={() => setSelectedFeature(null)}
                onDelete={() => void featureOperations.removeSelected()}
                onRedraw={beginRedraw}
                onSave={() => void featureOperations.saveSelected()}
                onReview={(reviewed) => void featureOperations.reviewSelected(reviewed)}
              />
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
