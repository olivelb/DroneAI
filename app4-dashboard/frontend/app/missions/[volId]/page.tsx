"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import React, { useEffect, useState } from "react";
import AppProviders from "../../components/AppProviders";
import { fetchMissionDetail } from "../../lib/api";
import { useI18n } from "../../lib/i18n/provider";
import type { MissionDetail } from "../../lib/types";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="surface p-5"><h2 className="mb-4 text-sm font-bold uppercase tracking-wide text-[#53615c]">{title}</h2>{children}</section>;
}

function MissionDetailView() {
  const { t } = useI18n();
  const params = useParams<{ volId: string }>();
  const volId = decodeURIComponent(params.volId);
  const [mission, setMission] = useState<MissionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void fetchMissionDetail(volId)
      .then((result) => active && setMission(result))
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : String(reason)));
    return () => { active = false; };
  }, [volId]);

  return (
    <main className="mx-auto min-h-screen max-w-7xl px-4 py-8 sm:px-6">
      <Link href="/missions" className="text-sm font-semibold text-[#0f766e]">← {t("detail.back")}</Link>
      {error && <div className="mt-6 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {!mission && !error && <div className="mt-6 surface p-6 text-sm text-[#66736f]">{t("common.waiting")}</div>}
      {mission && (
        <>
          <header className="my-6 flex flex-wrap items-center justify-between gap-3">
            <div><div className="eyebrow">Mission</div><h1 className="mt-2 font-mono text-2xl font-bold text-[#17201e]">{mission.vol_id}</h1></div>
            <div className="flex items-center gap-3"><span className="rounded-full bg-[#dff5f0] px-3 py-1 text-xs font-bold uppercase text-[#0f766e]">{mission.overall_status}</span><span className="text-sm font-semibold text-[#53615c]">{mission.progress}%</span></div>
          </header>
          <div className="grid gap-4 lg:grid-cols-2">
            <Section title={t("detail.heartbeat")}><pre className="overflow-auto text-xs text-[#53615c]">{JSON.stringify(mission.heartbeat, null, 2)}</pre></Section>
            <Section title={t("detail.attempts")}><pre className="overflow-auto text-xs text-[#53615c]">{JSON.stringify(mission.attempts, null, 2)}</pre></Section>
            <Section title={t("detail.phases")}><pre className="max-h-96 overflow-auto text-xs text-[#53615c]">{JSON.stringify({ stage_runs: mission.stage_runs ?? [], compatibility: mission.phases }, null, 2)}</pre></Section>
            <Section title={t("detail.products")}>
              {mission.products.length === 0 ? <p className="text-sm text-[#77837f]">{t("detail.noProducts")}</p> : mission.products.map((product, index) => <div key={`${product.kind}-${product.run_id ?? index}`} className="border-b border-[#e7ecea] py-2 text-xs last:border-0"><strong>{product.kind}</strong> · {product.name ?? product.s3_key ?? product.status}</div>)}
            </Section>
            <Section title={t("detail.parameters")}><pre className="max-h-[32rem] overflow-auto text-xs text-[#53615c]">{JSON.stringify(mission.parameters, null, 2)}</pre></Section>
            <Section title={t("detail.logs")}>
              {mission.logs.length === 0 ? <p className="text-sm text-[#77837f]">{t("detail.noLogs")}</p> : <div className="max-h-[32rem] overflow-auto font-mono text-[11px] leading-5">{mission.logs.map((log, index) => <div key={`${log.created_at}-${index}`} className="border-b border-[#edf1ef] py-2"><span className="text-[#0f766e]">{log.service ?? "SYSTEM"}</span> {log.step ?? ""} · {log.message ?? log.status ?? ""}</div>)}</div>}
            </Section>
          </div>
        </>
      )}
    </main>
  );
}

export default function MissionDetailPage() {
  return <AppProviders><MissionDetailView /></AppProviders>;
}
