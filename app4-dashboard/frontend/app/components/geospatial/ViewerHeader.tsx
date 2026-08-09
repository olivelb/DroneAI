"use client";

import {
  Eye,
  Keyboard,
  Maximize2,
  Minimize2,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
} from "lucide-react";
import { useI18n } from "../../lib/i18n/provider";

interface ViewerHeaderProps {
  expanded: boolean;
  panelOpen: boolean;
  missionId: string;
  selectedMission: string;
  missions: Array<{ vol_id: string }>;
  searchText: string;
  busySearch: boolean;
  onMissionChange: (missionId: string) => void;
  onSearchTextChange: (value: string) => void;
  onSearch: () => void;
  onPanelToggle: () => void;
  onShortcutsToggle: () => void;
  onExpandedToggle: () => void;
}

export default function ViewerHeader({
  expanded,
  panelOpen,
  missionId,
  selectedMission,
  missions,
  searchText,
  busySearch,
  onMissionChange,
  onSearchTextChange,
  onSearch,
  onPanelToggle,
  onShortcutsToggle,
  onExpandedToggle,
}: ViewerHeaderProps) {
  const { t } = useI18n();
  return (
    <section
      className={`flex shrink-0 flex-col gap-3 p-3 sm:flex-row sm:items-center ${
        expanded
          ? "border-b border-[#d9e2de] bg-[#f7f9f8] sm:px-4"
          : "surface sm:p-4"
      }`}
    >
      <div className="flex min-w-0 items-center gap-3">
        <span className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#173f3b] text-white sm:flex">
          <Eye size={17} />
        </span>
        <div className="min-w-0">
          <div className="eyebrow">{t("explorer.title")}</div>
          <h2 className="truncate text-lg font-bold tracking-[-0.03em] text-[#17201e]">
            {missionId}
          </h2>
        </div>
      </div>

      <div className="flex min-w-0 flex-1 items-center gap-2 sm:justify-end">
        <select
          value={selectedMission}
          onChange={(event) => onMissionChange(event.target.value)}
          aria-label={t("explorer.displayedMission")}
          className="input-control hidden min-h-10 max-w-52 sm:block"
        >
          {missions.map((item) => (
            <option key={item.vol_id} value={item.vol_id}>
              {item.vol_id}
            </option>
          ))}
        </select>
        <div className="flex min-w-0 flex-1 sm:max-w-xl">
          <input
            value={searchText}
            onChange={(event) => onSearchTextChange(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && onSearch()}
            placeholder={t("explorer.searchPlaceholder")}
            className="input-control min-h-10 rounded-r-none"
          />
          <button
            type="button"
            onClick={onSearch}
            disabled={busySearch}
            className="flex min-w-11 items-center justify-center rounded-r-xl bg-[#173f38] text-white hover:bg-[#0f766e] disabled:opacity-50"
            aria-label={t("explorer.search")}
          >
            <Search size={16} />
          </button>
        </div>
        <button
          type="button"
          onClick={onPanelToggle}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[#d7e0dc] bg-white text-[#53615d] hover:border-[#a9bdb6]"
          aria-label={
            panelOpen ? t("explorer.hidePanel") : t("explorer.showPanel")
          }
          title={`${panelOpen ? t("explorer.hidePanel") : t("explorer.showPanel")} (B)`}
        >
          {panelOpen ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
        </button>
        <button
          type="button"
          onClick={onShortcutsToggle}
          className="hidden h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[#d7e0dc] bg-white text-[#53615d] hover:border-[#a9bdb6] sm:flex"
          aria-label={t("explorer.shortcuts")}
          title={t("explorer.shortcuts")}
        >
          <Keyboard size={16} />
        </button>
        <button
          type="button"
          onClick={onExpandedToggle}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#173f38] text-white hover:bg-[#0f766e]"
          aria-label={
            expanded ? t("explorer.exitFullscreen") : t("explorer.fullscreen")
          }
          title={`${expanded ? t("explorer.exitFullscreen") : t("explorer.fullscreen")} (F)`}
        >
          {expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
        </button>
      </div>
    </section>
  );
}
