"use client";

import { ChevronDown, Search, SlidersHorizontal, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useI18n } from "../lib/i18n/provider";
import type { ParameterMeta } from "../lib/types";
import { ParamField } from "./ParamField";

export interface ParameterGroup {
  id: string;
  label: string;
  description: string;
  keys: readonly string[];
}

interface AdvancedParametersProps {
  groups: readonly ParameterGroup[];
  metadata: Record<string, ParameterMeta>;
  values: Record<string, string | boolean>;
  onChange: (key: string, value: string | boolean) => void;
  title?: string;
  description?: string;
}

export default function AdvancedParameters({
  groups,
  metadata,
  values,
  onChange,
  title,
  description,
}: AdvancedParametersProps) {
  const { t } = useI18n();
  const availableGroups = useMemo(
    () =>
      groups
        .map((group) => ({
          ...group,
          keys: group.keys.filter((key) => metadata[key]),
        }))
        .filter((group) => group.keys.length > 0),
    [groups, metadata],
  );
  const [open, setOpen] = useState(false);
  const [activeGroup, setActiveGroup] = useState(
    availableGroups[0]?.id ?? "",
  );
  const [query, setQuery] = useState("");

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matchingKeys = useMemo(() => {
    if (!normalizedQuery) return [];
    return availableGroups.flatMap((group) =>
      group.keys.filter((key) => {
        const meta = metadata[key];
        return [key, meta.label, meta.description, group.label]
          .filter(Boolean)
          .some((value) =>
            String(value).toLocaleLowerCase().includes(normalizedQuery),
          );
      }),
    );
  }, [availableGroups, metadata, normalizedQuery]);

  const selectedGroup =
    availableGroups.find((group) => group.id === activeGroup) ??
    availableGroups[0];
  const visibleKeys = normalizedQuery
    ? [...new Set(matchingKeys)]
    : selectedGroup?.keys ?? [];
  const controlCount = availableGroups.reduce(
    (total, group) => total + group.keys.length,
    0,
  );

  if (controlCount === 0) return null;

  return (
    <section className="surface overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex min-h-[76px] w-full items-center gap-3 px-5 text-left sm:px-6"
      >
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-[#edf3f1] text-[#47645d]">
          <SlidersHorizontal size={17} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-bold text-[#2d3a36]">
            {title ?? t("advanced.title")}
          </span>
          <span className="mt-0.5 block text-xs leading-5 text-[#77847f]">
            {description ?? t("advanced.description")}
          </span>
        </span>
        <span className="hidden rounded-full bg-[#edf3f1] px-2.5 py-1 text-[10px] font-bold text-[#5d6b66] sm:inline">
          {t("advanced.controls", { count: controlCount })}
        </span>
        <ChevronDown
          size={17}
          className={`shrink-0 text-[#7b8883] transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div className="border-t border-[#e5ebe8]">
          <div className="border-b border-[#e8eeeb] bg-[#fafcfb] p-4 sm:p-5">
            <label className="relative block">
              <Search
                size={15}
                className="pointer-events-none absolute left-3.5 top-3.5 text-[#83908b]"
              />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="input-control min-h-11 pl-10 pr-10"
                placeholder={t("advanced.searchPlaceholder")}
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  aria-label={t("advanced.clearSearch")}
                  className="absolute right-2.5 top-2.5 flex h-7 w-7 items-center justify-center rounded-lg text-[#7d8a85] hover:bg-[#e9efec]"
                >
                  <X size={14} />
                </button>
              )}
            </label>
            {!normalizedQuery && (
              <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
                {availableGroups.map((group) => (
                  <button
                    key={group.id}
                    type="button"
                    onClick={() => setActiveGroup(group.id)}
                    className={`min-h-9 shrink-0 rounded-xl px-3 text-xs font-semibold transition ${
                      selectedGroup?.id === group.id
                        ? "bg-[#173f3b] text-white"
                        : "border border-[#dce4e1] bg-white text-[#65726e] hover:border-[#aebfba]"
                    }`}
                  >
                    {group.label}
                    <span className="ml-1.5 opacity-60">{group.keys.length}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="p-5 sm:p-6">
            <div className="mb-5">
              <h3 className="text-base font-bold text-[#2d3a36]">
                {normalizedQuery
                  ? t("advanced.results", { count: visibleKeys.length })
                  : selectedGroup?.label}
              </h3>
              {!normalizedQuery && selectedGroup && (
                <p className="mt-1 text-xs leading-5 text-[#77847f]">
                  {selectedGroup.description}
                </p>
              )}
            </div>
            {visibleKeys.length > 0 ? (
              <div className="parameter-grid">
                {visibleKeys.map((key) => (
                  <ParamField
                    key={key}
                    paramKey={key}
                    meta={metadata[key]}
                    value={values[key] ?? ""}
                    onChange={onChange}
                  />
                ))}
              </div>
            ) : (
              <div className="rounded-2xl border border-dashed border-[#d4ddda] bg-[#fafcfb] px-5 py-8 text-center text-sm text-[#7d8a85]">
                {t("advanced.noResults")}
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
