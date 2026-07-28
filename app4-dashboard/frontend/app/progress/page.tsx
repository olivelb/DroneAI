"use client";

import {
  Activity,
  Camera,
  CheckCircle2,
  Clock3,
  Cpu,
  Database,
  RefreshCw,
  Server,
  TriangleAlert,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

type ProgressData = {
  status: "running" | "completed" | "stalled" | "error" | "unavailable";
  stage:
    | "preparation"
    | "extraction"
    | "matching"
    | "mapping"
    | "alignment"
    | "undistortion"
    | "completed";
  progress: number;
  totalImages: number;
  extracted: number;
  matched: number;
  registered: number;
  undistorted: number;
  alignmentSucceeded: boolean;
  completedModels: number;
  elapsedSeconds: number;
  lastActivityAt: string;
  updatedAt: string;
  gpu: {
    utilization: number;
    memoryUsed: number;
    memoryTotal: number;
    temperature: number;
  } | null;
  events: string[];
  fatalLines: string[];
  message?: string;
};

const stages = [
  { id: "extraction", label: "Extraction SIFT" },
  { id: "matching", label: "Matching spatial" },
  { id: "mapping", label: "Mapping & BA" },
  { id: "alignment", label: "Alignement RTK" },
  { id: "undistortion", label: "Undistortion" },
  { id: "completed", label: "Prêt pour DroneGS" },
] as const;

const stageOrder = {
  preparation: -1,
  extraction: 0,
  matching: 1,
  mapping: 2,
  alignment: 3,
  undistortion: 4,
  completed: 5,
};

function formatDuration(seconds: number) {
  const totalMinutes = Math.floor(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours} h ${minutes.toString().padStart(2, "0")}` : `${minutes} min`;
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

export default function ProgressPage() {
  const [data, setData] = useState<ProgressData | null>(null);
  const [loading, setLoading] = useState(true);
  const [secondsUntilRefresh, setSecondsUntilRefresh] = useState(60);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/progress", { cache: "no-store" });
      const payload = (await response.json()) as ProgressData;
      setData(payload);
      setSecondsUntilRefresh(60);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initialRefresh = window.setTimeout(() => void refresh(), 0);
    const refreshTimer = window.setInterval(() => void refresh(), 60_000);
    const countdownTimer = window.setInterval(
      () => setSecondsUntilRefresh((value) => (value <= 1 ? 60 : value - 1)),
      1_000,
    );
    return () => {
      window.clearTimeout(initialRefresh);
      window.clearInterval(refreshTimer);
      window.clearInterval(countdownTimer);
    };
  }, [refresh]);

  const currentStage = data ? stageOrder[data.stage] : -1;
  const statusTone = useMemo(() => {
    if (data?.status === "completed") return "text-emerald-300 bg-emerald-400/10 border-emerald-400/20";
    if (data?.status === "error") return "text-rose-300 bg-rose-400/10 border-rose-400/20";
    if (data?.status === "stalled") return "text-amber-300 bg-amber-400/10 border-amber-400/20";
    return "text-cyan-300 bg-cyan-400/10 border-cyan-400/20";
  }, [data?.status]);

  return (
    <main className="min-h-screen bg-[#07111f] text-slate-100">
      <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8 sm:py-12">
        <header className="mb-10 flex flex-col gap-6 border-b border-white/10 pb-8 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="mb-3 flex items-center gap-2 font-mono text-xs uppercase tracking-[0.24em] text-cyan-300">
              <span className="h-2 w-2 animate-pulse rounded-full bg-cyan-300" />
              DroneAI · Albagnac Mavic 3E RTK
            </div>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-5xl">
              Préparation DroneGS
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400 sm:text-base">
              Suivi minute par minute de la reconstruction, de l’alignement RTK et de l’undistortion des 1 376 prises de vue.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className={`rounded-full border px-3 py-1.5 text-xs font-medium ${statusTone}`}>
              {data?.status === "completed"
                ? "Terminé"
                : data?.status === "stalled"
                  ? "Activité ralentie"
                  : data?.status === "error"
                    ? "Attention requise"
                    : "Calcul actif"}
            </span>
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-white/10 disabled:opacity-50"
              aria-label="Actualiser maintenant"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              {secondsUntilRefresh}s
            </button>
          </div>
        </header>

        <section className="mb-8 rounded-3xl border border-white/10 bg-white/[0.045] p-6 shadow-2xl shadow-black/20 sm:p-8">
          <div className="mb-5 flex items-end justify-between gap-4">
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.2em] text-slate-500">
                Avancement global estimé
              </p>
              <p className="mt-2 text-4xl font-semibold tabular-nums sm:text-6xl">
                {data ? data.progress.toFixed(1) : "—"}<span className="text-xl text-slate-500">%</span>
              </p>
            </div>
            <div className="text-right">
              <p className="text-sm text-slate-500">Phase courante</p>
              <p className="mt-1 font-medium text-cyan-300">
                {data?.stage === "mapping"
                  ? "Mapping & bundle adjustment"
                  : data?.stage === "alignment"
                    ? "Alignement RTK"
                    : data?.stage === "undistortion"
                      ? "Undistortion des images"
                  : data?.stage === "matching"
                    ? "Matching spatial"
                    : data?.stage === "extraction"
                      ? "Extraction SIFT"
                      : data?.stage === "completed"
                        ? "Validation terminée"
                        : "Préparation"}
              </p>
            </div>
          </div>
          <div className="h-3 overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-gradient-to-r from-cyan-400 via-sky-400 to-emerald-400 transition-[width] duration-700"
              style={{ width: `${data?.progress ?? 0}%` }}
            />
          </div>
          <div className="mt-6 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {stages.map((stage, index) => {
              const complete = currentStage > index || data?.status === "completed";
              const active = currentStage === index && data?.status !== "completed";
              return (
                <div
                  key={stage.id}
                  className={`rounded-2xl border px-4 py-3 ${
                    active
                      ? "border-cyan-400/30 bg-cyan-400/10"
                      : complete
                        ? "border-emerald-400/20 bg-emerald-400/[0.07]"
                        : "border-white/5 bg-black/10"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    {complete ? (
                      <CheckCircle2 size={15} className="text-emerald-300" />
                    ) : (
                      <span className={`h-2 w-2 rounded-full ${active ? "animate-pulse bg-cyan-300" : "bg-slate-700"}`} />
                    )}
                    <span className={`text-xs ${active ? "text-cyan-200" : complete ? "text-emerald-200" : "text-slate-600"}`}>
                      {stage.label}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="mb-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { icon: Camera, label: "Caméras enregistrées", value: data ? `${data.registered.toLocaleString("fr-FR")} / ${data.totalImages.toLocaleString("fr-FR")}` : "—" },
            { icon: Clock3, label: "Temps écoulé", value: data ? formatDuration(data.elapsedSeconds) : "—" },
            { icon: Cpu, label: "GPU / VRAM", value: data?.gpu ? `${data.gpu.utilization}% · ${data.gpu.memoryUsed} Mio` : "Indisponible" },
            { icon: Database, label: "Images undistordues", value: data ? `${data.undistorted.toLocaleString("fr-FR")} / ${data.totalImages.toLocaleString("fr-FR")}` : "—" },
          ].map(({ icon: Icon, label, value }) => (
            <div key={label} className="rounded-2xl border border-white/10 bg-white/[0.035] p-5">
              <Icon size={18} className="mb-5 text-cyan-300" />
              <p className="text-xs uppercase tracking-[0.14em] text-slate-500">{label}</p>
              <p className="mt-2 text-xl font-medium tabular-nums text-slate-100">{value}</p>
            </div>
          ))}
        </section>

        <section className="grid gap-6 lg:grid-cols-[1.4fr_0.6fr]">
          <div className="rounded-3xl border border-white/10 bg-[#091626] p-6">
            <div className="mb-5 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Activity size={17} className="text-cyan-300" />
                <h2 className="font-medium">Événements récents</h2>
              </div>
              <span className="font-mono text-[11px] text-slate-600">
                {data?.updatedAt ? `MAJ ${formatTime(data.updatedAt)}` : "Connexion…"}
              </span>
            </div>
            <ol className="space-y-3 font-mono text-xs leading-5 text-slate-400">
              {(data?.events ?? []).map((event, index) => (
                <li key={`${event}-${index}`} className="flex gap-3 border-b border-white/5 pb-3 last:border-0">
                  <span className="text-cyan-500">{String(index + 1).padStart(2, "0")}</span>
                  <span>{event}</span>
                </li>
              ))}
              {!data?.events?.length && <li>En attente des premiers événements…</li>}
            </ol>
          </div>

          <aside className="rounded-3xl border border-white/10 bg-white/[0.035] p-6">
            <div className="mb-5 flex items-center gap-2">
              {data?.fatalLines?.length ? (
                <TriangleAlert size={17} className="text-rose-300" />
              ) : (
                <Server size={17} className="text-emerald-300" />
              )}
              <h2 className="font-medium">Santé du calcul</h2>
            </div>
            <dl className="space-y-4 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">Dernière activité</dt>
                <dd className="font-mono text-slate-300">{data?.lastActivityAt ? formatTime(data.lastActivityAt) : "—"}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">Extraction</dt>
                <dd className="font-mono text-slate-300">{data?.extracted ?? "—"} / 1376</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">Matching</dt>
                <dd className="font-mono text-slate-300">{data?.matched ?? "—"} / 1376</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">Alignement RTK</dt>
                <dd className="font-mono text-slate-300">{data?.alignmentSucceeded ? "OK" : "En attente"}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-500">Température GPU</dt>
                <dd className="font-mono text-slate-300">{data?.gpu ? `${data.gpu.temperature} °C` : "—"}</dd>
              </div>
            </dl>
            <div className={`mt-6 rounded-2xl border p-4 text-sm ${
              data?.fatalLines?.length
                ? "border-rose-400/20 bg-rose-400/10 text-rose-200"
                : "border-emerald-400/20 bg-emerald-400/[0.07] text-emerald-200"
            }`}>
              {data?.fatalLines?.length
                ? data.fatalLines.at(-1)
                : "Aucune erreur bloquante détectée dans le journal."}
            </div>
          </aside>
        </section>
      </div>
    </main>
  );
}
