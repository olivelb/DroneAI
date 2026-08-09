"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import L, {
  latLng,
  type LatLngBoundsExpression,
  type PathOptions,
} from "leaflet";
import {
  CircleMarker,
  GeoJSON,
  MapContainer,
  Polygon,
  Polyline,
  ScaleControl,
  TileLayer,
  Tooltip,
  ZoomControl,
  useMap,
  useMapEvents,
} from "react-leaflet";
import type {
  Feature,
  FeatureCollection,
  Geometry,
  LineString,
  Point,
  Polygon as GeoJSONPolygon,
} from "geojson";
import {
  getAnalysisVectors,
  getMapMetadata,
  getMapTileUrl,
  getVectorLayer,
} from "../lib/api";
import type { AnalysisRun } from "../lib/types";

export type MapTool =
  | "navigate"
  | "point"
  | "line"
  | "polygon"
  | "measure-distance"
  | "measure-area";

type RasterMetadata = {
  bounds: { wgs84: [number, number, number, number] };
  min_zoom: number;
  max_zoom: number;
  display_ranges?: Array<[number, number] | null>;
};

const escapeHtml = (value: unknown) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

function FitBounds({
  bounds,
  padding = 18,
}: {
  bounds: LatLngBoundsExpression;
  padding?: number;
}) {
  const map = useMap();
  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (
        !map.getContainer().isConnected ||
        !map.getPane("mapPane")?.isConnected
      ) {
        return;
      }
      map.invalidateSize();
      map.fitBounds(bounds, { padding: [padding, padding], maxZoom: 21 });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [bounds, map, padding]);
  return null;
}

function ResizeController() {
  const map = useMap();
  useEffect(() => {
    const container = map.getContainer();
    const observer = new ResizeObserver(() => map.invalidateSize(false));
    observer.observe(container);
    return () => observer.disconnect();
  }, [map]);
  return null;
}

const distanceMetres = (points: [number, number][]) =>
  points.slice(1).reduce(
    (total, point, index) =>
      total + latLng(points[index]).distanceTo(latLng(point)),
    0,
  );

const areaSquareMetres = (points: [number, number][]) => {
  if (points.length < 3) return 0;
  const radius = 6378137;
  let area = 0;
  for (let index = 0; index < points.length; index += 1) {
    const [lat1, lon1] = points[index];
    const [lat2, lon2] = points[(index + 1) % points.length];
    area +=
      ((lon2 - lon1) * Math.PI) /
      180 *
      (2 + Math.sin((lat1 * Math.PI) / 180) + Math.sin((lat2 * Math.PI) / 180));
  }
  return Math.abs((area * radius * radius) / 2);
};

const formatMeasurement = (tool: MapTool, points: [number, number][]) => {
  if (tool === "measure-area") {
    const area = areaSquareMetres(points);
    return area >= 10_000
      ? `${(area / 10_000).toFixed(2)} ha`
      : `${area.toFixed(1)} m²`;
  }
  const distance = distanceMetres(points);
  return distance >= 1_000
    ? `${(distance / 1_000).toFixed(2)} km`
    : `${distance.toFixed(1)} m`;
};

