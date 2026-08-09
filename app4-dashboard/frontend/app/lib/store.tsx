"use client";

import React, { createContext, useCallback, useContext, useEffect, useId, useState } from "react";
import type {
  AIBackend, ParameterConfigResponse,
  ParamValue, PipelineName, QualityProfileId, YOLOModelVariant,
  PhaseId,
} from "./types";
import { fetchParameters } from "./api";
import { useAuth } from "./auth";
import {
  initialParameterValues,
  qualityProfileParameters,
  useQualityProfileState,
} from "./quality-profile-state";

type StoreState = {
  // Mission input selection
  selectedPath: string;
  setSelectedPath: (path: string) => void;

  // Mission draft
  volId: string;
  setVolId: (id: string) => void;

  // Pipeline params
  pipeline: PipelineName;
  setPipeline: (p: PipelineName) => void;
  parameterSchema: ParameterConfigResponse | null;
  parameterValues: Record<string, ParamValue>;
  setParameterValues: React.Dispatch<React.SetStateAction<Record<string, ParamValue>>>;
  updateParameter: (key: string, value: ParamValue) => void;
  qualityProfileId: QualityProfileId;
  setQualityProfile: (profileId: QualityProfileId) => void;
  workDrive: string;
  setWorkDrive: (d: string) => void;

  // AI config
  aiConfidence: number;
  setAiConfidence: (c: number) => void;
  aiBackend: AIBackend;
  setAiBackend: (b: AIBackend) => void;
  aiModelVariant: YOLOModelVariant;
  setAiModelVariant: (m: YOLOModelVariant) => void;
  samPrompt: string;
  setSamPrompt: (p: string) => void;
  selectedClasses: string[];
  setSelectedClasses: (c: string[]) => void;
  tileSize: number;
  setTileSize: (size: number) => void;

  // Upload
  uploadDatasetName: string;
  setUploadDatasetName: (n: string) => void;
  uploadFiles: FileList | null;
  setUploadFiles: (f: FileList | null) => void;
  uploadProgress: { total: number; completed: number; failed: number; status: string } | null;
  setUploadProgress: React.Dispatch<React.SetStateAction<{ total: number; completed: number; failed: number; status: string } | null>>;
  isUploading: boolean;
  setIsUploading: (u: boolean) => void;

  // Active tab (phase)
  activePhase: PhaseId;
  setActivePhase: (phase: PhaseId) => void;

};

const StoreContext = createContext<StoreState | null>(null);

export function useStore() {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used within StoreProvider");
  return ctx;
}

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const { authStatus } = useAuth();
  const generatedMissionId = useId().replace(/[^A-Za-z0-9]/g, "") || "new";
  const [selectedPath, setSelectedPath] = useState("");
  const [volId, setVolId] = useState(`mission-${generatedMissionId}`);
  const [activePhase, setActivePhase] = useState<PhaseId>("setup");

  const [aiConfidence, setAiConfidence] = useState(0.5);
  const [aiBackend, setAiBackend] = useState<AIBackend>("yolo");
  const [aiModelVariant, setAiModelVariant] = useState<YOLOModelVariant>("yolo26l");
  const [samPrompt, setSamPrompt] = useState("car");
  const [selectedClasses, setSelectedClasses] = useState<string[]>(["car"]);
  const [tileSize, setTileSize] = useState(1024);

  const [pipeline, setPipelineRaw] = useState<PipelineName>("modern");
  const [parameterSchema, setParameterSchema] = useState<ParameterConfigResponse | null>(null);
  const [parameterValues, setParameterValues] = useState<Record<string, ParamValue>>({});
  const { qualityProfileId, setQualityProfile, synchronizeQualityProfile } =
    useQualityProfileState(parameterSchema, setParameterValues);
  const [workDrive, setWorkDrive] = useState<string>("");

  const [uploadDatasetName, setUploadDatasetName] = useState("");
  const [uploadFiles, setUploadFiles] = useState<FileList | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{ total: number; completed: number; failed: number; status: string } | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const loadParameters = useCallback(async () => {
    try {
      const data = (await fetchParameters()) as ParameterConfigResponse;
      setParameterSchema(data);
      setAiModelVariant((current) =>
        data.yolo_models.some((model) => model.id === current && model.available)
          ? current
          : data.yolo_models.find((model) => model.available)?.id ?? current,
      );
      setParameterValues((current) =>
        Object.keys(current).length > 0
          ? current
          : initialParameterValues(data),
      );
      synchronizeQualityProfile(data);
      setWorkDrive((current) => {
        const names = new Set((data.work_drives ?? []).map((drive) => drive.name));
        if (current && names.has(current)) return current;
        if (data.work_drive_default && names.has(data.work_drive_default)) {
          return data.work_drive_default;
        }
        return data.work_drives?.[0]?.name ?? "";
      });
    } catch (e) {
      // Never leave a disappeared disk selectable after a redeploy or API
      // outage. Keep the pipeline schema and the operator's edited values.
      setParameterSchema((current) =>
        current
          ? { ...current, work_drives: [], work_drive_default: "" }
          : current,
      );
      setWorkDrive("");
      console.error("Param error:", e);
    }
  }, [synchronizeQualityProfile]);

  const setPipeline = useCallback((p: PipelineName) => {
    setPipelineRaw(p);
    if (parameterSchema) {
      setParameterValues((current) => {
        const processId = current.orthophoto_mode === "facade" ? "facade" : "map";
        const process = parameterSchema.processes.find(
          (candidate) => candidate.id === processId,
        );
        return {
          ...(parameterSchema.pipelines[p] ?? {}),
          ...(process?.parameters ?? { orthophoto_mode: processId }),
          ...qualityProfileParameters(parameterSchema, qualityProfileId),
        };
      });
    }
  }, [parameterSchema, qualityProfileId]);

  const updateParameter = useCallback((key: string, value: ParamValue) => {
    setParameterValues((prev) => ({ ...prev, [key]: value }));
  }, []);

  // Load protected data only after an authenticated cookie session exists.
  useEffect(() => {
    if (authStatus !== "authenticated") return;
    const initialLoad = window.setTimeout(() => {
      void loadParameters();
    }, 0);
    const wi = setInterval(() => void loadParameters(), 15000);
    return () => {
      window.clearTimeout(initialLoad);
      clearInterval(wi);
    };
  }, [
    authStatus,
    loadParameters,
  ]);

  const value: StoreState = {
    selectedPath, setSelectedPath,
    volId, setVolId,
    pipeline, setPipeline, parameterSchema, parameterValues, setParameterValues, updateParameter,
    qualityProfileId, setQualityProfile,
    workDrive, setWorkDrive,
    aiConfidence, setAiConfidence, aiBackend, setAiBackend, aiModelVariant, setAiModelVariant,
    samPrompt, setSamPrompt, selectedClasses, setSelectedClasses, tileSize, setTileSize,
    uploadDatasetName, setUploadDatasetName, uploadFiles, setUploadFiles,
    uploadProgress, setUploadProgress, isUploading, setIsUploading,
    activePhase, setActivePhase,
  };

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}
