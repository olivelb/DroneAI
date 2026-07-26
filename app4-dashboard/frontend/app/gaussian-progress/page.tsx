"use client";

import {
  Activity,
  Boxes,
  Clock3,
  Gauge,
  ImageIcon,
  RefreshCw,
  Sparkles,
  Thermometer,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

type ProgressData = {
  status: "waiting" | "running" | "finalizing" | "completed" | "error";
  stage: string;
  progress: number;
  iteration: number;
  totalIterations: number;
  loss?: number | null;
  gaussians?: number | null;
  elapsedSeconds?: number;
  etaSeconds?: number | null;
  updatedAt: string;
  gpu?: {
    utilization: number;
    memoryUsed: number;
    memoryTotal: number;
    temperature: number;
  } | null;
  metrics?: {
    psnr?: number;
    ssim?: number;
    lpips?: number;
    final_loss?: number;
    final_gaussians?: number;
  } | null;
  timings?: {
    training_seconds?: number;
    evaluation_seconds?: number;
    wall_seconds?: number;
  } | null;
  baseline?: { label: string; psnr: number; ssim: number };
  hasPreview?: boolean;
  fatalLines?: string[];
  recentEvents?: Array<Record<string, unknown>>;
};

function duration(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const secs = rounded % 60;
  return hours > 0
    ? `${hours} h ${minutes.toString().padStart(2, "0")}`
    : `${minutes} min ${secs.toString().padStart(2, "0")} s`;
}

function number(value?: number | null, digits = 3) {
  return value == null || !Number.isFinite(value)
    ? "—"
    : value.toLocaleString("fr-FR", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
}

export default function GaussianProgressPage() {
  const [data, setData] = useState<ProgressData | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const response = await fetch("/api/gaussian-progress", {
        cache: "no-store",
      });
      setData((await response.json()) as ProgressData);
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const tone = useMemo(() => {
    if (data?.status === "completed") return "text-emerald-300";
    if (data?.status === "error") return "text-rose-300";
    if (data?.status === "finalizing") return "text-violet-300";
    return "text-cyan-300";
  }, [data?.status]);
  const finalPsnr = data?.metrics?.psnr;
  const finalSsim = data?.metrics?.ssim;
  const psnrDelta =
    finalPsnr != null && data?.baseline
      ? finalPsnr - data.baseline.psnr
      : null;
  const ssimDelta =
    finalSsim != null && data?.baseline
      ? finalSsim - data.baseline.ssim
      : null;

  return (
    <main className="min-h-screen bg-[#071018] text-slate-100">
      <div className="mx-auto max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <header className="mb-8 flex flex-col gap-5 border-b border-white/10 pb-7 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="mb-3 font-mono text-xs uppercase tracking-[0.24em] text-cyan-300">
              DroneGS dev38 · Albagnac Mavic 3E RTK
            </p>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-5xl">
              Entraînement 15 000
            </h1>
            <p className={`mt-3 text-sm font-medium ${tone}`}>
              {data?.stage ?? "Connexion au suivi…"}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            className="inline-flex w-fit items-center gap-2 rounded-full border border-white/10 bg-white/5 px-4 py-2 text-xs text-slate-300 hover:bg-white/10"
          >
            <RefreshCw
              size={14}
              className={refreshing ? "animate-spin" : ""}
            />
            Actualiser
          </button>
        </header>

        <section className="mb-5 rounded-3xl border border-white/10 bg-white/[0.045] p-6 sm:p-8">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-[0.16em] text-slate-500">
                Progression
              </p>
              <p className="mt-2 text-5xl font-semibold tabular-nums">
                {number(data?.progress, 1)}
                <span className="text-xl text-slate-500"> %</span>
              </p>
            </div>
            <p className="text-right font-mono text-sm tabular-nums text-slate-400">
              {(data?.iteration ?? 0).toLocaleString("fr-FR")} /{" "}
              {(data?.totalIterations ?? 15_000).toLocaleString("fr-FR")}
            </p>
          </div>
          <div className="mt-5 h-3 overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-gradient-to-r from-cyan-400 via-sky-400 to-emerald-400 transition-[width] duration-500"
              style={{ width: `${Math.max(0, data?.progress ?? 0)}%` }}
            />
          </div>
        </section>

        <section className="mb-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            {
              icon: Activity,
              label: "Loss courante",
              value: number(data?.loss, 6),
            },
            {
              icon: Boxes,
              label: "Gaussiens",
              value: data?.gaussians?.toLocaleString("fr-FR") ?? "—",
            },
            {
              icon: Clock3,
              label: "Écoulé / restant",
              value: `${duration(data?.elapsedSeconds)} · ${duration(data?.etaSeconds)}`,
            },
            {
              icon: Gauge,
              label: "GPU / VRAM",
              value: data?.gpu
                ? `${data.gpu.utilization} % · ${data.gpu.memoryUsed.toLocaleString("fr-FR")} Mio`
                : "—",
            },
          ].map(({ icon: Icon, label, value }) => (
            <div
              key={label}
              className="rounded-2xl border border-white/10 bg-white/[0.035] p-5"
            >
              <Icon size={18} className="mb-5 text-cyan-300" />
              <p className="text-xs uppercase tracking-[0.14em] text-slate-500">
                {label}
              </p>
              <p className="mt-2 text-lg font-medium tabular-nums">{value}</p>
            </div>
          ))}
        </section>

        <section className="grid gap-5 lg:grid-cols-[1fr_0.9fr]">
          <div className="rounded-3xl border border-white/10 bg-[#0a1722] p-6">
            <div className="mb-5 flex items-center gap-2">
              <Sparkles size={18} className="text-cyan-300" />
              <h2 className="font-medium">Qualité finale</h2>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              {[
                {
                  label: "PSNR",
                  value: number(finalPsnr, 5),
                  delta:
                    psnrDelta == null
                      ? null
                      : `${psnrDelta >= 0 ? "+" : ""}${number(psnrDelta, 5)} dB`,
                },
                {
                  label: "SSIM",
                  value: number(finalSsim, 6),
                  delta:
                    ssimDelta == null
                      ? null
                      : `${ssimDelta >= 0 ? "+" : ""}${number(ssimDelta, 6)}`,
                },
                {
                  label: "LPIPS",
                  value: number(data?.metrics?.lpips, 6),
                  delta: null,
                },
              ].map((metric) => (
                <div
                  key={metric.label}
                  className="rounded-2xl border border-white/10 bg-black/10 p-4"
                >
                  <p className="text-xs text-slate-500">{metric.label}</p>
                  <p className="mt-2 text-2xl font-semibold tabular-nums">
                    {metric.value}
                  </p>
                  <p className="mt-1 text-xs text-emerald-300">
                    {metric.delta ?? "En attente"}
                  </p>
                </div>
              ))}
            </div>
            <p className="mt-4 text-xs leading-5 text-slate-500">
              Référence : {data?.baseline?.label ?? "PLY LichtFeld"} ·{" "}
              {number(data?.baseline?.psnr, 5)} dB /{" "}
              {number(data?.baseline?.ssim, 6)} SSIM
            </p>
            <div className="mt-5 flex items-center gap-2 text-xs text-slate-500">
              <Thermometer size={14} />
              {data?.gpu
                ? `GPU ${data.gpu.temperature} °C · VRAM ${data.gpu.memoryUsed.toLocaleString("fr-FR")} / ${data.gpu.memoryTotal.toLocaleString("fr-FR")} Mio`
                : "Télémétrie GPU en attente"}
            </div>
          </div>

          <div className="overflow-hidden rounded-3xl border border-white/10 bg-[#0a1722]">
            <div className="flex items-center gap-2 border-b border-white/10 px-5 py-4">
              <ImageIcon size={17} className="text-cyan-300" />
              <h2 className="font-medium">Aperçu final</h2>
            </div>
            {data?.hasPreview ? (
              // The timestamp bypasses browser cache when the final image lands.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`/api/gaussian-progress/image?t=${encodeURIComponent(data.updatedAt)}`}
                alt="Comparaison cible et rendu DroneGS sur une vue Albagnac tenue à l’écart"
                className="aspect-video h-auto w-full object-cover"
              />
            ) : (
              <div className="flex aspect-video items-center justify-center px-8 text-center text-sm leading-6 text-slate-500">
                L’image apparaîtra après l’évaluation finale.
              </div>
            )}
          </div>
        </section>

        {!!data?.fatalLines?.length && (
          <section className="mt-5 rounded-2xl border border-rose-400/20 bg-rose-400/10 p-5 text-sm text-rose-200">
            {data.fatalLines.join(" · ")}
          </section>
        )}

        <footer className="mt-6 flex flex-wrap justify-between gap-3 font-mono text-[11px] text-slate-600">
          <span>Mise à jour automatique toutes les 5 secondes</span>
          <span>
            {data?.updatedAt
              ? new Date(data.updatedAt).toLocaleTimeString("fr-FR")
              : "—"}
          </span>
        </footer>
      </div>
    </main>
  );
}
