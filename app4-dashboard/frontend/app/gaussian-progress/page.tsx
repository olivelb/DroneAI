"use client";

import {
  Activity,
  Boxes,
  CheckCircle2,
  Clock3,
  Gauge,
  ImageIcon,
  RefreshCw,
  Scale,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

type HistoryPoint = {
  iteration: number;
  loss: number | null;
  gaussians: number | null;
};

type RunData = {
  engine: "dronegs" | "lichtfeld";
  status: "waiting" | "running" | "completed" | "error";
  stage: string;
  iteration: number;
  totalIterations: number;
  progress: number;
  loss: number | null;
  gaussians: number | null;
  elapsedSeconds: number;
  etaSeconds: number | null;
  lastActivityAt: string | null;
  metrics: {
    psnr?: number;
    ssim?: number;
    lpips?: number;
    common_psnr?: number | null;
    common_ssim?: number | null;
    common_lpips?: number | null;
    final_gaussians?: number;
    training_seconds?: number;
  } | null;
  timings: {
    training_seconds?: number;
    evaluation_seconds?: number;
    wall_seconds?: number;
  } | null;
  fatalLines: string[];
  history: HistoryPoint[];
};

type ProgressData = {
  updatedAt: string;
  gpu: {
    utilization: number;
    memoryUsed: number;
    memoryTotal: number;
    temperature: number;
  } | null;
  contract: {
    dataset: string;
    images: number;
    trainingImages: number;
    heldOutImages: number;
    iterations: number;
    strategy: string;
    seed: number;
    shSchedule: string;
    maxGaussians: number;
    resize: string;
    evaluator: string;
  };
  dronegs: RunData;
  lichtfeld: RunData;
  commonEvaluation: {
    status: "waiting" | "pending" | "completed";
    psnr: number | null;
    ssim: number | null;
    lpips: number | null;
  };
  preview: { dronegs: boolean; lichtfeld: boolean };
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

function sharedMetrics(run?: RunData) {
  if (!run?.metrics) return { psnr: null, ssim: null, lpips: null };
  if (
    run.metrics.common_psnr != null ||
    run.metrics.common_ssim != null ||
    run.metrics.common_lpips != null
  ) {
    return {
      psnr: run.metrics.common_psnr ?? null,
      ssim: run.metrics.common_ssim ?? null,
      lpips: run.metrics.common_lpips ?? null,
    };
  }
  return {
    psnr: run.metrics.psnr ?? null,
    ssim: run.metrics.ssim ?? null,
    lpips: run.metrics.lpips ?? null,
  };
}

function RunCard({
  run,
  accent,
}: {
  run: RunData;
  accent: "cyan" | "amber";
}) {
  const color = accent === "cyan" ? "text-cyan-300" : "text-amber-300";
  const bar =
    accent === "cyan"
      ? "from-cyan-400 to-emerald-400"
      : "from-amber-400 to-orange-400";
  const maximum = Math.max(
    1,
    ...run.history.map((point) => point.gaussians ?? 0),
  );

  return (
    <article className="rounded-3xl border border-white/10 bg-white/[0.045] p-6 sm:p-7">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className={`font-mono text-xs uppercase tracking-[0.2em] ${color}`}>
            {run.engine === "dronegs" ? "DroneGS dev38" : "LichtFeld v0.5.1"}
          </p>
          <h2 className="mt-2 text-2xl font-semibold">
            {run.status === "completed" ? "15 000 terminées" : run.stage}
          </h2>
        </div>
        <span
          className={`rounded-full border px-3 py-1 text-xs ${
            run.status === "completed"
              ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300"
              : run.status === "error"
                ? "border-rose-400/20 bg-rose-400/10 text-rose-300"
                : "border-white/10 bg-white/5 text-slate-300"
          }`}
        >
          {run.status === "completed"
            ? "Terminé"
            : run.status === "running"
              ? "En cours"
              : run.status === "error"
                ? "Erreur"
                : "En attente"}
        </span>
      </div>

      <div className="mt-7 flex items-end justify-between gap-3">
        <p className="text-4xl font-semibold tabular-nums">
          {number(run.progress, 1)}
          <span className="text-lg text-slate-500"> %</span>
        </p>
        <p className="font-mono text-xs tabular-nums text-slate-500">
          {run.iteration.toLocaleString("fr-FR")} / 15 000
        </p>
      </div>
      <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-800">
        <div
          className={`h-full rounded-full bg-gradient-to-r ${bar} transition-[width] duration-500`}
          style={{ width: `${Math.max(0, run.progress)}%` }}
        />
      </div>

      <div className="mt-6 grid grid-cols-3 gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-wider text-slate-600">
            Loss
          </p>
          <p className="mt-1 font-mono text-sm">{number(run.loss, 5)}</p>
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-wider text-slate-600">
            Gaussiens
          </p>
          <p className="mt-1 font-mono text-sm">
            {run.gaussians?.toLocaleString("fr-FR") ?? "—"}
          </p>
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-wider text-slate-600">
            Temps / ETA
          </p>
          <p className="mt-1 font-mono text-sm">
            {duration(
              run.status === "completed"
                ? (run.timings?.wall_seconds ??
                    run.timings?.training_seconds ??
                    run.elapsedSeconds)
                : run.elapsedSeconds,
            )}
            {run.etaSeconds != null ? ` · ${duration(run.etaSeconds)}` : ""}
          </p>
        </div>
      </div>

      <div className="mt-6">
        <div className="mb-2 flex items-center justify-between text-[11px] uppercase tracking-wider text-slate-600">
          <span>Évolution des splats</span>
          <span>{run.history.length} points</span>
        </div>
        <div className="flex h-14 items-end gap-1 rounded-xl border border-white/[0.06] bg-black/10 px-2 py-2">
          {run.history.length ? (
            run.history.map((point, index) => (
              <div
                key={`${point.iteration}-${index}`}
                className={`min-w-0 flex-1 rounded-sm bg-gradient-to-t ${bar} opacity-80`}
                style={{
                  height: `${Math.max(8, ((point.gaussians ?? 0) / maximum) * 100)}%`,
                }}
                title={`${point.iteration.toLocaleString("fr-FR")} · ${point.gaussians?.toLocaleString("fr-FR") ?? "—"} splats · loss ${number(point.loss, 5)}`}
              />
            ))
          ) : (
            <div className="m-auto text-xs text-slate-600">
              Télémétrie en attente
            </div>
          )}
        </div>
      </div>

      {!!run.fatalLines.length && (
        <p className="mt-4 rounded-xl bg-rose-400/10 p-3 text-xs text-rose-200">
          {run.fatalLines.join(" · ")}
        </p>
      )}
    </article>
  );
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

  const drone = sharedMetrics(data?.dronegs);
  const lichtfeld = sharedMetrics(data?.lichtfeld);
  const deltas = useMemo(
    () => ({
      psnr:
        drone.psnr != null && lichtfeld.psnr != null
          ? drone.psnr - lichtfeld.psnr
          : null,
      ssim:
        drone.ssim != null && lichtfeld.ssim != null
          ? drone.ssim - lichtfeld.ssim
          : null,
      lpips:
        drone.lpips != null && lichtfeld.lpips != null
          ? drone.lpips - lichtfeld.lpips
          : null,
    }),
    [drone.lpips, drone.psnr, drone.ssim, lichtfeld.lpips, lichtfeld.psnr, lichtfeld.ssim],
  );

  return (
    <main className="min-h-screen bg-[#071018] text-slate-100">
      <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8 sm:py-12">
        <header className="mb-7 flex flex-col gap-5 border-b border-white/10 pb-7 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="mb-3 font-mono text-xs uppercase tracking-[0.24em] text-cyan-300">
              Albagnac · comparaison contrôlée
            </p>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-5xl">
              DroneGS vs LichtFeld · 15 000
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-400">
              Même donnée, même split, même budget. Les scores finaux passent
              par le même évaluateur et exactement les mêmes 172 vues tenues à
              l’écart.
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

        {data && (
          <>
            <section className="mb-5 grid gap-5 lg:grid-cols-2">
              <RunCard run={data.dronegs} accent="cyan" />
              <RunCard run={data.lichtfeld} accent="amber" />
            </section>

            <section className="mb-5 rounded-3xl border border-white/10 bg-[#0a1722] p-6 sm:p-7">
              <div className="flex items-center gap-2">
                <Scale size={18} className="text-violet-300" />
                <h2 className="font-medium">Scores comparables</h2>
                <span className="ml-auto rounded-full border border-violet-400/20 bg-violet-400/10 px-3 py-1 text-xs text-violet-200">
                  {data.commonEvaluation.status === "completed"
                    ? "Évaluation commune terminée"
                    : data.commonEvaluation.status === "pending"
                      ? "Évaluation commune à lancer"
                      : "En attente de LichtFeld"}
                </span>
              </div>
              <div className="mt-5 overflow-x-auto">
                <table className="w-full min-w-[680px] text-left text-sm">
                  <thead className="border-b border-white/10 text-xs uppercase tracking-wider text-slate-600">
                    <tr>
                      <th className="pb-3 font-medium">Moteur</th>
                      <th className="pb-3 font-medium">PSNR ↑</th>
                      <th className="pb-3 font-medium">SSIM ↑</th>
                      <th className="pb-3 font-medium">LPIPS ↓</th>
                      <th className="pb-3 font-medium">Splats finaux</th>
                      <th className="pb-3 font-medium">Temps entraînement</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.06]">
                    <tr>
                      <td className="py-4 font-medium text-cyan-300">DroneGS</td>
                      <td className="py-4 font-mono">{number(drone.psnr, 6)}</td>
                      <td className="py-4 font-mono">{number(drone.ssim, 6)}</td>
                      <td className="py-4 font-mono">{number(drone.lpips, 6)}</td>
                      <td className="py-4 font-mono">
                        {data.dronegs.gaussians?.toLocaleString("fr-FR") ?? "—"}
                      </td>
                      <td className="py-4 font-mono">
                        {duration(
                          data.dronegs.timings?.training_seconds ??
                            data.dronegs.timings?.wall_seconds,
                        )}
                      </td>
                    </tr>
                    <tr>
                      <td className="py-4 font-medium text-amber-300">
                        LichtFeld
                      </td>
                      <td className="py-4 font-mono">
                        {number(lichtfeld.psnr, 6)}
                      </td>
                      <td className="py-4 font-mono">
                        {number(lichtfeld.ssim, 6)}
                      </td>
                      <td className="py-4 font-mono">
                        {number(lichtfeld.lpips, 6)}
                      </td>
                      <td className="py-4 font-mono">
                        {data.lichtfeld.gaussians?.toLocaleString("fr-FR") ?? "—"}
                      </td>
                      <td className="py-4 font-mono">
                        {data.lichtfeld.status === "completed"
                          ? duration(
                              data.lichtfeld.timings?.training_seconds ??
                                data.lichtfeld.elapsedSeconds,
                            )
                          : "En cours"}
                      </td>
                    </tr>
                    <tr className="text-violet-200">
                      <td className="py-4 font-medium">Δ DroneGS − LichtFeld</td>
                      <td className="py-4 font-mono">
                        {deltas.psnr == null
                          ? "—"
                          : `${deltas.psnr >= 0 ? "+" : ""}${number(deltas.psnr, 6)}`}
                      </td>
                      <td className="py-4 font-mono">
                        {deltas.ssim == null
                          ? "—"
                          : `${deltas.ssim >= 0 ? "+" : ""}${number(deltas.ssim, 6)}`}
                      </td>
                      <td className="py-4 font-mono">
                        {deltas.lpips == null
                          ? "—"
                          : `${deltas.lpips >= 0 ? "+" : ""}${number(deltas.lpips, 6)}`}
                      </td>
                      <td className="py-4" colSpan={2}>
                        Même plafond : 1 500 000
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p className="mt-4 text-xs leading-5 text-slate-500">
                Les métriques natives LichtFeld ne sont pas utilisées dans ce
                tableau : le PLY final est rendu par le même rasterizer dev38
                que DroneGS, puis PSNR, SSIM et LPIPS/AlexNet sont calculés sur
                les mêmes paires cible/prédiction.
              </p>
            </section>

            <section className="mb-5 grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
              <div className="rounded-3xl border border-white/10 bg-white/[0.035] p-6">
                <div className="mb-5 flex items-center gap-2">
                  <CheckCircle2 size={18} className="text-emerald-300" />
                  <h2 className="font-medium">Contrat de parité</h2>
                </div>
                <div className="grid gap-3 text-sm sm:grid-cols-2">
                  {[
                    ["Donnée", data.contract.dataset],
                    [
                      "Vues",
                      `${data.contract.images} · ${data.contract.trainingImages} train · ${data.contract.heldOutImages} test`,
                    ],
                    ["Budget", `${data.contract.iterations} pas · seed ${data.contract.seed}`],
                    ["Stratégie", data.contract.strategy],
                    ["SH", data.contract.shSchedule],
                    [
                      "Capacité",
                      `${data.contract.maxGaussians.toLocaleString("fr-FR")} splats`,
                    ],
                    ["Images", data.contract.resize],
                    ["Évaluateur", data.contract.evaluator],
                  ].map(([label, value]) => (
                    <div
                      key={label}
                      className="rounded-xl border border-white/[0.06] bg-black/10 p-3"
                    >
                      <p className="text-[11px] uppercase tracking-wider text-slate-600">
                        {label}
                      </p>
                      <p className="mt-1 leading-5 text-slate-300">{value}</p>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-3xl border border-white/10 bg-white/[0.035] p-6">
                <div className="mb-5 flex items-center gap-2">
                  <Gauge size={18} className="text-cyan-300" />
                  <h2 className="font-medium">Machine</h2>
                </div>
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-1">
                  {[
                    {
                      icon: Activity,
                      label: "GPU",
                      value: data.gpu ? `${data.gpu.utilization} %` : "—",
                    },
                    {
                      icon: Boxes,
                      label: "VRAM",
                      value: data.gpu
                        ? `${data.gpu.memoryUsed.toLocaleString("fr-FR")} / ${data.gpu.memoryTotal.toLocaleString("fr-FR")} Mio`
                        : "—",
                    },
                    {
                      icon: Clock3,
                      label: "Dernière mise à jour",
                      value: new Date(data.updatedAt).toLocaleTimeString("fr-FR"),
                    },
                  ].map(({ icon: Icon, label, value }) => (
                    <div key={label} className="flex items-center gap-3">
                      <Icon size={16} className="text-slate-500" />
                      <span className="text-xs text-slate-500">{label}</span>
                      <span className="ml-auto font-mono text-sm">{value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </section>

            <section className="grid gap-5 lg:grid-cols-2">
              {(
                [
                  ["dronegs", "DroneGS", data.preview.dronegs],
                  ["lichtfeld", "LichtFeld", data.preview.lichtfeld],
                ] as const
              ).map(([engine, label, available]) => (
                <div
                  key={engine}
                  className="overflow-hidden rounded-3xl border border-white/10 bg-[#0a1722]"
                >
                  <div className="flex items-center gap-2 border-b border-white/10 px-5 py-4">
                    <ImageIcon size={17} className="text-slate-400" />
                    <h2 className="font-medium">Aperçu {label}</h2>
                  </div>
                  {available ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={`/api/gaussian-progress/image?engine=${engine}&t=${encodeURIComponent(data.updatedAt)}`}
                      alt={`Cible et rendu ${label} sur la même vue Albagnac tenue à l’écart`}
                      className="aspect-video h-auto w-full object-cover"
                    />
                  ) : (
                    <div className="flex aspect-video items-center justify-center px-8 text-center text-sm leading-6 text-slate-500">
                      L’image apparaîtra après l’évaluation commune.
                    </div>
                  )}
                </div>
              ))}
            </section>
          </>
        )}

        {!data && (
          <div className="flex min-h-[50vh] items-center justify-center text-slate-500">
            <Sparkles size={18} className="mr-2 text-cyan-300" />
            Connexion au suivi…
          </div>
        )}

        <footer className="mt-6 flex flex-wrap justify-between gap-3 font-mono text-[11px] text-slate-600">
          <span>Mise à jour automatique toutes les 5 secondes</span>
          <span>Protocole Albagnac parity-15000</span>
        </footer>
      </div>
    </main>
  );
}
