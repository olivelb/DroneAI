import type { Geometry } from "geojson";
import {
  CircleDot,
  Hand,
  MousePointer2,
  Pentagon,
  Route,
  Ruler,
} from "lucide-react";
import type { AnalysisCreate, MissionProduct } from "../../lib/types";
import type { MessageKey } from "../../lib/i18n/catalog";
import type { MapTool } from "../GeospatialMap";

export type ViewerLayer = "ortho" | "depth";

export const geometryTool = (geometry?: Geometry): MapTool => {
  if (geometry?.type === "Point" || geometry?.type === "MultiPoint") return "point";
  if (geometry?.type === "LineString" || geometry?.type === "MultiLineString") {
    return "line";
  }
  return "polygon";
};

export const retainKnownRunIds = (
  visibleRunIds: string[],
  knownRunIds: string[],
): string[] => {
  const known = new Set(knownRunIds);
  return visibleRunIds.filter((runId) => known.has(runId));
};

/** Mission detail returns artifacts in publication order, oldest first. */
export const rasterProductFiles = (products: MissionProduct[] = []): string[] => {
  const raster = products
    .filter((product) => product.kind === "raster_product_workspace" && product.artifact_id)
    .at(-1);
  return [raster?.metadata?.ortho_file, raster?.metadata?.height_file]
    .filter((path): path is string => typeof path === "string" && path.length > 0);
};

export type WorkspacePanel = "layers" | "gcp" | "analysis" | "search" | "export";

export const DEFAULT_ANALYSIS: AnalysisCreate = {
  name: "Vehicle detection",
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
  labelKey: MessageKey;
  icon: typeof MousePointer2;
}> = [
  { id: "select", labelKey: "toolbar.select", icon: MousePointer2 },
  { id: "navigate", labelKey: "toolbar.navigate", icon: Hand },
  { id: "point", labelKey: "toolbar.point", icon: CircleDot },
  { id: "line", labelKey: "toolbar.line", icon: Route },
  { id: "polygon", labelKey: "toolbar.polygon", icon: Pentagon },
  { id: "measure-distance", labelKey: "toolbar.distance", icon: Ruler },
  { id: "measure-area", labelKey: "toolbar.area", icon: Pentagon },
];

export const TOOL_SHORTCUTS: Record<MapTool, string> = {
  select: "V",
  navigate: "H",
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
