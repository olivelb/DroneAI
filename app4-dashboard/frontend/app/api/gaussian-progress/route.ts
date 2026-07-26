import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import { promisify } from "node:util";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const execFileAsync = promisify(execFile);
const runDirectory =
  process.env.DRONEGS_PROGRESS_RUN ??
  "/home/olivier/droneAI-workspaces/albagnac-dronegs-dev38-fastgs-15000";
const logPath =
  process.env.DRONEGS_PROGRESS_LOG ??
  "/home/olivier/droneAI-workspaces/albagnac-dronegs-dev38-fastgs-15000.log";
const exitPath =
  process.env.DRONEGS_PROGRESS_EXIT ??
  "/home/olivier/droneAI-workspaces/albagnac-dronegs-dev38-fastgs-15000.exit";
const totalIterations = Number(
  process.env.DRONEGS_PROGRESS_ITERATIONS ?? "15000",
);

type ProgressEvent = {
  event: string;
  iteration?: number;
  iterations?: number;
  loss?: number;
  gaussians?: number;
  added?: number;
  pruned?: number;
};

function parseEvents(content: string): ProgressEvent[] {
  const events: ProgressEvent[] = [];
  for (const line of content.split(/\r?\n/)) {
    if (!line.startsWith("{")) continue;
    try {
      const event = JSON.parse(line) as ProgressEvent;
      if (typeof event.event === "string") events.push(event);
    } catch {
      // Human-readable diagnostics may share the same log.
    }
  }
  return events;
}

async function readJson(path: string) {
  try {
    return JSON.parse(await fs.readFile(path, "utf8")) as Record<
      string,
      unknown
    >;
  } catch {
    return null;
  }
}

async function gpuSnapshot() {
  try {
    const { stdout } = await execFileAsync("nvidia-smi", [
      "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
      "--format=csv,noheader,nounits",
    ]);
    const [utilization, memoryUsed, memoryTotal, temperature] = stdout
      .trim()
      .split(",")
      .map((value) => Number(value.trim()));
    return { utilization, memoryUsed, memoryTotal, temperature };
  } catch {
    return null;
  }
}

export async function GET() {
  try {
    const [content, logStats, manifest, gpu] = await Promise.all([
      fs.readFile(logPath, "utf8"),
      fs.stat(logPath),
      readJson(`${runDirectory}/trainer_run.json`),
      gpuSnapshot(),
    ]);
    const events = parseEvents(content);
    const progressEvents = events.filter(
      (event) => event.event === "progress",
    );
    const latest = progressEvents.at(-1);
    const topology = events
      .filter((event) => event.event === "topology_refinement")
      .at(-1);
    const iteration = Math.min(
      totalIterations,
      Math.max(0, Number(latest?.iteration ?? 0)),
    );
    const progress = (iteration / totalIterations) * 100;
    const elapsedSeconds = Math.max(
      0,
      (Date.now() - logStats.birthtimeMs) / 1000,
    );
    const estimatedTotalSeconds =
      iteration > 0 ? (elapsedSeconds * totalIterations) / iteration : null;
    const etaSeconds =
      estimatedTotalSeconds === null
        ? null
        : Math.max(0, estimatedTotalSeconds - elapsedSeconds);
    const fatalLines = content
      .split(/\r?\n/)
      .filter((line) =>
        /out of memory|CUDA error|terminate called|Traceback|fatal/i.test(line),
      )
      .slice(-4);
    let exitCode: number | null = null;
    try {
      exitCode = Number((await fs.readFile(exitPath, "utf8")).trim());
    } catch {
      exitCode = null;
    }
    const metrics = manifest?.metrics as Record<string, unknown> | undefined;
    const timings = manifest?.timings as Record<string, unknown> | undefined;
    const completed = manifest?.status === "completed";
    const status = completed
      ? "completed"
      : exitCode !== null && exitCode !== 0
        ? "error"
        : fatalLines.length > 0
          ? "error"
          : iteration >= totalIterations
            ? "finalizing"
            : "running";
    const stage =
      iteration === 0
        ? "Évaluation initiale et chargement"
        : iteration >= totalIterations && !completed
          ? "Évaluation finale et export"
          : completed
            ? "Terminé"
            : "Entraînement MRNF / FastGS";
    const hasPreview = await fs
      .access(`${runDirectory}/preview.png`)
      .then(() => true)
      .catch(() => false);

    return NextResponse.json(
      {
        status,
        stage,
        progress,
        iteration,
        totalIterations,
        loss: latest?.loss ?? null,
        gaussians: latest?.gaussians ?? topology?.gaussians ?? null,
        elapsedSeconds,
        etaSeconds,
        lastActivityAt: logStats.mtime.toISOString(),
        updatedAt: new Date().toISOString(),
        gpu,
        metrics: metrics ?? null,
        timings: timings ?? null,
        topology: topology ?? null,
        fatalLines,
        hasPreview,
        recentEvents: events.slice(-8),
        baseline: {
          label: "PLY LichtFeld · mêmes 172 vues",
          psnr: 18.90036392,
          ssim: 0.4286741614,
        },
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return NextResponse.json(
      {
        status: "waiting",
        stage: "En attente du lancement",
        progress: 0,
        iteration: 0,
        totalIterations,
        updatedAt: new Date().toISOString(),
        message: error instanceof Error ? error.message : "Suivi indisponible",
      },
      { status: 200, headers: { "Cache-Control": "no-store" } },
    );
  }
}
