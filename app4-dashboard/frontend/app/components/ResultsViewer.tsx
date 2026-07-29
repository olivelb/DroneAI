"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Feature, Geometry } from "geojson";
import {
  AlertTriangle,
  CheckCircle2,
  Layers,
  Map as MapIcon,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import {
  cancelAnalysis,
  createAnalysis,
  createMapFeature,
  deleteMapFeature,
  fetchAnalyses,
  fetchBrowse,
  retryAnalysis,
  searchMapFeatures,
  updateMapFeature,
} from "../lib/api";
import { useStore } from "../lib/store";
import type { AnalysisCreate, AnalysisRun } from "../lib/types";
import type { MapTool } from "./GeospatialMap";
import AnalysisPanel from "./geospatial/AnalysisPanel";
import {
  DraftFeatureEditor,
  SelectedFeatureEditor,
} from "./geospatial/FeatureEditors";
import LayersPanel from "./geospatial/LayersPanel";
import SearchPanel from "./geospatial/SearchPanel";
import {
  DEFAULT_ANALYSIS,
  splitTags,
  TOOL_BUTTONS,
  type ViewerLayer,
  type WorkspacePanel,
} from "./geospatial/workspace-config";

const GeospatialMap = dynamic(() => import("./GeospatialMap"), {
  ssr: false,
});

export default function ResultsViewer() {
  const { activeMission, missions } = useStore();
  const sortedMissions = useMemo(
    () =>
      Object.values(missions).sort(
        (left, right) => right.updated_at - left.updated_at,
      ),
    [missions],
  );
  const [selectedVol, setSelectedVol] = useState<string | null>(null);
  const mission = selectedVol ? missions[selectedVol] : activeMission;
  const missionId =
    mission?.vol_id ?? sortedMissions[0]?.vol_id ?? null;

  const [activePanel, setActivePanel] =
    useState<WorkspacePanel>("layers");
  const [activeLayer, setActiveLayer] =
    useState<ViewerLayer>("ortho");
  const [rasterOpacity, setRasterOpacity] = useState(1);
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
  const [draftGeometry, setDraftGeometry] =
    useState<Geometry | null>(null);
  const [measurement, setMeasurement] = useState("");
  const [annotationName, setAnnotationName] = useState("");
  const [annotationDescription, setAnnotationDescription] =
    useState("");
  const [annotationColor, setAnnotationColor] = useState("#10b981");
  const [annotationTags, setAnnotationTags] = useState("terrain");
  const [selectedFeature, setSelectedFeature] =
    useState<Feature | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  const [searchText, setSearchText] = useState("");
  const [searchSource, setSearchSource] = useState("");
  const [searchRun, setSearchRun] = useState("");
  const [searchResults, setSearchResults] = useState<Feature[]>([]);
  const [focusBounds, setFocusBounds] = useState<
    [number, number, number, number] | null
  >(null);
  const [busySearch, setBusySearch] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const refreshAnalyses = useCallback(async () => {
    if (!missionId) return;
    const payload = await fetchAnalyses(missionId);
    setAnalyses(payload.runs);
    setVisibleRuns((current) => {
      const known = new Set(payload.runs.map((run) => run.run_id));
      const retained = current.filter((runId) => known.has(runId));
      const completed = payload.runs
        .filter((run) => run.status === "completed")
        .map((run) => run.run_id);
      return [...new Set([...retained, ...completed])];
    });
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

  const hasDepth = availableFiles.some((file) =>
    file.endsWith("orthomosaic.height.tif"),
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
      setNotice("Analyse IA mise en file avec reprise automatique.");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Échec du lancement",
      );
    } finally {
      setSubmittingAnalysis(false);
    }
  };

  const runSearch = async () => {
    if (!missionId) return;
    setBusySearch(true);
    setError("");
    try {
      const response = await searchMapFeatures(missionId, {
        q: searchText,
        source: searchSource || undefined,
        runId: searchRun || undefined,
      });
      setSearchResults(response.features);
      if (response.bounds) setFocusBounds(response.bounds);
      setActivePanel("search");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Recherche impossible",
      );
    } finally {
      setBusySearch(false);
    }
  };

  const geometryReady = (geometry: Geometry, result?: string) => {
    setDraftGeometry(geometry);
    setMeasurement(result ?? "");
    setTool("navigate");
    setAnnotationName(
      result ? `Mesure ${result}` : "Nouvelle annotation",
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
      setNotice("Annotation enregistrée dans la couche collaborative.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Échec de l’enregistrement",
      );
    }
  };

  const selectedFeatureId = String(
    selectedFeature?.properties?.feature_id ??
      selectedFeature?.id ??
      "",
  );

  const removeSelected = async () => {
    if (!missionId || !selectedFeatureId) return;
    try {
      await deleteMapFeature(missionId, selectedFeatureId);
      setSelectedFeature(null);
      setRefreshToken((value) => value + 1);
      setNotice("Annotation supprimée.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Suppression impossible",
      );
    }
  };

  const saveSelected = async () => {
    if (!missionId || !selectedFeature || !selectedFeatureId) return;
    try {
      const updated = await updateMapFeature(
        missionId,
        selectedFeatureId,
        {
          name: selectedFeature.properties?.name || "Annotation",
          description: selectedFeature.properties?.description || "",
          color: selectedFeature.properties?.color || "#10b981",
          tags: selectedFeature.properties?.tags || [],
          version: selectedFeature.properties?.version || 1,
        },
      );
      setSelectedFeature(updated);
      setRefreshToken((value) => value + 1);
      setNotice("Annotation mise à jour.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Mise à jour impossible",
      );
    }
  };

  if (!missionId || sortedMissions.length === 0) {
    return (
      <div className="surface flex min-h-[520px] items-center justify-center text-[#87938f]">
        <div className="text-center">
          <MapIcon size={30} className="mx-auto mb-3" />
          <p className="font-semibold">
            Aucun produit cartographique disponible
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section className="surface flex flex-col gap-4 p-4 sm:p-5 xl:flex-row xl:items-center">
        <div className="min-w-0">
          <div className="eyebrow">Espace géospatial</div>
          <h2 className="mt-1 text-2xl font-bold tracking-[-0.035em] text-[#17201e]">
            Analyse, recherche et annotation
          </h2>
        </div>
        <div className="flex flex-1 flex-col gap-2 sm:flex-row xl:justify-end">
          <select
            value={selectedVol ?? missionId}
            onChange={(event) => setSelectedVol(event.target.value)}
            className="input-control min-h-11 sm:max-w-64"
          >
            {sortedMissions.map((item) => (
              <option key={item.vol_id} value={item.vol_id}>
                {item.vol_id}
              </option>
            ))}
          </select>
          <div className="flex min-w-0 flex-1 sm:max-w-xl">
            <input
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              onKeyDown={(event) =>
                event.key === "Enter" && void runSearch()
              }
              placeholder="Nom, description, tag ou classe…"
              className="input-control min-h-11 rounded-r-none"
            />
            <button
              type="button"
              onClick={() => void runSearch()}
              disabled={busySearch}
              className="flex min-w-12 items-center justify-center rounded-r-xl bg-[#173f38] text-white hover:bg-[#0f766e] disabled:opacity-50"
              aria-label="Rechercher"
            >
              <Search size={17} />
            </button>
          </div>
        </div>
      </section>

      {(notice || error) && (
        <div
          className={`flex items-center justify-between rounded-xl px-4 py-3 text-sm ${
            error
              ? "bg-rose-50 text-rose-700"
              : "bg-emerald-50 text-emerald-700"
          }`}
        >
          <span className="flex items-center gap-2">
            {error ? (
              <AlertTriangle size={16} />
            ) : (
              <CheckCircle2 size={16} />
            )}
            {error || notice}
          </span>
          <button
            type="button"
            onClick={() => {
              setError("");
              setNotice("");
            }}
            aria-label="Fermer"
          >
            <X size={15} />
          </button>
        </div>
      )}

      <div className="grid min-h-[720px] gap-4 xl:h-[calc(100vh-220px)] xl:grid-cols-[330px_minmax(0,1fr)]">
        <aside className="surface order-2 flex min-h-0 flex-col overflow-hidden xl:order-none">
          <div className="grid grid-cols-3 border-b border-[#e1e8e5] p-2">
            {[
              ["layers", Layers, "Couches"],
              ["analysis", Sparkles, "IA"],
              ["search", Search, "Objets"],
            ].map(([id, Icon, label]) => (
              <button
                type="button"
                key={String(id)}
                onClick={() => setActivePanel(id as WorkspacePanel)}
                className={`flex min-h-10 items-center justify-center gap-1.5 rounded-lg text-xs font-semibold ${
                  activePanel === id
                    ? "bg-[#e8f5f1] text-[#0f766e]"
                    : "text-[#76827e] hover:bg-[#f3f6f5]"
                }`}
              >
                <Icon size={14} /> {String(label)}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {activePanel === "layers" && (
              <LayersPanel
                missionId={missionId}
                activeLayer={activeLayer}
                hasDepth={hasDepth}
                rasterOpacity={rasterOpacity}
                showLegacy={showLegacy}
                showManual={showManual}
                analyses={analyses}
                visibleRuns={visibleRuns}
                onLayerChange={setActiveLayer}
                onOpacityChange={setRasterOpacity}
                onLegacyChange={setShowLegacy}
                onManualChange={setShowManual}
                onRunVisibilityChange={(runId, visible) =>
                  setVisibleRuns((current) =>
                    visible
                      ? [...current, runId]
                      : current.filter((id) => id !== runId),
                  )
                }
              />
            )}
            {activePanel === "analysis" && (
              <AnalysisPanel
                analyses={analyses}
                form={analysisForm}
                formVisible={showAnalysisForm}
                submitting={submittingAnalysis}
                onFormChange={setAnalysisForm}
                onFormVisibilityChange={setShowAnalysisForm}
                onSubmit={() => void submitAnalysis()}
                onRetry={(runId) =>
                  void retryAnalysis(missionId, runId).then(
                    refreshAnalyses,
                  )
                }
                onCancel={(runId) =>
                  void cancelAnalysis(missionId, runId).then(
                    refreshAnalyses,
                  )
                }
              />
            )}
            {activePanel === "search" && (
              <SearchPanel
                source={searchSource}
                runId={searchRun}
                analyses={analyses}
                results={searchResults}
                onSourceChange={setSearchSource}
                onRunChange={setSearchRun}
                onSearch={() => void runSearch()}
                onFeatureSelect={setSelectedFeature}
                onFocus={setFocusBounds}
              />
            )}
          </div>
        </aside>

        <main className="relative order-1 min-h-[620px] overflow-hidden rounded-[1.25rem] border border-[#263632] bg-[#16201d] shadow-[0_20px_50px_rgba(20,32,28,0.12)] xl:order-none">
          <div className="absolute left-3 top-3 z-[500] flex max-w-[calc(100%-24px)] flex-wrap gap-1.5 rounded-2xl border border-white/40 bg-white/92 p-2 shadow-lg backdrop-blur">
            {TOOL_BUTTONS.map(({ id, label, icon: Icon }) => (
              <button
                type="button"
                key={id}
                title={label}
                onClick={() => setTool(id)}
                className={`flex min-h-9 items-center gap-1.5 rounded-xl px-2.5 text-xs font-semibold ${
                  tool === id
                    ? "bg-[#173f38] text-white"
                    : "text-[#53615d] hover:bg-[#edf3f1]"
                }`}
              >
                <Icon size={14} />
                <span className="hidden 2xl:inline">{label}</span>
              </button>
            ))}
          </div>
          {toolHint && (
            <div className="absolute left-1/2 top-16 z-[500] -translate-x-1/2 rounded-full bg-[#17201e]/85 px-4 py-2 text-center text-xs text-white shadow backdrop-blur">
              {toolHint}
            </div>
          )}
          <GeospatialMap
            key={`${missionId}:${activeLayer}`}
            missionId={missionId}
            layer={activeLayer}
            rasterOpacity={rasterOpacity}
            showLegacy={showLegacy}
            showManual={showManual}
            analyses={visibleAnalyses}
            tool={tool}
            focusBounds={focusBounds}
            refreshToken={refreshToken}
            onGeometryReady={geometryReady}
            onFeatureSelect={setSelectedFeature}
            onHint={setToolHint}
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
          {selectedFeature && !draftGeometry && (
            <SelectedFeatureEditor
              feature={selectedFeature}
              onChange={setSelectedFeature}
              onClose={() => setSelectedFeature(null)}
              onDelete={() => void removeSelected()}
              onSave={() => void saveSelected()}
            />
          )}
        </main>
      </div>
    </div>
  );
}
