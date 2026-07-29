import type { ReactNode } from "react";

import type { ParamValue } from "../lib/types";

type Preset = {
  id: string;
  label: string;
  description: string;
  icon: ReactNode;
  values: Readonly<Record<string, ParamValue>>;
};

type PresetButtonProps = {
  preset: Preset;
  parameterValues: Record<string, ParamValue>;
  onApply: (values: Readonly<Record<string, ParamValue>>) => void;
  layout: "row" | "stacked";
  tone: "teal" | "amber";
};

const TONES = {
  teal: {
    selected:
      "border-[#68bfae] bg-[#edf9f6] shadow-[0_8px_24px_rgba(15,118,110,0.08)]",
    idle: "border-[#dce4e1] bg-[#fafcfb] hover:border-[#b8c9c3]",
    selectedIcon: "bg-[#0f766e] text-white",
    idleIcon: "bg-white text-[#65736f]",
  },
  amber: {
    selected:
      "border-[#e2b557] bg-[#fff8e7] shadow-[0_8px_24px_rgba(180,116,12,0.08)]",
    idle: "border-[#dce4e1] bg-[#fafcfb] hover:border-[#c8b986]",
    selectedIcon: "bg-[#b66b05] text-white",
    idleIcon: "bg-white text-[#7a7568]",
  },
} as const;

export function PresetButton({
  preset,
  parameterValues,
  onApply,
  layout,
  tone,
}: PresetButtonProps) {
  const selected = Object.entries(preset.values).every(
    ([key, value]) => String(parameterValues[key]) === String(value),
  );
  const styles = TONES[tone];
  const rowLayout = layout === "row";

  return (
    <button
      type="button"
      onClick={() => onApply(preset.values)}
      className={`${rowLayout ? "flex min-h-[92px] items-start gap-3" : "min-h-[130px]"} rounded-2xl border p-4 text-left transition ${
        selected ? styles.selected : styles.idle
      }`}
    >
      <span
        className={`flex h-9 w-9 items-center justify-center rounded-xl ${
          rowLayout ? "shrink-0" : ""
        } ${selected ? styles.selectedIcon : styles.idleIcon}`}
      >
        {preset.icon}
      </span>
      <span className={rowLayout ? "" : "mt-3 block"}>
        <span className="block text-sm font-bold text-[#2b3834]">
          {preset.label}
        </span>
        <span className="mt-1 block text-xs leading-5 text-[#75827e]">
          {preset.description}
        </span>
      </span>
    </button>
  );
}
