"use client";

import { LocateFixed } from "lucide-react";
import { useEffect, useState } from "react";
import { getMapMetadata } from "../../lib/api";

export type ExportCrsChoice = {
  request: string;
  label: string;
  valid: boolean;
};

export default function ExportCrsSelector({
  missionId,
  onChange,
}: {
  missionId: string;
  onChange: (choice: ExportCrsChoice) => void;
}) {
  const [mode, setMode] = useState<"raster" | "wgs84" | "custom">("raster");
  const [rasterCrs, setRasterCrs] = useState("");
  const [customCrs, setCustomCrs] = useState("EPSG:2154");

  useEffect(() => {
    let active = true;
    getMapMetadata(missionId, "ortho")
      .then((metadata) => {
        if (!active) return;
        const crs = String(
          (metadata as { crs?: string | null }).crs ?? "",
        ).trim();
        setRasterCrs(crs.toLowerCase() === "unknown" ? "" : crs);
      })
      .catch(() => active && setRasterCrs(""));
    return () => {
      active = false;
    };
  }, [missionId]);

  useEffect(() => {
    if (mode === "raster") {
      onChange({
        request: "raster",
        label: rasterCrs || "EPSG:4326",
        valid: true,
      });
      return;
    }
    if (mode === "wgs84") {
      onChange({ request: "EPSG:4326", label: "EPSG:4326", valid: true });
      return;
    }
    const normalized = customCrs.trim().toUpperCase();
    onChange({
      request: normalized,
      label: normalized,
      valid: /^EPSG:\d{4,6}$/.test(normalized),
    });
  }, [customCrs, mode, onChange, rasterCrs]);

  return (
    <section className="rounded-2xl border border-[#dce4e1] p-3.5">
      <div className="flex items-start gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#f0ecfb] text-[#7157a8]">
          <LocateFixed size={15} />
        </span>
        <div>
          <h3 className="text-sm font-bold text-[#2d3a36]">
            CRS des GeoPackages
          </h3>
          <p className="mt-0.5 text-[11px] leading-4 text-[#7a8783]">
            Appliqué aux couches IA, détections et annotations manuelles.
          </p>
        </div>
      </div>
      <select
        value={mode}
        onChange={(event) =>
          setMode(event.target.value as "raster" | "wgs84" | "custom")
        }
        className="input-control mt-3 text-xs"
      >
        <option value="raster">
          CRS du raster — {rasterCrs || "repli WGS84"}
        </option>
        <option value="wgs84">WGS84 — EPSG:4326</option>
        <option value="custom">EPSG personnalisé</option>
      </select>
      {mode === "custom" && (
        <div className="mt-2">
          <input
            value={customCrs}
            onChange={(event) => setCustomCrs(event.target.value)}
            placeholder="EPSG:2154"
            aria-label="Code EPSG personnalisé"
            className="input-control text-xs uppercase"
          />
          {!/^EPSG:\d{4,6}$/i.test(customCrs.trim()) && (
            <p className="mt-1 text-[10px] text-rose-600">
              Format attendu : EPSG:2154
            </p>
          )}
        </div>
      )}
      <p className="mt-2 text-[10px] leading-4 text-[#7a8783]">
        GeoJSON reste en EPSG:4326 selon RFC 7946.
      </p>
    </section>
  );
}
