import type { Geometry } from "geojson";
import {
  CircleDot,
  MousePointer2,
  Pentagon,
  Route,
  Ruler,
} from "lucide-react";
import type { AnalysisCreate } from "../../lib/types";
import type { MapTool } from "../GeospatialMap";

export type ViewerLayer = "ortho" | "depth";

export const retainKnownRunIds = (
  visibleRunIds: string[],
  knownRunIds: string[],
): string[] => {
  const known = new Set(knownRunIds);
  return visibleRunIds.filter((runId) => known.has(runId));
};

export type WorkspacePanel = "layers" | "analysis" | "search" | "export";

export const DEFAULT_ANALYSIS: AnalysisCreate = {
  name: "Détection véhicules",
  description: "",
  color: "#f43f5e",
  tags: ["IA"],
  backend: "yolo",
  model_variant: "yolo26l",
  prompt: "car",
  classes: ["car"],
  confidence: 0.3,
  tile_size: 1024,
  persist_results: true,
};

export const TOOL_BUTTONS: Array<{
  id: MapTool;
  label: string;
  icon: typeof MousePointer2;
}> = [
  { id: "navigate", label: "Naviguer", icon: MousePointer2 },
  { id: "point", label: "Point", icon: CircleDot },
  { id: "line", label: "Ligne", icon: Route },
  { id: "polygon", label: "Polygone", icon: Pentagon },
  { id: "measure-distance", label: "Distance", icon: Ruler },
  { id: "measure-area", label: "Surface", icon: Pentagon },
];

export const TOOL_SHORTCUTS: Record<MapTool, string> = {
  navigate: "V",
  point: "P",
  line: "L",
  polygon: "G",
  "measure-distance": "D",
  "measure-area": "A",
};

export const statusTone = (status: string) => {
  if (status === "completed") return "bg-emerald-50 text-emerald-700";
  if (status === "failed") return "bg-rose-50 text-rose-700";
  if (status === "cancelled") return "bg-slate-100 text-slate-500";
  return "bg-amber-50 text-amber-700";
};

export const splitTags = (value: string) =>
  value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);

export const geometryBounds = (
  geometry: Geometry,
): [number, number, number, number] => {
  const points: [number, number][] = [];
  const walk = (node: unknown) => {
    if (
      Array.isArray(node) &&
      node.length >= 2 &&
      typeof node[0] === "number" &&
      typeof node[1] === "number"
    ) {
      points.push([node[0], node[1]]);
      return;
    }
    if (Array.isArray(node)) node.forEach(walk);
    else if (node && typeof node === "object") {
      if ("coordinates" in node) walk(node.coordinates);
      if ("geometries" in node) walk(node.geometries);
    }
  };
  walk(geometry);
  if (points.length === 0) {
    throw new Error("Geometry has no coordinate positions");
  }
  return [
    Math.min(...points.map((point) => point[0])),
    Math.min(...points.map((point) => point[1])),
    Math.max(...points.map((point) => point[0])),
    Math.max(...points.map((point) => point[1])),
  ];
};
