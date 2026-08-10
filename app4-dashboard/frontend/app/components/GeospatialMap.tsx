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
import { useI18n } from "../lib/i18n/provider";
import type {
  AnalysisRun,
  GcpCollection,
  GcpFeature,
  RasterMetadata,
  RasterStyleRecipe,
} from "../lib/types";

export type MapTool =
  | "select"
  | "navigate"
  | "point"
  | "line"
  | "polygon"
  | "measure-distance"
  | "measure-area";

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

function InteractionController({ tool }: { tool: MapTool }) {
  const map = useMap();
  useEffect(() => {
    if (tool === "navigate") map.dragging.enable();
    else map.dragging.disable();
    return () => {
      map.dragging.enable();
    };
  }, [map, tool]);
  return null;
}

function CoordinateReadout() {
  const map = useMap();
  useEffect(() => {
    const control = new L.Control({ position: "bottomleft" });
    const container = L.DomUtil.create("div", "droneai-coordinate-control");
    container.textContent = "EPSG:4326  ·  X —  ·  Y —";
    control.onAdd = () => container;
    control.addTo(map);
    const update = (event: L.LeafletMouseEvent) => {
      container.textContent = `EPSG:4326  ·  X ${event.latlng.lng.toFixed(7)}  ·  Y ${event.latlng.lat.toFixed(7)}`;
    };
    map.on("mousemove", update);
    return () => {
      map.off("mousemove", update);
      control.remove();
    };
  }, [map]);
  return null;
}

function SelectionController({
  tool,
  onFeatureClear,
}: {
  tool: MapTool;
  onFeatureClear: () => void;
}) {
  useMapEvents({
    click: () => {
      if (tool === "select") onFeatureClear();
    },
  });
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
  const { t } = useI18n();
  const [points, setPoints] = useState<[number, number][]>([]);

  useEffect(() => {
    onHint(
      tool === "navigate"
        ? ""
        : tool === "point"
          ? t("map.placePoint")
          : t("map.addVertices"),
    );
  }, [onHint, t, tool]);

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
      if (tool === "navigate" || tool === "select") return;
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
  tool,
  selectedFeatureId,
  onFeatureSelect,
}: {
  missionId: string;
  showLegacy: boolean;
  showManual: boolean;
  analyses: AnalysisRun[];
  refreshToken: number;
  tool: MapTool;
  selectedFeatureId: string;
  onFeatureSelect: (feature: Feature) => void;
}) {
  const { t } = useI18n();
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
      const featureId = String(
        feature?.properties?.feature_id ?? feature?.id ?? "",
      );
      const selected = Boolean(
        selectedFeatureId && featureId === selectedFeatureId,
      );
      return {
        color,
        fillColor: color,
        fillOpacity: selected ? 0.42 : 0.26,
        opacity: 0.95,
        weight: selected ? 4 : 2.2,
        dashArray: selected ? "8 4" : undefined,
      };
    },
    [selectedFeatureId],
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
              radius:
                String(feature.properties?.feature_id ?? feature.id ?? "") ===
                selectedFeatureId
                  ? 9
                  : 6,
              fillOpacity: 0.8,
            })
          }
          onEachFeature={(feature, layer) => {
            const properties = feature.properties ?? {};
            const confidence =
              properties.confidence === null ||
              properties.confidence === undefined
                ? ""
                : `<br/>${t("map.confidence")} : ${Math.round(Number(properties.confidence) * 100)} %`;
            layer.bindPopup(
              `<strong>${escapeHtml(properties.name || properties.class_name || t("search.object"))}</strong>` +
                (properties.description
                  ? `<br/>${escapeHtml(properties.description)}`
                  : "") +
                confidence,
            );
            layer.on("click", (event) => {
              L.DomEvent.stopPropagation(event);
              if (tool === "select") onFeatureSelect(feature);
            });
          }}
        />
      ))}
    </>
  );
}

