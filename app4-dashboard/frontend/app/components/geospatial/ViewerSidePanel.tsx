"use client";

import type { ComponentProps } from "react";
import { Download, Layers, MapPinCheck, Search, Sparkles } from "lucide-react";
import type { MessageKey } from "../../lib/i18n/catalog";
import { useI18n } from "../../lib/i18n/provider";
import AnalysisPanel from "./AnalysisPanel";
import ExportPanel from "./ExportPanel";
import GcpPanel from "./GcpPanel";
import LayersPanel from "./LayersPanel";
import SearchPanel from "./SearchPanel";
import type { WorkspacePanel } from "./workspace-config";

interface ViewerSidePanelProps {
  expanded: boolean;
  activePanel: WorkspacePanel;
  onPanelChange: (panel: WorkspacePanel) => void;
  layers: ComponentProps<typeof LayersPanel>;
  gcp: ComponentProps<typeof GcpPanel>;
  analysis: ComponentProps<typeof AnalysisPanel>;
  search: ComponentProps<typeof SearchPanel>;
  exportPanel: ComponentProps<typeof ExportPanel>;
}

const PANELS = [
  ["layers", Layers, "explorer.panel.layers"],
  ["gcp", MapPinCheck, "explorer.panel.gcp"],
  ["analysis", Sparkles, "explorer.panel.analysis"],
  ["search", Search, "explorer.panel.objects"],
  ["export", Download, "explorer.panel.export"],
] as const satisfies ReadonlyArray<
  readonly [WorkspacePanel, typeof Layers, MessageKey]
>;

export default function ViewerSidePanel({
  expanded,
  activePanel,
  onPanelChange,
  layers,
  gcp,
  analysis,
  search,
  exportPanel,
}: ViewerSidePanelProps) {
  const { t } = useI18n();
  return (
    <aside
      className={`surface flex min-h-0 flex-col overflow-hidden ${
        expanded
          ? "absolute bottom-2 left-2 top-2 z-[510] w-[min(320px,calc(100%-16px))] xl:static xl:w-auto"
          : "order-2 max-h-[520px] xl:order-none xl:max-h-none"
      }`}
    >
      <div className="grid grid-cols-5 border-b border-[#e1e8e5] p-2">
        {PANELS.map(([id, Icon, labelKey]) => (
          <button
            type="button"
            key={id}
            onClick={() => onPanelChange(id)}
            className={`flex min-h-10 items-center justify-center gap-1 rounded-lg text-[11px] font-semibold ${
              activePanel === id
                ? "bg-[#e8f5f1] text-[#0f766e]"
                : "text-[#76827e] hover:bg-[#f3f6f5]"
            }`}
          >
            <Icon size={13} /> {t(labelKey)}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {activePanel === "layers" && <LayersPanel {...layers} />}
        {activePanel === "gcp" && <GcpPanel {...gcp} />}
        {activePanel === "analysis" && <AnalysisPanel {...analysis} />}
        {activePanel === "search" && <SearchPanel {...search} />}
        {activePanel === "export" && <ExportPanel {...exportPanel} />}
      </div>
    </aside>
  );
}
