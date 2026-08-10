"use client";

import React from "react";
import {
  Boxes,
  ScanSearch,
  Search,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import { useMissionRuntime } from "../lib/mission-runtime";
import { useI18n } from "../lib/i18n/provider";
import { useStore } from "../lib/store";
import StageHeader from "./StageHeader";
import {
  AVAILABLE_AI_BACKENDS,
} from "../lib/types";
import type { AIBackend, YOLOModelVariant } from "../lib/types";

export default function PhaseDetection() {
  const { t } = useI18n();
  const {
    aiConfidence,
    setAiConfidence,
    aiBackend,
    setAiBackend,
    aiModelVariant,
    setAiModelVariant,
    samPrompt,
    setSamPrompt,
    selectedClasses,
    setSelectedClasses,
    tileSize,
    setTileSize,
    parameterSchema,
  } = useStore();
  const { activeMission } = useMissionRuntime();

  const tilerService = activeMission?.services?.TILER;
  const iaService = activeMission?.services?.IA;
  const hasOrthomosaic =
    tilerService?.status === "success" || (tilerService?.progress ?? 0) >= 100;
  const yoloModels = parameterSchema?.yolo_models ?? [];
  const sam3MaximumTileSize =
    parameterSchema?.sam3.maximum_source_tile_size ?? 1024;
  const tileSizes = [512, 768, 1024, 1536, 2048].filter(
    (size) => aiBackend !== "sam3" || size <= sam3MaximumTileSize,
  );
  const selectedModel = yoloModels.find(
    (model) => model.id === aiModelVariant,
  );
  const availableClasses = selectedModel?.selectable_classes ?? [];

  const toggleClass = (className: string) => {
    if (
      selectedClasses.includes(className) &&
      selectedClasses.length === 1
    ) {
      return;
    }
    setSelectedClasses(
      selectedClasses.includes(className)
        ? selectedClasses.filter((entry) => entry !== className)
        : [...selectedClasses, className],
    );
  };

  const selectBackend = (backend: AIBackend) => {
    setAiBackend(backend);
    if (backend === "sam3" && tileSize > sam3MaximumTileSize) {
      setTileSize(sam3MaximumTileSize);
    }
  };

  return (
    <div className="space-y-5">
      <StageHeader
        eyebrow={t("detection.eyebrow")}
        title={t("detection.title")}
        description={t("detection.description")}
        icon={<ScanSearch size={21} />}
        iconClassName="bg-[#e8eefb] text-[#3458a5]"
        status={
          <div
            className={`flex items-center gap-2 rounded-2xl border px-4 py-3 ${
              hasOrthomosaic
                ? "border-emerald-200 bg-emerald-50"
                : "border-amber-200 bg-amber-50"
            }`}
          >
            <ShieldCheck
              size={17}
              className={hasOrthomosaic ? "text-emerald-600" : "text-amber-600"}
            />
            <div>
              <div className="text-[10px] font-bold uppercase tracking-wide text-[#7c8884]">
                {t("detection.inputReadiness")}
              </div>
              <div className="text-sm font-semibold text-[#34413d]">
                {hasOrthomosaic
                  ? t("detection.inputReady")
                  : t("detection.inputWaiting")}
              </div>
            </div>
          </div>
        }
      />

      {iaService && (
        <section className="rounded-[1.25rem] border border-[#cbd9f4] bg-[#f0f4fc] p-5">
          <div className="flex items-center justify-between text-sm">
            <span className="font-semibold text-[#394c75]">
              {iaService.step ?? "Detection"}
            </span>
            <span className="font-bold text-[#3458a5]">
              {iaService.progress ?? 0}%
            </span>
          </div>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/80">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                iaService.status === "error"
                  ? "bg-red-500"
                  : iaService.status === "success"
                    ? "bg-emerald-500"
                    : "bg-[#4568b1]"
              }`}
              style={{ width: `${iaService.progress ?? 0}%` }}
            />
          </div>
        </section>
      )}

      <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="surface p-5 sm:p-6">
        <div className="eyebrow">{t("detection.strategy")}</div>
        <h3 className="mb-4 mt-1 text-lg font-bold text-[#26332f]">
          {t("detection.goal")}
        </h3>
        <div className="grid gap-3 sm:grid-cols-2">
          {AVAILABLE_AI_BACKENDS.map((backend) => (
            <button
              key={backend.value}
              type="button"
              onClick={() => selectBackend(backend.value as AIBackend)}
              className={`min-h-[96px] rounded-2xl border p-4 text-left transition ${
                aiBackend === backend.value
                  ? "border-[#7f9bd4] bg-[#f0f4fc]"
                  : "border-[#dce4e1] bg-[#fafcfb] hover:border-[#b8c9c3]"
              }`}
            >
              <span className="flex items-center gap-2 text-sm font-bold text-[#2e3b37]">
                {backend.value === "yolo" ? (
                  <Boxes size={16} className="text-[#4568b1]" />
                ) : (
                  <Search size={16} className="text-[#4568b1]" />
                )}
                {backend.label}
              </span>
              <span className="mt-1.5 block text-xs leading-5 text-[#75827e]">
                {t(
                  backend.value === "yolo"
                    ? "detection.backend.yolo.description"
                    : "detection.backend.sam3.description",
                )}
              </span>
            </button>
          ))}
        </div>
        </div>
        <div className="surface p-5 sm:p-6">
          <div className="flex items-center gap-2">
            <SlidersHorizontal size={16} className="text-[#4568b1]" />
            <h3 className="text-base font-bold text-[#2d3a36]">
              {t("detection.sensitivity")}
            </h3>
          </div>
          <p className="mt-1 text-xs leading-5 text-[#77847f]">
            {t("detection.sensitivityHelp")}
          </p>
          <div className="mt-5 flex items-center gap-4">
            <input
              aria-label="Detection confidence threshold"
              type="range"
              min="0.1"
              max="0.9"
              step="0.05"
              value={aiConfidence}
              onChange={(event) =>
                setAiConfidence(Number.parseFloat(event.target.value))
              }
              className="h-2 flex-1 cursor-pointer accent-[#4568b1]"
            />
            <span className="min-w-16 rounded-xl bg-[#f0f4fc] px-3 py-2 text-center font-mono text-sm font-bold text-[#3458a5]">
              {aiConfidence.toFixed(2)}
            </span>
          </div>
        </div>
      </section>

      <section className="surface p-5 sm:p-6">
        <h3 className="text-base font-bold text-[#2d3a36]">
          {aiBackend === "sam3"
            ? t("detection.promptTitle")
            : t("detection.classesTitle")}
        </h3>
        <p className="mt-1 text-xs leading-5 text-[#77847f]">
          {aiBackend === "sam3"
            ? t("detection.promptHelp")
            : t("detection.classesHelp")}
        </p>
        {aiBackend === "sam3" ? (
          <div className="relative mt-4">
            <Search
              size={16}
              className="pointer-events-none absolute left-3 top-3.5 text-[#8a9692]"
            />
            <input
              value={samPrompt}
              onChange={(event) => setSamPrompt(event.target.value)}
              placeholder="e.g. vehicle, solar panel, building"
              className="input-control min-h-11 pl-10"
            />
          </div>
        ) : (
          <div className="mt-4 flex flex-wrap gap-2">
            {availableClasses.map((className) => {
              const selected = selectedClasses.includes(className);
              return (
                <button
                  key={className}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => toggleClass(className)}
                  className={`min-h-10 rounded-full border px-4 text-xs font-semibold transition ${
                    selected
                      ? "border-[#7f9bd4] bg-[#4568b1] text-white"
                      : "border-[#d9e1de] bg-white text-[#5d6965] hover:border-[#b5c4bf]"
                  }`}
                >
                  {className}
                </button>
              );
            })}
          </div>
        )}
      </section>

      <details className="surface">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 sm:px-6">
          <span>
            <span className="block text-sm font-bold text-[#2d3a36]">
              {t("detection.modelAndTiles")}
            </span>
            <span className="mt-0.5 block text-xs text-[#77847f]">
              {aiBackend === "yolo" ? aiModelVariant : "SAM 3"} · {tileSize} × {tileSize} px
            </span>
          </span>
          <span className="text-xs font-semibold text-[#4568b1]">
            {t("detection.modify")}
          </span>
        </summary>
        <div className="grid gap-6 border-t border-[#e5ebe8] p-5 sm:p-6 lg:grid-cols-[minmax(0,1fr)_280px]">
          <div>
            <h3 className="text-sm font-bold text-[#2d3a36]">
              {aiBackend === "yolo"
                ? t("detection.yoloCapacity")
                : t("detection.samBackend")}
            </h3>
            {aiBackend === "yolo" ? (
              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {yoloModels.map((model) => (
                  <button
                    key={model.id}
                    type="button"
                    disabled={!model.available}
                    onClick={() =>
                      setAiModelVariant(model.id as YOLOModelVariant)
                    }
                    className={`min-h-[78px] rounded-xl border p-3 text-left transition ${
                      aiModelVariant === model.id
                        ? "border-[#7f9bd4] bg-[#f0f4fc]"
                        : "border-[#dce4e1] bg-[#fafcfb] hover:border-[#b8c9c3]"
                    } disabled:cursor-not-allowed disabled:opacity-40`}
                  >
                    <span className="block text-xs font-bold text-[#2f3c38]">
                      {model.label}
                    </span>
                    <span className="mt-1 block text-[10px] leading-4 text-[#7a8783]">
                      {model.artifact} · {model.revision}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-xs leading-5 text-[#77847f]">
                {t("detection.samPromptHelp")}
              </p>
            )}
          </div>
          <label className="block">
            <span className="text-sm font-bold text-[#2d3a36]">
              {t("detection.tileSize")}
            </span>
            <span className="mt-1 block text-xs leading-5 text-[#77847f]">
              {t("detection.tileSizeHelp")}
            </span>
            <select
              value={tileSize}
              onChange={(event) => setTileSize(Number(event.target.value))}
              className="input-control mt-4 min-h-11"
            >
              {tileSizes.map((size) => (
                <option key={size} value={size}>
                  {size} × {size} px
                </option>
              ))}
            </select>
          </label>
        </div>
      </details>
    </div>
  );
}
