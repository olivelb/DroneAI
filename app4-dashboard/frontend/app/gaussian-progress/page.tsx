"use client";

import { useEffect, useState } from "react";

type Progress = {
  generatedAt: string;
  scene: string;
  status: "running" | "completed" | "failed" | "idle";
  iteration: number;
  iterations: number;
  loss: number | null;
  gaussians: number | null;
  checkpoint: { iteration: number; path: string } | null;
  evaluation: {
    view: number | null;
    views: number | null;
    psnr: number | null;
    ssim: number | null;
    lpips: number | null;
  };
  timings: Record<string, number> | null;
  canary: { status?: string } | null;
  fatal: string[];
  preview: boolean;
};

const number = (value: number | null, digits = 4) =>
  value == null ? "—" : value.toFixed(digits);

export default function GaussianProgressPage() {
  const [data, setData] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const response = await fetch("/api/gaussian-progress", {
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const next = (await response.json()) as Progress;
        if (active) {
          setData(next);
          setError(null);
        }
      } catch (reason) {
        if (active) setError(String(reason));
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const percent = data
    ? Math.min(100, (100 * data.iteration) / Math.max(1, data.iterations))
    : 0;

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-10 text-slate-100">
      <div className="mx-auto max-w-5xl space-y-6">
        <header>
          <p className="text-sm uppercase tracking-[0.25em] text-cyan-400">
            DroneGS · canary de production
          </p>
          <h1 className="mt-2 text-3xl font-semibold">
            {data?.scene ?? "Chargement…"}
          </h1>
          <p className="mt-2 text-slate-400">
            Checkpoint atomique, split held-out et évaluation commune.
          </p>
        </header>

        <section className="rounded-2xl border border-slate-800 bg-slate-900 p-6">
          <div className="flex items-center justify-between">
            <span className="font-medium capitalize">{data?.status ?? "…"}</span>
            <span className="font-mono">{percent.toFixed(1)} %</span>
          </div>
          <div className="mt-4 h-3 overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-cyan-400 transition-all"
              style={{ width: `${percent}%` }}
            />
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-4">
            <Metric label="Itération" value={`${data?.iteration ?? 0} / ${data?.iterations ?? 0}`} />
            <Metric label="Loss" value={number(data?.loss ?? null, 6)} />
            <Metric label="Gaussiennes" value={data?.gaussians?.toLocaleString("fr-FR") ?? "—"} />
            <Metric label="Checkpoint" value={data?.checkpoint ? `iter ${data.checkpoint.iteration}` : "—"} />
          </div>
        </section>

        <section className="grid gap-4 sm:grid-cols-3">
          <Metric label="PSNR held-out" value={number(data?.evaluation.psnr ?? null, 6)} large />
          <Metric label="SSIM held-out" value={number(data?.evaluation.ssim ?? null, 6)} large />
          <Metric label="LPIPS" value={number(data?.evaluation.lpips ?? null, 6)} large />
        </section>

        {data?.preview && (
          <img
            className="w-full rounded-2xl border border-slate-800"
            src={`/api/gaussian-progress/image?t=${encodeURIComponent(data.generatedAt)}`}
            alt="Aperçu DroneGS Savères"
          />
        )}
        {(error || data?.fatal.length) && (
          <pre className="overflow-auto rounded-xl border border-red-900 bg-red-950/40 p-4 text-sm text-red-200">
            {error ?? data?.fatal.join("\n")}
          </pre>
        )}
      </div>
    </main>
  );
}

function Metric({
  label,
  value,
  large = false,
}: {
  label: string;
  value: string;
  large?: boolean;
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <p className="text-xs uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-2 font-mono ${large ? "text-2xl" : "text-lg"}`}>
        {value}
      </p>
    </div>
  );
}
