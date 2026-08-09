"use client";

import {
  ArrowLeft,
  Boxes,
  Clock3,
  Home,
  ListTree,
  PackageCheck,
  SlidersHorizontal,
  Terminal,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import React, { useEffect, useState } from "react";
import AppProviders from "../../components/AppProviders";
import MissionStageProgress from "../../components/MissionStageProgress";
import { fetchMissionDetail } from "../../lib/api";
import { useI18n } from "../../lib/i18n/provider";
import type { MissionDetail } from "../../lib/types";

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="surface p-5">
      <h2 className="mb-4 flex items-center gap-2 text-sm font-bold text-[#40504b]">
        <span className="text-[#0f766e]">{icon}</span>
        {title}
      </h2>
      {children}
    </section>
  );
}

const humanSize = (bytes?: number | null) => {
  if (bytes === undefined || bytes === null) return null;
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KiB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
};

function MissionDetailView() {
  const { t } = useI18n();
  const params = useParams<{ volId: string }>();
  const volId = decodeURIComponent(params.volId);
  const [mission, setMission] = useState<MissionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void fetchMissionDetail(volId)
        .then((result) => {
          if (!active) return;
          setMission(result);
          setError(null);
        })
        .catch((reason) => {
          if (active) {
            setError(reason instanceof Error ? reason.message : String(reason));
          }
        });
    };
    refresh();
    const interval = window.setInterval(refresh, 3_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [volId]);

  return (
    <main className="mx-auto min-h-screen max-w-[1500px] px-4 py-6 sm:px-6">
      <nav className="flex flex-wrap items-center gap-2 text-xs font-semibold">
        <Link
          href={`/?mission=${encodeURIComponent(volId)}`}
          className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-[#dce5e1] bg-white px-3 text-[#34413d] transition hover:border-[#9ebbb2]"
        >
          <Home size={14} /> {t("detail.home")}
        </Link>
        <Link
          href="/missions"
          className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-[#dce5e1] bg-white px-3 text-[#34413d] transition hover:border-[#9ebbb2]"
        >
          <ArrowLeft size={14} /> {t("detail.back")}
        </Link>
      </nav>

      {error && (
        <div className="mt-6 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}
      {!mission && !error && (
        <div className="mt-6 surface p-6 text-sm text-[#66736f]">
          {t("common.waiting")}
        </div>
      )}
      {mission && (
        <>
          <header className="my-6 overflow-hidden rounded-[1.75rem] border border-[#d9e4e0] bg-[linear-gradient(135deg,#173f3b_0%,#285f57_100%)] p-6 text-white shadow-[0_20px_60px_rgba(23,63,59,0.18)]">
            <div className="flex flex-wrap items-start justify-between gap-5">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.16em] text-[#9ee1d4]">
                  <Boxes size={14} /> DroneAI · {t("detail.overview")}
                </div>
                <h1 className="mt-3 break-all font-mono text-2xl font-bold sm:text-3xl">
                  {mission.vol_id}
                </h1>
                <p className="mt-2 text-sm text-white/65">
                  {mission.current_step ?? t("common.waiting")}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <span className="rounded-full bg-white/12 px-3 py-1.5 text-xs font-bold uppercase tracking-wide text-white">
                  {mission.overall_status}
                </span>
                <span className="font-mono text-2xl font-bold text-[#9ee1d4]">
                  {mission.progress}%
                </span>
              </div>
            </div>
            <div className="mt-6 h-2 overflow-hidden rounded-full bg-white/12">
              <div
                className="h-full rounded-full bg-[#8ce0d1] transition-all duration-500"
                style={{ width: `${mission.progress}%` }}
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-white/60">
              <span>{mission.quality_profile ?? mission.pipeline}</span>
              <span>{mission.attempt_count} attempt(s)</span>
              <span>
                {t("monitor.lastUpdate", {
                  seconds: Math.max(
                    0,
                    Math.round(mission.last_event_age_seconds ?? 0),
                  ),
                })}
              </span>
            </div>
          </header>

          <section className="surface p-5">
            <h2 className="mb-4 flex items-center gap-2 text-sm font-bold text-[#40504b]">
              <ListTree size={17} className="text-[#0f766e]" />
              {t("monitor.progress")}
            </h2>
            <MissionStageProgress runs={mission.stage_runs ?? []} />
          </section>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Section title={t("detail.products")} icon={<PackageCheck size={17} />}>
              {mission.products.length === 0 ? (
                <p className="text-sm text-[#77837f]">{t("detail.noProducts")}</p>
              ) : (
                <div className="space-y-2">
                  {mission.products.map((product, index) => (
                    <article
                      key={`${product.artifact_id ?? product.run_id ?? product.kind}-${index}`}
                      className="rounded-xl border border-[#e1e8e5] bg-[#fafcfb] p-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <strong className="text-xs text-[#34413d]">{product.kind}</strong>
                        {humanSize(product.size_bytes) && (
                          <span className="text-[10px] font-semibold text-[#7a8783]">
                            {humanSize(product.size_bytes)}
                          </span>
                        )}
                      </div>
                      <p className="mt-1 break-all font-mono text-[10px] leading-4 text-[#687571]">
                        {product.s3_key ?? product.status}
                      </p>
                      {product.checksum_sha256 && (
                        <p className="mt-1 font-mono text-[9px] text-[#8b9692]">
                          sha256:{product.checksum_sha256}
                        </p>
                      )}
                    </article>
                  ))}
                </div>
              )}
            </Section>

            <Section title={t("detail.logs")} icon={<Terminal size={17} />}>
              {mission.logs.length === 0 ? (
                <p className="text-sm text-[#77837f]">{t("detail.noLogs")}</p>
              ) : (
                <div className="max-h-[32rem] space-y-2 overflow-auto">
                  {mission.logs.map((log, index) => (
                    <article
                      key={`${log.created_at}-${index}`}
                      className="rounded-xl border border-[#e4ebe8] bg-[#fafcfb] p-3"
                    >
                      <div className="flex items-center justify-between gap-3 text-[10px]">
                        <strong className="text-[#0f766e]">
                          {log.service ?? "SYSTEM"} · {log.step ?? ""}
                        </strong>
                        <span className="text-[#8b9692]">
                          {log.created_at
                            ? new Date(log.created_at).toLocaleTimeString()
                            : ""}
                        </span>
                      </div>
                      <p className="mt-1 text-xs leading-5 text-[#53615c]">
                        {log.message ?? log.status ?? ""}
                      </p>
                    </article>
                  ))}
                </div>
              )}
            </Section>
          </div>

          <details className="surface mt-4 overflow-hidden">
            <summary className="flex cursor-pointer list-none items-center gap-2 p-5 text-sm font-bold text-[#40504b]">
              <SlidersHorizontal size={17} className="text-[#0f766e]" />
              {t("detail.technical")}
            </summary>
            <div className="grid gap-4 border-t border-[#e3eae7] p-5 lg:grid-cols-2">
              <div>
                <h3 className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-[#65726e]">
                  <Clock3 size={14} /> {t("detail.heartbeat")}
                </h3>
                <pre className="max-h-56 overflow-auto rounded-xl bg-[#f5f8f7] p-3 text-[10px] leading-5 text-[#53615c]">
                  {JSON.stringify(mission.heartbeat, null, 2)}
                </pre>
              </div>
              <div>
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-[#65726e]">
                  {t("detail.parameters")}
                </h3>
                <pre className="max-h-56 overflow-auto rounded-xl bg-[#f5f8f7] p-3 text-[10px] leading-5 text-[#53615c]">
                  {JSON.stringify(mission.parameters, null, 2)}
                </pre>
              </div>
              <div className="lg:col-span-2">
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-[#65726e]">
                  {t("monitor.technical")}
                </h3>
                <pre className="max-h-[36rem] overflow-auto rounded-xl bg-[#18221f] p-4 text-[10px] leading-5 text-[#b8c8c3]">
                  {JSON.stringify(
                    {
                      attempts: mission.attempts,
                      stage_runs: mission.stage_runs ?? [],
                      compatibility: mission.phases,
                    },
                    null,
                    2,
                  )}
                </pre>
              </div>
            </div>
          </details>
        </>
      )}
    </main>
  );
}

export default function MissionDetailPage() {
  return (
    <AppProviders>
      <MissionDetailView />
    </AppProviders>
  );
}
