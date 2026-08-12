"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { fetchBrowse, fetchPods } from "./api";
import { useAuth } from "./auth";
import type { DatasetItem, PodState } from "./types";

const DEFAULT_BROWSER_PATH = "datasets/";

type WorkspaceDataState = {
  currentPath: string;
  items: DatasetItem[];
  browse: (path: string) => Promise<void>;
  pods: PodState[];
  podsError: string | null;
};

const WorkspaceDataContext = createContext<WorkspaceDataState | null>(null);

export function useWorkspaceData() {
  const context = useContext(WorkspaceDataContext);
  if (!context) {
    throw new Error("useWorkspaceData must be used within WorkspaceDataProvider");
  }
  return context;
}

export function WorkspaceDataProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const { authStatus } = useAuth();
  const [currentPath, setCurrentPath] = useState(DEFAULT_BROWSER_PATH);
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [pods, setPods] = useState<PodState[]>([]);
  const [podsError, setPodsError] = useState<string | null>(null);

  const browse = useCallback(async (path: string) => {
    try {
      const data = await fetchBrowse(path);
      if (!Array.isArray(data)) return;
      setItems(data);
      setCurrentPath(path);
    } catch (error) {
      console.error("Browse error:", error);
    }
  }, []);

  const refreshPods = useCallback(async () => {
    try {
      const data = await fetchPods();
      setPods(data.pods ?? []);
      setPodsError(data.error ?? null);
    } catch (error) {
      setPodsError(String(error));
    }
  }, []);

  useEffect(() => {
    if (authStatus !== "authenticated") return;
    const initialLoad = window.setTimeout(() => {
      void browse(DEFAULT_BROWSER_PATH);
      void refreshPods();
    }, 0);
    const interval = window.setInterval(() => void refreshPods(), 10000);
    return () => {
      window.clearTimeout(initialLoad);
      window.clearInterval(interval);
    };
  }, [authStatus, browse, refreshPods]);

  const value: WorkspaceDataState = {
    currentPath,
    items,
    browse,
    pods,
    podsError,
  };

  return (
    <WorkspaceDataContext.Provider value={value}>
      {children}
    </WorkspaceDataContext.Provider>
  );
}
