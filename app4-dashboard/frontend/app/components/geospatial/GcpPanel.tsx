"use client";

import { useState } from "react";
import { Crosshair, Eye, MapPin, Upload } from "lucide-react";
import type { MessageKey } from "../../lib/i18n/catalog";
import { useI18n } from "../../lib/i18n/provider";
import type {
  GcpCollection,
  GcpFeature,
  GcpImportOptions,
  GcpObservation,
  GcpRole,
} from "../../lib/types";

interface GcpPanelProps {
  collection: GcpCollection | null;
  selectedPoint: GcpFeature | null;
  visible: boolean;
  busy: boolean;
  onVisibilityChange: (visible: boolean) => void;
  onImport: (file: File, options: GcpImportOptions) => Promise<void>;
  onPointSelect: (point: GcpFeature) => void;
  onPointUpdate: (
    point: GcpFeature,
    request: Record<string, unknown>,
  ) => Promise<void>;
  onObservationOpen: (point: GcpFeature, observation: GcpObservation) => void;
}

const DEFAULT_IMPORT: GcpImportOptions = {
  name: "Ground control",
  sourceCrs: "",
  defaultRole: "adjustment",
  horizontalAccuracyM: 0.02,
  verticalAccuracyM: 0.03,
  imageAccuracyPx: 1,
  candidateRadiusM: 250,
  maxCandidates: 20,
};

const numberValue = (value: string) => Number.parseFloat(value);