function DrawController({
  tool,
  onGeometryReady,
  onHint,
}: {
  tool: MapTool;
  onGeometryReady: (geometry: Geometry, measurement?: string) => void;
  onHint: (hint: string) => void;
}) {
  const [points, setPoints] = useState<[number, number][]>([]);

  useEffect(() => {
    onHint(
      tool === "navigate"
        ? ""
        : tool === "point"
          ? "Cliquez pour placer le point"
          : "Cliquez pour ajouter des sommets · Entrée ou double-clic pour terminer · Retour pour annuler le dernier sommet",
    );
  }, [onHint, tool]);

  const finish = useCallback(
    (input: [number, number][]) => {
      const compact = input.filter(
        (point, index) =>
          index === 0 ||
          point[0] !== input[index - 1][0] ||
          point[1] !== input[index - 1][1],
      );
      const isMeasured = tool.startsWith("measure-");
      const isLine = tool === "line" || tool === "measure-distance";
      const isPolygon =
        tool === "polygon" || tool === "measure-area";
      if (isLine && compact.length >= 2) {
        const geometry: LineString = {
          type: "LineString",
          coordinates: compact.map(([latitude, longitude]) => [
            longitude,
            latitude,
          ]),
        };
        onGeometryReady(
          geometry,
          isMeasured ? formatMeasurement(tool, compact) : undefined,
        );
      } else if (isPolygon && compact.length >= 3) {
        const ring = compact.map(([latitude, longitude]) => [
          longitude,
          latitude,
        ]);
        ring.push([...ring[0]]);
        const geometry: GeoJSONPolygon = {
          type: "Polygon",
          coordinates: [ring],
        };
        onGeometryReady(
          geometry,
          isMeasured ? formatMeasurement(tool, compact) : undefined,
        );
      }
      setPoints([]);
    },
    [onGeometryReady, tool],
  );

  useEffect(() => {
    const handleKeyboard = (event: KeyboardEvent) => {
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
      ) {
        return;
      }
      if (event.key === "Escape") {
        setPoints([]);
      } else if (
        (event.key === "Backspace" || event.key === "Delete") &&
        points.length > 0
      ) {
        event.preventDefault();
        setPoints((current) => current.slice(0, -1));
      } else if (event.key === "Enter" && points.length > 0) {
        event.preventDefault();
        finish(points);
      }
    };
    window.addEventListener("keydown", handleKeyboard);
    return () => window.removeEventListener("keydown", handleKeyboard);
  }, [finish, points]);

  useMapEvents({
    click: (event) => {
      if (tool === "navigate") return;
      const point: [number, number] = [event.latlng.lat, event.latlng.lng];
      if (tool === "point") {
        const geometry: Point = {
          type: "Point",
          coordinates: [event.latlng.lng, event.latlng.lat],
        };
        onGeometryReady(geometry);
        return;
      }
      setPoints((current) => [...current, point]);
    },
    dblclick: (event) => {
      if (
        tool === "line" ||
        tool === "polygon" ||
        tool === "measure-distance" ||
        tool === "measure-area"
      ) {
        L.DomEvent.preventDefault(event.originalEvent);
        finish([...points, [event.latlng.lat, event.latlng.lng]]);
      }
    },
  });

  if (!points.length) return null;
  const measurement =
    tool.startsWith("measure") ? formatMeasurement(tool, points) : "";
  return (
    <>
      {(tool === "polygon" || tool === "measure-area") && points.length >= 3 ? (
        <Polygon
          positions={points}
          pathOptions={{ color: "#f59e0b", fillOpacity: 0.16, dashArray: "7 5" }}
        >
          {measurement && <Tooltip permanent>{measurement}</Tooltip>}
        </Polygon>
      ) : (
        <Polyline
          positions={points}
          pathOptions={{ color: "#f59e0b", weight: 3, dashArray: "7 5" }}
        >
          {measurement && <Tooltip permanent>{measurement}</Tooltip>}
        </Polyline>
      )}
      {points.map((point, index) => (
        <CircleMarker
          key={`${point.join(":")}:${index}`}
          center={point}
          radius={4}
          pathOptions={{ color: "#fff", fillColor: "#f59e0b", fillOpacity: 1 }}
        />
      ))}
    </>
  );
}

function ViewportVectors({
  missionId,
  showLegacy,
  showManual,
  analyses,
  refreshToken,
  onFeatureSelect,
}: {
  missionId: string;
  showLegacy: boolean;
  showManual: boolean;
  analyses: AnalysisRun[];
  refreshToken: number;
  onFeatureSelect: (feature: Feature) => void;
}) {
  const [vectors, setVectors] = useState<FeatureCollection[]>([]);
  const load = useCallback(
    async (west: number, south: number, east: number, north: number) => {
      const bbox: [number, number, number, number] = [
        west,
        south,
        east,
        north,
      ];
      const requests: Promise<FeatureCollection>[] = [];
      if (showLegacy || showManual) {
        requests.push(
          getVectorLayer(missionId, bbox, {
            sources: [
              ...(showLegacy ? ["legacy"] : []),
              ...(showManual ? ["manual"] : []),
            ],
          }),
        );
      }
      requests.push(
        ...analyses.map((run) =>
          getAnalysisVectors(missionId, run.run_id, bbox),
        ),
      );
      const results = await Promise.allSettled(requests);
      setVectors(
        results.flatMap((result) =>
          result.status === "fulfilled" ? [result.value] : [],
        ),
      );
    },
    [analyses, missionId, showLegacy, showManual],
  );
  const map = useMapEvents({
    moveend: () => {
      const bounds = map.getBounds();
      void load(
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
      );
    },
  });
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const bounds = map.getBounds();
      void load(
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load, map, refreshToken]);

  const style = useCallback(
    (feature?: Feature): PathOptions => {
      const color = String(feature?.properties?.color || "#f43f5e");
      return {
        color,
        fillColor: color,
        fillOpacity: 0.26,
        opacity: 0.95,
        weight: 2.2,
      };
    },
    [],
  );

  return (
    <>
      {vectors.map((collection, index) => (
        <GeoJSON
          key={`${index}:${refreshToken}:${collection.features.length}`}
          data={collection}
          style={style}
          pointToLayer={(feature, point) =>
            L.circleMarker(point, {
              ...style(feature),
              radius: 6,
              fillOpacity: 0.8,
            })
          }
          onEachFeature={(feature, layer) => {
            const properties = feature.properties ?? {};
            const confidence =
              properties.confidence === null ||
              properties.confidence === undefined
                ? ""
                : `<br/>Confiance : ${Math.round(Number(properties.confidence) * 100)} %`;
            layer.bindPopup(
              `<strong>${escapeHtml(properties.name || properties.class_name || "Objet")}</strong>` +
                (properties.description
                  ? `<br/>${escapeHtml(properties.description)}`
                  : "") +
                confidence,
            );
            layer.on("click", () => onFeatureSelect(feature));
          }}
        />
      ))}
    </>
  );
}

