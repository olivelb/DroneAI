"use client";

import type { Geometry } from "geojson";
import { useState } from "react";
import {
  editableCoordinateVertices,
  updateEditableCoordinateVertex,
} from "./geometry-coordinates";

function CoordinateVertexField({
  label,
  longitude,
  latitude,
  onCommit,
}: {
  label: string;
  longitude: number;
  latitude: number;
  onCommit: (longitude: number, latitude: number) => void;
}) {
  const [longitudeText, setLongitudeText] = useState(String(longitude));
  const [latitudeText, setLatitudeText] = useState(String(latitude));
  const commit = () => {
    const nextLongitude = Number(longitudeText);
    const nextLatitude = Number(latitudeText);
    if (
      Number.isFinite(nextLongitude) &&
      nextLongitude >= -180 &&
      nextLongitude <= 180 &&
      Number.isFinite(nextLatitude) &&
      nextLatitude >= -90 &&
      nextLatitude <= 90
    ) {
      onCommit(nextLongitude, nextLatitude);
    } else {
      setLongitudeText(String(longitude));
      setLatitudeText(String(latitude));
    }
  };
  return (
    <div className="rounded-xl border border-[#dce4e1] bg-[#f8faf9] p-2">
      <div className="mb-1 text-[10px] font-bold uppercase tracking-wide text-[#71807b]">
        {label} · EPSG:4326
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="text-[10px] text-[#71807b]">
          Longitude (X)
          <input
            inputMode="decimal"
            value={longitudeText}
            onChange={(event) => setLongitudeText(event.target.value)}
            onBlur={commit}
            onKeyDown={(event) => event.key === "Enter" && commit()}
            className="input-control mt-1 font-mono text-xs"
          />
        </label>
        <label className="text-[10px] text-[#71807b]">
          Latitude (Y)
          <input
            inputMode="decimal"
            value={latitudeText}
            onChange={(event) => setLatitudeText(event.target.value)}
            onBlur={commit}
            onKeyDown={(event) => event.key === "Enter" && commit()}
            className="input-control mt-1 font-mono text-xs"
          />
        </label>
      </div>
    </div>
  );
}

export default function GeometryCoordinateEditor({
  geometry,
  onChange,
}: {
  geometry: Geometry;
  onChange: (geometry: Geometry) => void;
}) {
  const vertices = editableCoordinateVertices(geometry);
  if (!vertices) return null;
  return (
    <details className="rounded-xl border border-[#dce4e1] px-2 py-1.5">
      <summary className="cursor-pointer text-xs font-semibold text-[#52615c]">
        Coordonnées GPS · {vertices.length} point(s)
      </summary>
      <div className="mt-2 max-h-52 space-y-2 overflow-auto pr-1">
        {vertices.map((vertex) => (
          <CoordinateVertexField
            key={`${vertex.index}:${vertex.longitude}:${vertex.latitude}`}
            {...vertex}
            onCommit={(longitude, latitude) =>
              onChange(
                updateEditableCoordinateVertex(
                  geometry,
                  vertex.index,
                  longitude,
                  latitude,
                ),
              )
            }
          />
        ))}
      </div>
    </details>
  );
}
