"use client";

import { useState } from "react";
import type { Feature, Geometry } from "geojson";
import { CheckCircle2, PenLine, Save, Trash2, Undo2, X } from "lucide-react";
import { useI18n } from "../../lib/i18n/provider";
import {
  editableCoordinateVertices,
  updateEditableCoordinateVertex,
} from "./geometry-coordinates";
import { splitTags } from "./workspace-config";

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

function GeometryCoordinateEditor({
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

interface DraftFeatureEditorProps {
  geometry: Geometry;
  measurement: string;
  name: string;
  description: string;
  color: string;
  tags: string;
  onNameChange: (value: string) => void;
  onDescriptionChange: (value: string) => void;
  onColorChange: (value: string) => void;
  onTagsChange: (value: string) => void;
  onGeometryChange: (geometry: Geometry) => void;
  onClose: () => void;
  onSave: () => void;
}

export function DraftFeatureEditor({
  geometry,
  measurement,
  name,
  description,
  color,
  tags,
  onNameChange,
  onDescriptionChange,
  onColorChange,
  onTagsChange,
  onGeometryChange,
  onClose,
  onSave,
}: DraftFeatureEditorProps) {
  const { t } = useI18n();
  return (
    <div className="absolute bottom-4 right-4 z-[500] w-[min(360px,calc(100%-32px))] rounded-2xl bg-white p-4 shadow-2xl">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-[#27342f]">
          {measurement
            ? t("editor.measurement", { measurement })
            : t("editor.newAnnotation")}
        </h3>
        <button type="button" onClick={onClose} aria-label={t("common.close")}>
          <X size={15} />
        </button>
      </div>
      <div className="mt-3 space-y-2">
        <input
          value={name}
          onChange={(event) => onNameChange(event.target.value)}
          className="input-control"
          placeholder={t("editor.name")}
        />
        <textarea
          value={description}
          onChange={(event) => onDescriptionChange(event.target.value)}
          className="input-control min-h-16"
          placeholder={t("editor.description")}
        />
        <GeometryCoordinateEditor
          geometry={geometry}
          onChange={onGeometryChange}
        />
        <div className="grid grid-cols-[1fr_50px] gap-2">
          <input
            value={tags}
            onChange={(event) => onTagsChange(event.target.value)}
            className="input-control"
            placeholder={t("editor.tagsPlaceholder")}
          />
          <input
            type="color"
            value={color}
            onChange={(event) => onColorChange(event.target.value)}
            className="h-11 rounded-xl border p-1"
            aria-label={t("editor.color")}
          />
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onClose}
            className="min-h-10 flex-1 rounded-xl border text-xs"
          >
            {t("editor.keepTemporary")}
          </button>
          <button
            type="button"
            onClick={onSave}
            className="flex min-h-10 flex-1 items-center justify-center gap-1 rounded-xl bg-[#0f766e] text-xs font-semibold text-white"
          >
            <Save size={13} /> {t("common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}

interface SelectedFeatureEditorProps {
  feature: Feature;
  onChange: (feature: Feature) => void;
  onClose: () => void;
  onDelete: () => void;
  onRedraw: () => void;
  onSave: () => void;
  onReview: (reviewed: boolean) => void;
}

export function SelectedFeatureEditor({
  feature,
  onChange,
  onClose,
  onDelete,
  onRedraw,
  onSave,
  onReview,
}: SelectedFeatureEditorProps) {
  const { t } = useI18n();
  const editable = ["manual", "ai"].includes(String(feature.properties?.source));
  const reviewed = feature.properties?.reviewed === true;
  const updateProperty = (name: string, value: string) =>
    onChange({
      ...feature,
      properties: { ...feature.properties, [name]: value },
    });
  const tags = Array.isArray(feature.properties?.tags)
    ? feature.properties.tags.join(", ")
    : String(feature.properties?.tags || "");

  return (
    <div className="absolute bottom-4 right-4 z-[500] w-[min(360px,calc(100%-32px))] rounded-2xl bg-white p-4 shadow-2xl">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-[#27342f]">
          {t("editor.objectProperties")}
        </h3>
        <button type="button" onClick={onClose} aria-label={t("common.close")}>
          <X size={15} />
        </button>
      </div>
      <div className="mt-3 space-y-2">
        <input
          value={String(
            feature.properties?.name ||
              feature.properties?.class_name ||
              "",
          )}
          disabled={!editable}
          onChange={(event) =>
            updateProperty("name", event.target.value)
          }
          className="input-control disabled:bg-slate-50"
        />
        <textarea
          value={String(feature.properties?.description || "")}
          disabled={!editable}
          onChange={(event) =>
            updateProperty("description", event.target.value)
          }
          className="input-control min-h-16 disabled:bg-slate-50"
        />
        {editable && feature.geometry && (
          <GeometryCoordinateEditor
            geometry={feature.geometry}
            onChange={(geometry) => onChange({ ...feature, geometry })}
          />
        )}
        {editable && (
          <div className="grid grid-cols-[1fr_50px] gap-2">
            <input
              value={tags}
              onChange={(event) =>
                onChange({
                  ...feature,
                  properties: {
                    ...feature.properties,
                    tags: splitTags(event.target.value),
                  },
                })
              }
              className="input-control"
              placeholder={t("editor.tagsPlaceholder")}
            />
            <input
              type="color"
              value={String(feature.properties?.color || "#10b981")}
              onChange={(event) => updateProperty("color", event.target.value)}
              className="h-11 rounded-xl border border-[#dce4e1] p-1"
              aria-label={t("editor.color")}
            />
          </div>
        )}
        <div className="flex flex-wrap gap-1 text-[10px] text-[#71807b]">
          <span className="rounded-full bg-slate-100 px-2 py-1">
            {String(feature.properties?.source || "—")}
          </span>
          {feature.properties?.confidence !== undefined && (
            <span className="rounded-full bg-slate-100 px-2 py-1">
              {Math.round(
                Number(feature.properties.confidence) * 100,
              )}{" "}
              %
            </span>
          )}
        </div>
        {editable && (
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => onReview(!reviewed)}
              className="flex min-h-10 items-center justify-center gap-1 rounded-xl border border-emerald-200 px-3 text-xs text-emerald-700"
            >
              {reviewed ? <Undo2 size={13} /> : <CheckCircle2 size={13} />}
              {reviewed ? t("editor.unreview") : t("editor.review")}
            </button>
            <button
              type="button"
              onClick={onDelete}
              className="flex min-h-10 items-center justify-center gap-1 rounded-xl border border-rose-200 px-3 text-xs text-rose-700"
            >
              <Trash2 size={13} /> {t("editor.withdraw")}
            </button>
            <button
              type="button"
              onClick={onRedraw}
              className="flex min-h-10 items-center justify-center gap-1 rounded-xl border border-[#d4dfdb] px-3 text-xs text-[#52615c]"
            >
              <PenLine size={13} /> {t("editor.redraw")}
            </button>
            <button
              type="button"
              onClick={onSave}
              className="flex min-h-10 flex-1 items-center justify-center gap-1 rounded-xl bg-[#0f766e] text-xs font-semibold text-white"
            >
              <Save size={13} /> {t("editor.update")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