export default function GeospatialMap({
  missionId,
  layer,
  rasterOpacity,
  showLegacy,
  showManual,
  analyses,
  tool,
  focusBounds,
  refreshToken,
  onGeometryReady,
  onFeatureSelect,
  onHint,
}: {
  missionId: string;
  layer: "ortho" | "depth";
  rasterOpacity: number;
  showLegacy: boolean;
  showManual: boolean;
  analyses: AnalysisRun[];
  tool: MapTool;
  focusBounds: [number, number, number, number] | null;
  refreshToken: number;
  onGeometryReady: (geometry: Geometry, measurement?: string) => void;
  onFeatureSelect: (feature: Feature) => void;
  onHint: (hint: string) => void;
}) {
  const [metadata, setMetadata] = useState<RasterMetadata | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    getMapMetadata(missionId, layer)
      .then((result) => active && setMetadata(result as RasterMetadata))
      .catch((reason: Error) => active && setError(reason.message));
    return () => {
      active = false;
    };
  }, [layer, missionId]);

  const bounds = useMemo<LatLngBoundsExpression | null>(() => {
    if (!metadata) return null;
    const [west, south, east, north] = metadata.bounds.wgs84;
    return [
      [south, west],
      [north, east],
    ];
  }, [metadata]);
  const searchBounds = useMemo<LatLngBoundsExpression | null>(
    () =>
      focusBounds
        ? [
            [focusBounds[1], focusBounds[0]],
            [focusBounds[3], focusBounds[2]],
          ]
        : null,
    [focusBounds],
  );
  const displayRange =
    layer === "depth" ? metadata?.display_ranges?.[0] ?? undefined : undefined;

  if (error) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-gray-300">
        Carte indisponible : {error}
      </div>
    );
  }
  if (!metadata || !bounds) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-600 border-t-emerald-400" />
      </div>
    );
  }

  return (
    <MapContainer
      bounds={bounds}
      minZoom={Math.max(0, metadata.min_zoom - 2)}
      maxZoom={Math.min(24, metadata.max_zoom + 2)}
      className={`h-full w-full ${tool === "navigate" ? "" : "map-crosshair"}`}
      preferCanvas
      doubleClickZoom={false}
      zoomControl={false}
    >
      <ResizeController />
      <FitBounds bounds={bounds} />
      {searchBounds && <FitBounds bounds={searchBounds} padding={42} />}
      <TileLayer
        key={`${missionId}:${layer}:${displayRange?.join(":") ?? "default"}`}
        url={getMapTileUrl(missionId, layer, displayRange)}
        minZoom={metadata.min_zoom}
        maxNativeZoom={metadata.max_zoom}
        maxZoom={Math.min(24, metadata.max_zoom + 2)}
        opacity={rasterOpacity}
        attribution="DroneAI COG"
      />
      <ScaleControl imperial={false} position="bottomleft" />
      <ZoomControl position="bottomright" />
      <ViewportVectors
        missionId={missionId}
        showLegacy={showLegacy}
        showManual={showManual}
        analyses={analyses}
        refreshToken={refreshToken}
        onFeatureSelect={onFeatureSelect}
      />
      <DrawController
        key={tool}
        tool={tool}
        onGeometryReady={onGeometryReady}
        onHint={onHint}
      />
    </MapContainer>
  );
}
