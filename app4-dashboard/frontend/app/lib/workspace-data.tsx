"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { fetchBrowse } from "./api";
import { useAuth } from "./auth";
import type { DatasetItem } from "./types";

const DEFAULT_BROWSER_PATH = "datasets/";

type WorkspaceDataState = {
  currentPath: string;
  items: DatasetItem[];
  browse: (path: string) => Promise<void>;
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

  useEffect(() => {
    if (authStatus !== "authenticated") return;
    const initialLoad = window.setTimeout(() => {
      void browse(DEFAULT_BROWSER_PATH);
    }, 0);
    return () => {
      window.clearTimeout(initialLoad);
    };
  }, [authStatus, browse]);

  const value: WorkspaceDataState = {
    currentPath,
    items,
    browse,
  };

  return (
    <WorkspaceDataContext.Provider value={value}>
      {children}
    </WorkspaceDataContext.Provider>
  );
}
