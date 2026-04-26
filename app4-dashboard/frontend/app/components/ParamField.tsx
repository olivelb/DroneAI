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
    const checked = Boolean(value);
    return (
      <button
        onClick={() => onChange(paramKey, !checked)}
        className={`flex items-center justify-between rounded-xl border px-4 py-3 text-left transition ${
          checked ? "border-blue-400/40 bg-blue-500/5" : "border-gray-200 bg-white"
        }`}
      >
        <span className="text-sm font-medium text-gray-700">{meta.label}</span>
        <span className={`rounded-full px-3 py-0.5 text-xs font-semibold ${
          checked ? "bg-blue-500 text-white" : "bg-gray-100 text-gray-400"
        }`}>
          {checked ? "On" : "Off"}
        </span>
      </button>
    );
  }

  if (meta.type === "select") {
    return (
      <label className="block">
        <span className="mb-1 block text-sm font-medium text-gray-600">{meta.label}</span>
        <select
          value={String(value)}
          onChange={(e) => onChange(paramKey, e.target.value)}
          className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400/30"
        >
          {meta.options?.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </label>
    );
  }

  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-gray-600">{meta.label}</span>
      <input
        type={meta.type === "text" ? "text" : "number"}
        min={meta.min}
        max={meta.max}
        step={meta.step}
        value={String(value)}
        onChange={(e) => onChange(paramKey, e.target.value)}
        className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400/30"
      />
    </label>
  );
}
