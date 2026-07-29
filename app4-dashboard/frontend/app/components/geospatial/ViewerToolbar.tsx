"use client";

import { X } from "lucide-react";
import type { MapTool } from "../GeospatialMap";
import { TOOL_BUTTONS, TOOL_SHORTCUTS } from "./workspace-config";

interface ViewerToolbarProps {
  tool: MapTool;
  toolHint: string;
  redrawingFeature: boolean;
  shortcutsOpen: boolean;
  onToolChange: (tool: MapTool) => void;
  onShortcutsClose: () => void;
}

export default function ViewerToolbar({
  tool,
  toolHint,
  redrawingFeature,
  shortcutsOpen,
  onToolChange,
  onShortcutsClose,
}: ViewerToolbarProps) {
  return (
    <>
      <div className="absolute left-3 top-3 z-[500] flex flex-col gap-1.5 rounded-2xl border border-white/45 bg-white/92 p-1.5 shadow-xl backdrop-blur">
        {TOOL_BUTTONS.map(({ id, label, icon: Icon }) => (
          <button
            type="button"
            key={id}
            title={`${label} (${TOOL_SHORTCUTS[id]})`}
            aria-label={label}
            aria-keyshortcuts={TOOL_SHORTCUTS[id]}
            onClick={() => onToolChange(id)}
            className={`group relative flex h-10 w-10 items-center justify-center rounded-xl transition ${
              tool === id
                ? "bg-[#173f38] text-white"
                : "text-[#53615d] hover:bg-[#edf3f1]"
            }`}
          >
            <Icon size={16} />
            <span className="pointer-events-none absolute left-12 hidden whitespace-nowrap rounded-lg bg-[#17201e] px-2.5 py-1.5 text-[11px] font-semibold text-white shadow-lg group-hover:block">
              {label} · {TOOL_SHORTCUTS[id]}
            </span>
          </button>
        ))}
      </div>

      {(toolHint || redrawingFeature) && (
        <div className="absolute left-1/2 top-3 z-[500] max-w-[calc(100%-150px)] -translate-x-1/2 rounded-full bg-[#17201e]/88 px-4 py-2 text-center text-xs text-white shadow backdrop-blur">
          {redrawingFeature
            ? "Redessinez la géométrie · double-cliquez pour terminer · Échap pour annuler"
            : toolHint}
        </div>
      )}

      {shortcutsOpen && (
        <div className="absolute right-3 top-3 z-[520] w-60 rounded-2xl bg-white p-4 shadow-2xl">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-[#27342f]">Raccourcis</h3>
            <button
              type="button"
              onClick={onShortcutsClose}
              aria-label="Fermer"
            >
              <X size={14} />
            </button>
          </div>
          <div className="mt-3 grid grid-cols-[1fr_auto] gap-x-4 gap-y-2 text-xs text-[#66736f]">
            {TOOL_BUTTONS.map(({ id, label }) => (
              <span key={id} className="contents">
                <span>{label}</span>
                <kbd className="rounded-md border bg-[#f5f7f6] px-1.5 py-0.5 font-mono font-bold">
                  {TOOL_SHORTCUTS[id]}
                </kbd>
              </span>
            ))}
            <span>Panneau</span>
            <kbd className="rounded-md border bg-[#f5f7f6] px-1.5 py-0.5 font-mono font-bold">
              B
            </kbd>
            <span>Plein écran</span>
            <kbd className="rounded-md border bg-[#f5f7f6] px-1.5 py-0.5 font-mono font-bold">
              F
            </kbd>
            <span>Annuler / quitter</span>
            <kbd className="rounded-md border bg-[#f5f7f6] px-1.5 py-0.5 font-mono font-bold">
              Esc
            </kbd>
          </div>
        </div>
      )}
    </>
  );
}
