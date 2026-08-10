"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Minus, Plus, SkipForward, X } from "lucide-react";
import { getFileUrl } from "../../lib/api";
import { useI18n } from "../../lib/i18n/provider";
import type { GcpFeature, GcpObservation } from "../../lib/types";

interface PhotoMarkerEditorProps {
  point: GcpFeature;
  observation: GcpObservation;
  busy: boolean;
  onClose: () => void;
  onSave: (pixelX: number, pixelY: number) => Promise<void>;
  onSkip: () => Promise<void>;
}

export default function PhotoMarkerEditor({
  point,
  observation,
  busy,
  onClose,
  onSave,
  onSkip,
}: PhotoMarkerEditorProps) {
  const { t } = useI18n();
  const imageRef = useRef<HTMLImageElement>(null);
  const [naturalSize, setNaturalSize] = useState({ width: 0, height: 0 });
  const [zoom, setZoom] = useState(0.25);
  const [pixel, setPixel] = useState<{ x: number; y: number } | null>(
    observation.pixel_x !== null && observation.pixel_x !== undefined &&
      observation.pixel_y !== null && observation.pixel_y !== undefined
      ? { x: observation.pixel_x, y: observation.pixel_y }
      : null,
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Enter" && pixel && !busy) {
        void onSave(pixel.x, pixel.y);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose, onSave, pixel]);

  const placeMarker = (event: React.MouseEvent<HTMLImageElement>) => {
    const image = imageRef.current;
    if (!image) return;
    const bounds = image.getBoundingClientRect();
    setPixel({
      x: Math.max(0, Math.min(image.naturalWidth, (event.clientX - bounds.left) * image.naturalWidth / bounds.width)),
      y: Math.max(0, Math.min(image.naturalHeight, (event.clientY - bounds.top) * image.naturalHeight / bounds.height)),
    });
  };

  return (
    <div className="absolute inset-0 z-[700] flex flex-col bg-[#111916]/95 text-white">
      <header className="flex items-center gap-3 border-b border-white/10 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">
            {point.properties.external_id} · {observation.image_name}
          </div>
          <div className="text-xs text-white/60">
            {t("gcp.markerHelp")}
          </div>
        </div>
        <button type="button" onClick={onClose} aria-label={t("common.close")}>
          <X size={19} />
        </button>
      </header>
      <div className="flex items-center gap-2 border-b border-white/10 px-4 py-2 text-xs">
        <button
          type="button"
          onClick={() => setZoom((value) => Math.max(0.05, value / 1.4))}
          className="rounded-lg bg-white/10 p-2"
        >
          <Minus size={14} />
        </button>
        <span className="w-14 text-center font-mono">{Math.round(zoom * 100)} %</span>
        <button
          type="button"
          onClick={() => setZoom((value) => Math.min(4, value * 1.4))}
          className="rounded-lg bg-white/10 p-2"
        >
          <Plus size={14} />
        </button>
        <span className="ml-3 text-white/60">
          {naturalSize.width} × {naturalSize.height} px
        </span>
        <span className="ml-auto font-mono">
          X {pixel ? pixel.x.toFixed(1) : "—"} · Y {pixel ? pixel.y.toFixed(1) : "—"}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-black/40 p-4">
        {observation.image_s3_key && (
          <div
            className="relative mx-auto"
            style={{
              width: naturalSize.width ? naturalSize.width * zoom : "fit-content",
              height: naturalSize.height ? naturalSize.height * zoom : "auto",
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              ref={imageRef}
              src={getFileUrl(observation.image_s3_key)}
              alt={observation.image_name}
              draggable={false}
              onLoad={(event) => {
                const image = event.currentTarget;
                setNaturalSize({ width: image.naturalWidth, height: image.naturalHeight });
                setZoom(Math.min(1, 1100 / image.naturalWidth, 650 / image.naturalHeight));
              }}
              onClick={placeMarker}
              className="block max-w-none cursor-crosshair select-none"
              style={{ width: naturalSize.width ? naturalSize.width * zoom : "auto" }}
            />
            {pixel && naturalSize.width > 0 && (
              <span
                className="pointer-events-none absolute h-8 w-8 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-red-500 shadow-[0_0_0_1px_white] before:absolute before:left-1/2 before:top-[-8px] before:h-12 before:w-px before:-translate-x-1/2 before:bg-red-500 after:absolute after:left-[-8px] after:top-1/2 after:h-px after:w-12 after:-translate-y-1/2 after:bg-red-500"
                style={{
                  left: `${(pixel.x / naturalSize.width) * 100}%`,
                  top: `${(pixel.y / naturalSize.height) * 100}%`,
                }}
              />
            )}
          </div>
        )}
      </div>
      <footer className="flex items-center justify-end gap-2 border-t border-white/10 px-4 py-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => void onSkip()}
          className="flex items-center gap-2 rounded-xl bg-white/10 px-4 py-2 text-sm disabled:opacity-40"
        >
          <SkipForward size={15} /> {t("gcp.skipPhoto")}
        </button>
        <button
          type="button"
          disabled={!pixel || busy}
          onClick={() => pixel && void onSave(pixel.x, pixel.y)}
          className="flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold disabled:opacity-40"
        >
          <Check size={15} /> {t("gcp.markPhoto")}
        </button>
      </footer>
    </div>
  );
}
