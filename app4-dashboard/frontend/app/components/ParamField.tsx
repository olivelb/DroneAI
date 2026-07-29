"use client";

import React from "react";
import type { ParameterMeta, ParamValue } from "../lib/types";

export function ParamField({
  paramKey, meta, value, onChange,
}: {
  paramKey: string;
  meta: ParameterMeta;
  value: ParamValue;
  onChange: (key: string, val: ParamValue) => void;
}) {
  if (meta.type === "bool") {
    const checked =
      value === true || String(value).trim().toLowerCase() === "true";
    return (
      <button
        type="button"
        aria-pressed={checked}
        onClick={() => onChange(paramKey, !checked)}
        className={`flex min-h-[74px] items-center justify-between gap-4 rounded-2xl border px-4 py-3 text-left transition ${
          checked
            ? "border-[#83cfc1] bg-[#edf9f6]"
            : "border-[#dce4e1] bg-white hover:border-[#bdcbc6]"
        }`}
      >
        <span>
          <span className="block text-sm font-semibold text-[#34413d]">
            {meta.label}
          </span>
          {meta.description && (
            <span className="mt-1 block text-[11px] leading-4 text-[#7a8783]">
              {meta.description}
            </span>
          )}
        </span>
        <span
          className={`relative h-6 w-11 shrink-0 rounded-full transition ${
            checked ? "bg-[#0f766e]" : "bg-[#ced8d4]"
          }`}
        >
          <span
            className={`absolute top-1 h-4 w-4 rounded-full bg-white shadow-sm transition ${
              checked ? "left-6" : "left-1"
            }`}
          />
        </span>
      </button>
    );
  }

  if (meta.type === "select") {
    return (
      <label className="block">
        <span className="mb-1.5 block text-xs font-semibold text-[#4d5a56]">
          {meta.label}
        </span>
        <select
          value={String(value)}
          onChange={(e) => onChange(paramKey, e.target.value)}
          className="input-control min-h-11"
        >
          {meta.options?.map((option) => {
            const value =
              typeof option === "string" ? option : option.value;
            const label =
              typeof option === "string" ? option : option.label;
            return (
              <option key={value} value={value}>
                {label}
              </option>
            );
          })}
        </select>
        {meta.description && (
          <span className="mt-1.5 block text-[11px] leading-4 text-[#7a8783]">
            {meta.description}
          </span>
        )}
      </label>
    );
  }

  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold text-[#4d5a56]">
        {meta.label}
      </span>
      <input
        type={meta.type === "text" ? "text" : "number"}
        min={meta.min}
        max={meta.max}
        step={meta.step}
        value={String(value)}
        onChange={(e) => onChange(paramKey, e.target.value)}
        className="input-control min-h-11"
      />
      {meta.description && (
        <span className="mt-1.5 block text-[11px] leading-4 text-[#7a8783]">
          {meta.description}
        </span>
      )}
    </label>
  );
}