function GroundControlVectors({
  collection,
  tool,
  selectedPointId,
  onPointSelect,
}: {
  collection: GcpCollection;
  tool: MapTool;
  selectedPointId: string;
  onPointSelect: (point: GcpFeature) => void;
}) {
  const { t } = useI18n();
  const style = useCallback(
    (feature?: Feature): PathOptions => {
      const role = feature?.properties?.role;
      const color =
        role === "checkpoint" ? "#f59e0b" : role === "disabled" ? "#94a3b8" : "#0ea5e9";
      const pointId = String(feature?.properties?.point_id ?? feature?.id ?? "");
      const selected = pointId === selectedPointId;
      return {
        color: selected ? "#ffffff" : color,
        fillColor: color,
        fillOpacity: role === "disabled" ? 0.35 : 0.9,
        opacity: 1,
        weight: selected ? 4 : 2,
      };
    },
    [selectedPointId],
  );
  return (
    <GeoJSON
      key={`${collection.features.length}:${selectedPointId}`}
      data={collection}
      style={style}
      pointToLayer={(feature, point) =>
        L.circleMarker(point, {
          ...style(feature),
          radius:
            String(feature.properties?.point_id ?? feature.id ?? "") === selectedPointId
              ? 10
              : 7,
        })
      }
      onEachFeature={(feature, layer) => {
        const properties = feature.properties ?? {};
        layer.bindTooltip(
          `<strong>${escapeHtml(properties.external_id)}</strong><br/>${escapeHtml(t("gcp.role"))}: ${escapeHtml(properties.role)}`,
          { direction: "top" },
        );
        layer.on("click", (event) => {
          L.DomEvent.stopPropagation(event);
          if (tool === "select") onPointSelect(feature as GcpFeature);
        });
      }}
    />
  );
}

export default function GeospatialMap({
  missionId,
  layer,
  rasterStyle,
  showLegacy,
  showManual,
  showGcps,
  gcpCollection,
  analyses,
  tool,
  focusBounds,
  refreshToken,
  selectedFeatureId,
  selectedGcpId,
  onGeometryReady,
  onFeatureSelect,
  onGcpSelect,
  onFeatureClear,
  onHint,
  onMetadata,
}: {
  missionId: string;
  layer: "ortho" | "depth";
  rasterStyle: RasterStyleRecipe;
  showLegacy: boolean;
  showManual: boolean;
  showGcps: boolean;
  gcpCollection: GcpCollection | null;
  analyses: AnalysisRun[];
  tool: MapTool;
  focusBounds: [number, number, number, number] | null;
  refreshToken: number;
  selectedFeatureId: string;
  selectedGcpId: string;
  onGeometryReady: (geometry: Geometry, measurement?: string) => void;
  onFeatureSelect: (feature: Feature) => void;
  onGcpSelect: (point: GcpFeature) => void;
  onFeatureClear: () => void;
  onHint: (hint: string) => void;
  onMetadata: (metadata: RasterMetadata | null) => void;
}) {
  const { t } = useI18n();
  const requestKey = `${missionId}:${layer}`;
  const [metadataState, setMetadataState] = useState<{
    key: string;
    data: RasterMetadata | null;
    error: string;
  }>({ key: requestKey, data: null, error: "" });
  const metadata = metadataState.key === requestKey ? metadataState.data : null;
  const error = metadataState.key === requestKey ? metadataState.error : "";

  useEffect(() => {
    let active = true;
    getMapMetadata(missionId, layer)
      .then((result) => {
        if (!active) return;
        setMetadataState({ key: requestKey, data: result, error: "" });
        onMetadata(result);
      })
      .catch((reason: Error) => {
        if (active) {
          setMetadataState({ key: requestKey, data: null, error: reason.message });
        }
      });
    return () => {
      active = false;
    };
  }, [layer, missionId, onMetadata, requestKey]);

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
  const renderedStyle = useMemo<RasterStyleRecipe>(() => {
    if (!metadata || rasterStyle.stretch === "fixed") return rasterStyle;
    return {
      ...rasterStyle,
      display_ranges: rasterStyle.bands.map(
        (band) => metadata.display_ranges?.[band - 1] ?? null,
      ),
    };
  }, [metadata, rasterStyle]);

  if (error) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-gray-300">
        {t("map.unavailable", { error })}
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
      className={`h-full w-full map-cursor-${tool}`}
      preferCanvas
      doubleClickZoom={false}
      zoomControl={false}
    >
      <ResizeController />
      <InteractionController tool={tool} />
      <CoordinateReadout />
      <SelectionController tool={tool} onFeatureClear={onFeatureClear} />
      <FitBounds bounds={bounds} />
      {searchBounds && <FitBounds bounds={searchBounds} padding={42} />}
      <TileLayer
        key={`${missionId}:${layer}:${JSON.stringify(renderedStyle)}`}
        url={getMapTileUrl(missionId, layer, renderedStyle)}
        minZoom={metadata.min_zoom}
        maxNativeZoom={metadata.max_zoom}
        maxZoom={Math.min(24, metadata.max_zoom + 2)}
        opacity={renderedStyle.opacity}
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
        tool={tool}
        selectedFeatureId={selectedFeatureId}
        onFeatureSelect={onFeatureSelect}
      />
      {showGcps && gcpCollection && (
        <GroundControlVectors
          collection={gcpCollection}
          tool={tool}
          selectedPointId={selectedGcpId}
          onPointSelect={onGcpSelect}
        />
      )}
      <DrawController
        key={tool}
        tool={tool}
        onGeometryReady={onGeometryReady}
        onHint={onHint}
      />
    </MapContainer>
  );
}