function GcpPointEditor({
  point,
  busy,
  onUpdate,
  onObservationOpen,
}: {
  point: GcpFeature;
  busy: boolean;
  onUpdate: (point: GcpFeature, request: Record<string, unknown>) => Promise<void>;
  onObservationOpen: (point: GcpFeature, observation: GcpObservation) => void;
}) {
  const { t } = useI18n();
  const [pointLongitude, pointLatitude] = point.geometry.coordinates;
  const [longitude, setLongitude] = useState(String(pointLongitude));
  const [latitude, setLatitude] = useState(String(pointLatitude));
  const [altitude, setAltitude] = useState(String(point.properties.altitude_m));
  const [role, setRole] = useState<GcpRole>(point.properties.role);
  const [horizontalAccuracy, setHorizontalAccuracy] = useState(
    String(point.properties.horizontal_accuracy_m),
  );
  const [verticalAccuracy, setVerticalAccuracy] = useState(
    String(point.properties.vertical_accuracy_m),
  );
  const [imageAccuracy, setImageAccuracy] = useState(
    String(point.properties.image_accuracy_px),
  );
  const save = () => onUpdate(point, {
    longitude: numberValue(longitude),
    latitude: numberValue(latitude),
    altitude_m: numberValue(altitude),
    role,
    horizontal_accuracy_m: numberValue(horizontalAccuracy),
    vertical_accuracy_m: numberValue(verticalAccuracy),
    image_accuracy_px: numberValue(imageAccuracy),
    version: point.properties.version,
  });

  return (
    <section className="space-y-3 rounded-xl border border-[#b9d8d1] bg-[#f5fbf9] p-3">
      <div className="eyebrow flex items-center gap-2">
        <Crosshair size={13} /> {point.properties.external_id}
      </div>
      <div className="grid grid-cols-2 gap-2">
        {[
          [t("gcp.longitude"), longitude, setLongitude],
          [t("gcp.latitude"), latitude, setLatitude],
          [t("gcp.altitude"), altitude, setAltitude],
        ].map(([label, value, setter]) => (
          <label key={String(label)} className="text-xs text-[#5d6965] last:col-span-2">
            {String(label)}
            <input
              type="number"
              step="any"
              value={String(value)}
              onChange={(event) => (setter as (value: string) => void)(event.target.value)}
              className="input-control mt-1 w-full font-mono"
            />
          </label>
        ))}
      </div>
      <label className="block text-xs text-[#5d6965]">
        {t("gcp.role")}
        <select
          value={role}
          onChange={(event) => setRole(event.target.value as GcpRole)}
          className="input-control mt-1 w-full"
        >
          <option value="adjustment">{t("gcp.role.adjustment")}</option>
          <option value="checkpoint">{t("gcp.role.checkpoint")}</option>
          <option value="disabled">{t("gcp.role.disabled")}</option>
        </select>
      </label>
      <div className="grid grid-cols-3 gap-2">
        {[
          ["σXY m", horizontalAccuracy, setHorizontalAccuracy],
          ["σZ m", verticalAccuracy, setVerticalAccuracy],
          ["σ px", imageAccuracy, setImageAccuracy],
        ].map(([label, value, setter]) => (
          <label key={String(label)} className="text-[10px] text-[#5d6965]">
            {String(label)}
            <input
              type="number"
              min="0.0001"
              step="any"
              value={String(value)}
              onChange={(event) => (setter as (value: string) => void)(event.target.value)}
              className="input-control mt-1 w-full"
            />
          </label>
        ))}
      </div>
      <button
        type="button"
        disabled={busy}
        onClick={() => void save()}
        className="primary-button w-full disabled:opacity-40"
      >
        {t("gcp.savePoint")}
      </button>
      <div className="space-y-2 border-t border-[#dce4e1] pt-3">
        <div className="eyebrow">{t("gcp.photoObservations")}</div>
        {point.properties.observations.map((observation) => (
          <button
            type="button"
            key={observation.observation_id}
            disabled={!observation.image_s3_key}
            onClick={() => onObservationOpen(point, observation)}
            className="flex w-full items-center gap-2 rounded-lg border border-[#dce4e1] bg-white p-2 text-left text-xs disabled:opacity-40"
          >
            <Crosshair size={13} />
            <span className="min-w-0 flex-1 truncate">{observation.image_name}</span>
            <span className="text-[10px] uppercase text-[#76827e]">
              {t(`gcp.status.${observation.status}` as MessageKey)}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

export default function GcpPanel({
  collection,
  selectedPoint,
  visible,
  busy,
  onVisibilityChange,
  onImport,
  onPointSelect,
  onPointUpdate,
  onObservationOpen,
}: GcpPanelProps) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [options, setOptions] = useState(DEFAULT_IMPORT);

  const submitImport = async () => {
    if (!file || !options.name.trim()) return;
    await onImport(file, { ...options, name: options.name.trim() });
    setFile(null);
  };

  return (
    <div className="space-y-5">
      <button
        type="button"
        onClick={() => onVisibilityChange(!visible)}
        className="flex w-full items-center gap-2 rounded-xl border border-[#dce4e1] p-3 text-left text-sm text-[#5d6965]"
      >
        <Eye size={15} className={visible ? "text-emerald-600" : "opacity-40"} />
        {t("gcp.showOnMap")}
      </button>

      <section className="space-y-3 rounded-xl border border-[#dce4e1] p-3">
        <div className="eyebrow flex items-center gap-2">
          <Upload size={13} /> {t("gcp.importTitle")}
        </div>
        <input
          type="file"
          accept=".csv,.tsv,.txt,.xyz,.json,.geojson"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          className="block w-full text-xs text-[#5d6965] file:mr-2 file:rounded-lg file:border-0 file:bg-[#e8f5f1] file:px-3 file:py-2 file:text-[#0f766e]"
        />
        <label className="block text-xs text-[#5d6965]">
          {t("gcp.setName")}
          <input
            value={options.name}
            onChange={(event) => setOptions({ ...options, name: event.target.value })}
            className="input-control mt-1 w-full"
          />
        </label>
        <label className="block text-xs text-[#5d6965]">
          {t("gcp.sourceCrs")}
          <input
            value={options.sourceCrs}
            placeholder="EPSG:2154"
            onChange={(event) => setOptions({ ...options, sourceCrs: event.target.value })}
            className="input-control mt-1 w-full"
          />
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs text-[#5d6965]">
            {t("gcp.defaultRole")}
            <select
              value={options.defaultRole}
              onChange={(event) =>
                setOptions({ ...options, defaultRole: event.target.value as GcpRole })
              }
              className="input-control mt-1 w-full"
            >
              <option value="adjustment">{t("gcp.role.adjustment")}</option>
              <option value="checkpoint">{t("gcp.role.checkpoint")}</option>
              <option value="disabled">{t("gcp.role.disabled")}</option>
            </select>
          </label>
          <label className="text-xs text-[#5d6965]">
            {t("gcp.candidateRadius")}
            <input
              type="number"
              min="1"
              max="10000"
              value={options.candidateRadiusM}
              onChange={(event) =>
                setOptions({ ...options, candidateRadiusM: numberValue(event.target.value) })
              }
              className="input-control mt-1 w-full"
            />
          </label>
        </div>
        <button
          type="button"
          disabled={!file || busy}
          onClick={() => void submitImport()}
          className="primary-button w-full disabled:opacity-40"
        >
          {busy ? t("gcp.importing") : t("gcp.importAction")}
        </button>
        <p className="text-[11px] leading-4 text-[#87938f]">
          {t("gcp.importHelp")}
        </p>
      </section>

      <section>
        <div className="eyebrow mb-2">{t("gcp.points")}</div>
        <div className="space-y-2">
          {(collection?.features ?? []).map((point) => (
            <button
              type="button"
              key={point.properties.point_id}
              onClick={() => onPointSelect(point)}
              className={`flex w-full items-center gap-2 rounded-xl border p-3 text-left text-sm ${
                selectedPoint?.properties.point_id === point.properties.point_id
                  ? "border-[#68bfae] bg-[#edf9f6]"
                  : "border-[#dce4e1]"
              }`}
            >
              <MapPin size={14} className="text-[#0f766e]" />
              <span className="min-w-0 flex-1 truncate font-semibold">
                {point.properties.external_id}
              </span>
              <span className="text-[10px] text-[#76827e]">
                {point.properties.observation_summary.marked}/
                {point.properties.observations.length}
              </span>
            </button>
          ))}
          {!collection?.features.length && (
            <p className="text-sm text-[#87938f]">{t("gcp.noPoints")}</p>
          )}
        </div>
      </section>

      {selectedPoint && (
        <GcpPointEditor
          key={`${selectedPoint.properties.point_id}:${selectedPoint.properties.version}`}
          point={selectedPoint}
          busy={busy}
          onUpdate={onPointUpdate}
          onObservationOpen={onObservationOpen}
        />
      )}
    </div>
  );
}
