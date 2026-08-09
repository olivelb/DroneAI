"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import AppProviders from "../components/AppProviders";
import { fetchMissionCatalog } from "../lib/api";
import { useI18n } from "../lib/i18n/provider";
import type { MissionCatalogResponse } from "../lib/types";

const PAGE_SIZE = 20;

function MissionCatalogue() {
  const { locale, t } = useI18n();
  const [offset, setOffset] = useState(0);
  const [catalog, setCatalog] = useState<MissionCatalogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void fetchMissionCatalog(PAGE_SIZE, offset)
        .then((result) => {
          if (!active) return;
          setCatalog(result);
          setError(null);
        })
        .catch((reason) => {
          if (active) setError(reason instanceof Error ? reason.message : String(reason));
        });
    };
    refresh();
    const interval = window.setInterval(refresh, 5_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [offset]);

  const total = catalog?.total ?? 0;
  const items = catalog?.items ?? [];
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + items.length, total);

  return (
    <main className="mx-auto min-h-screen max-w-6xl px-4 py-8 sm:px-6">
      <header className="mb-7 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="eyebrow">DroneAI</div>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-[#17201e]">
            {t("catalog.title")}
          </h1>
          <p className="mt-2 text-sm text-[#66736f]">{t("catalog.description")}</p>
        </div>
        <Link className="primary-button" href="/">
          {t("catalog.backToLaunch")}
        </Link>
      </header>

      {error && <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {!catalog && !error && <div className="surface p-6 text-sm text-[#66736f]">{t("common.waiting")}</div>}
      {catalog && items.length === 0 && (
        <div className="surface p-8 text-center text-sm text-[#77837f]">{t("catalog.empty")}</div>
      )}
      <div className="space-y-3">
        {items.map((mission) => (
          <article key={mission.vol_id} className="surface flex flex-wrap items-center justify-between gap-4 p-5">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-mono text-sm font-bold text-[#25332f]">{mission.vol_id}</h2>
                <span className="rounded-full bg-[#dff5f0] px-2 py-0.5 text-[10px] font-bold uppercase text-[#0f766e]">
                  {mission.overall_status}
                </span>
                {mission.is_stale && <span className="text-xs text-amber-700">delayed</span>}
              </div>
              <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-[#77837f]">
                <span>{mission.current_step ?? "—"} · {mission.progress}%</span>
                <span>{mission.quality_profile ?? mission.pipeline}</span>
                <span>{mission.attempt_count} attempt(s)</span>
                <span>{mission.updated_at ? new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(mission.updated_at)) : "—"}</span>
              </div>
            </div>
            <Link href={`/missions/${encodeURIComponent(mission.vol_id)}`} className="secondary-button">
              {t("catalog.open")}
            </Link>
          </article>
        ))}
      </div>

      {catalog && total > 0 && (
        <footer className="mt-6 flex items-center justify-between gap-3">
          <button className="secondary-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            {t("catalog.previous")}
          </button>
          <span className="text-xs text-[#66736f]">{t("catalog.page", { start, end, total })}</span>
          <button className="secondary-button" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
            {t("catalog.next")}
          </button>
        </footer>
      )}
    </main>
  );
}

export default function MissionsPage() {
  return <AppProviders><MissionCatalogue /></AppProviders>;
}
