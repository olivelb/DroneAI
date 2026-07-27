import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import { promisify } from "node:util";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const execFileAsync = promisify(execFile);
const totalIterations = 15_000;

const paths = {
  dronegs: {
    run: "/home/olivier/droneAI-workspaces/albagnac-dronegs-dev45-photometric-fastgs-15000",
    log: "/home/olivier/droneAI-workspaces/albagnac-dronegs-dev45-photometric-fastgs-15000.log",
  },
  lichtfeld: {
    run: "/home/olivier/droneAI-workspaces/albagnac-lichtfeld-parity-15000-dev38",
    log: "/home/olivier/droneAI-workspaces/albagnac-lichtfeld-parity-15000-dev38/stdout.log",
  },
  commonEvaluation: {
    dronegs:
      "/home/olivier/droneAI-workspaces/albagnac-dronegs-dev45-photometric-fastgs-15000-cross-eval",
    lichtfeld:
      "/home/olivier/droneAI-workspaces/albagnac-lichtfeld-parity-15000-dev38-cross-eval",
  },
};

type Point = {
  iteration: number;
  loss: number | null;
  gaussians: number | null;
};

type Manifest = {
  status?: string;
  metrics?: Record<string, number | string | null>;
  timings?: Record<string, number>;
};

async function readText(path: string) {
  try {
    return await fs.readFile(path, "utf8");
  } catch {
    return "";
  }
}

async function readJson<T>(path: string): Promise<T | null> {
  try {
    return JSON.parse(await fs.readFile(path, "utf8")) as T;
  } catch {
    return null;
  }
}

async function stats(path: string) {
  try {
    return await fs.stat(path);
  } catch {
    return null;
  }
}

function compactHistory(points: Point[], maximum = 32) {
  if (points.length <= maximum) return points;
  const compact: Point[] = [];
  for (let index = 0; index < maximum; index += 1) {
    const source = Math.round((index * (points.length - 1)) / (maximum - 1));
    compact.push(points[source]);
  }
  return compact;
}

function timeEstimate(iteration: number, startedAt: number, finished = false) {
  const elapsedSeconds = Math.max(0, (Date.now() - startedAt) / 1000);
  const etaSeconds =
    finished || iteration <= 0
      ? null
      : Math.max(
          0,
          (elapsedSeconds * totalIterations) / iteration - elapsedSeconds,
        );
  return { elapsedSeconds, etaSeconds };
}

function fatalLines(content: string) {
  return content
    .replaceAll("\r", "\n")
    .split("\n")
    .filter((line) =>
      /out of memory|CUDA error|terminate called|Traceback|fatal/i.test(line),
    )
    .slice(-4);
}

function droneProgress(content: string) {
  const points: Point[] = [];
  for (const line of content.split(/\r?\n/)) {
    if (!line.startsWith("{")) continue;
    try {
      const event = JSON.parse(line) as Record<string, unknown>;
      if (event.event !== "progress") continue;
      points.push({
        iteration: Number(event.iteration),
        loss: Number(event.loss),
        gaussians: Number(event.gaussians),
      });
    } catch {
      // Diagnostics may share the log with JSON telemetry.
    }
  }
  return points;
}

function lichtfeldProgress(content: string) {
  const clean = content.replace(/\u001b\[[0-9;]*m/g, "");
  const expression =
    /(\d+)\/15000\s+\|\s+Loss:\s+([0-9.eE+-]+)\s+\|\s+Splats:\s+(\d+)/g;
  const points: Point[] = [];
  for (const match of clean.matchAll(expression)) {
    points.push({
      iteration: Number(match[1]),
      loss: Number(match[2]),
      gaussians: Number(match[3]),
    });
  }
  return points;
}

function parseNativeLichtfeldMetrics(content: string) {
  const lines = content.trim().split(/\r?\n/);
  if (lines.length < 2) return null;
  const values = lines.at(-1)?.split(",").map(Number);
  if (!values || values.length < 5) return null;
  return {
    iteration: values[0],
    psnr: values[1],
    ssim: values[2],
    time_per_image: values[3],
    final_gaussians: values[4],
  };
}

function parseLichtfeldTimings(content: string) {
  const clean = content.replace(/\u001b\[[0-9;]*m/g, "");
  const training = clean.match(/Training completed in\s+([0-9.]+)s/i);
  const stamps = [...clean.matchAll(/\[(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]/g)];
  const timings: Record<string, number> = {};
  if (training) timings.training_seconds = Number(training[1]);
  if (stamps.length >= 2) {
    const toSeconds = (match: RegExpMatchArray) =>
      Number(match[1]) * 3600 +
      Number(match[2]) * 60 +
      Number(match[3]) +
      Number(match[4]) / 1000;
    const first = toSeconds(stamps[0]);
    const last = toSeconds(stamps.at(-1)!);
    timings.wall_seconds =
      last >= first ? last - first : last + 86_400 - first;
  }
  return timings;
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
  const [
    droneLog,
    lichtfeldLog,
    droneStats,
    lichtfeldStats,
    droneManifest,
    nativeMetricsText,
    droneCommonManifest,
    droneCommonLpips,
    lichtfeldCommonManifest,
    lichtfeldCommonLpips,
    gpu,
  ] = await Promise.all([
    readText(paths.dronegs.log),
    readText(paths.lichtfeld.log),
    stats(paths.dronegs.log),
    stats(paths.lichtfeld.log),
    readJson<Manifest>(`${paths.dronegs.run}/trainer_run.json`),
    readText(`${paths.lichtfeld.run}/metrics.csv`),
    readJson<Manifest>(
      `${paths.commonEvaluation.dronegs}/trainer_run.json`,
    ),
    readJson<Record<string, number | string>>(
      `${paths.commonEvaluation.dronegs}/evaluation/lpips.json`,
    ),
    readJson<Manifest>(
      `${paths.commonEvaluation.lichtfeld}/trainer_run.json`,
    ),
    readJson<Record<string, number | string>>(
      `${paths.commonEvaluation.lichtfeld}/evaluation/lpips.json`,
    ),
    gpuSnapshot(),
  ]);

  const dronePoints = droneProgress(droneLog);
  const lichtfeldPoints = lichtfeldProgress(lichtfeldLog);
  const droneLatest = dronePoints.at(-1);
  const lichtfeldLatest = lichtfeldPoints.at(-1);
  const nativeMetrics = parseNativeLichtfeldMetrics(nativeMetricsText);
  const lichtfeldTimings = parseLichtfeldTimings(lichtfeldLog);
  const lichtfeldDone =
    /Training completed successfully/.test(lichtfeldLog) &&
    nativeMetrics?.iteration === totalIterations;
  const droneCommonDone = droneCommonManifest?.status === "completed";
  const lichtfeldCommonDone =
    lichtfeldCommonManifest?.status === "completed";
  const droneCommonMetrics = droneCommonManifest?.metrics ?? null;
  const lichtfeldCommonMetrics =
    lichtfeldCommonManifest?.metrics ?? null;
  const droneCommonPsnr =
    typeof droneCommonMetrics?.initial_held_out_psnr === "number"
      ? droneCommonMetrics.initial_held_out_psnr
      : null;
  const droneCommonSsim =
    typeof droneCommonMetrics?.initial_held_out_ssim === "number"
      ? droneCommonMetrics.initial_held_out_ssim
      : null;
  const droneCommonLpipsMean =
    typeof droneCommonLpips?.mean === "number"
      ? droneCommonLpips.mean
      : null;
  const lichtfeldCommonPsnr =
    typeof lichtfeldCommonMetrics?.initial_held_out_psnr === "number"
      ? lichtfeldCommonMetrics.initial_held_out_psnr
      : null;
  const lichtfeldCommonSsim =
    typeof lichtfeldCommonMetrics?.initial_held_out_ssim === "number"
      ? lichtfeldCommonMetrics.initial_held_out_ssim
      : null;
  const lichtfeldCommonLpipsMean =
    typeof lichtfeldCommonLpips?.mean === "number"
      ? lichtfeldCommonLpips.mean
      : null;
  const commonDone =
    droneCommonDone &&
    lichtfeldCommonDone &&
    droneCommonLpipsMean !== null &&
    lichtfeldCommonLpipsMean !== null;

  const makeRun = (
    engine: "dronegs" | "lichtfeld",
    points: Point[],
    latest: Point | undefined,
    logStat: Awaited<ReturnType<typeof stats>>,
    completed: boolean,
    metrics: Record<string, unknown> | null,
    timings: Record<string, number> | null,
    failures: string[],
  ) => {
    const iteration = completed
      ? totalIterations
      : Math.min(totalIterations, Math.max(0, latest?.iteration ?? 0));
    const timing = timeEstimate(
      iteration,
      logStat?.birthtimeMs ?? Date.now(),
      completed,
    );
    const elapsedSeconds =
      completed && typeof timings?.wall_seconds === "number"
        ? timings.wall_seconds
        : timing.elapsedSeconds;
    return {
      engine,
      status:
        failures.length > 0
          ? "error"
          : completed
            ? "completed"
            : logStat
              ? "running"
              : "waiting",
      stage:
        engine === "lichtfeld" && completed && !lichtfeldCommonDone
          ? "Évaluation commune en attente"
          : completed
            ? "Terminé"
            : iteration > 0
              ? "Entraînement MRNF"
              : "Chargement / initialisation",
      iteration,
      totalIterations,
      progress: (iteration / totalIterations) * 100,
      loss: latest?.loss ?? null,
      gaussians:
        latest?.gaussians ??
        (typeof metrics?.final_gaussians === "number"
          ? metrics.final_gaussians
          : null),
      ...timing,
      elapsedSeconds,
      lastActivityAt: logStat?.mtime.toISOString() ?? null,
      metrics,
      timings,
      fatalLines: failures,
      history: compactHistory(points),
    };
  };

  const droneMetrics = droneManifest?.metrics
    ? {
        ...droneManifest.metrics,
        common_psnr: droneCommonPsnr,
        common_ssim: droneCommonSsim,
        common_lpips: droneCommonLpipsMean,
      }
    : null;
  const lichtfeldMetrics = nativeMetrics
    ? {
        ...nativeMetrics,
        common_psnr: lichtfeldCommonPsnr,
        common_ssim: lichtfeldCommonSsim,
        common_lpips: lichtfeldCommonLpipsMean,
      }
    : null;

  return NextResponse.json(
    {
      updatedAt: new Date().toISOString(),
      gpu,
      contract: {
        dataset: "Albagnac Mavic 3E RTK · dense COLMAP existant",
        images: 1376,
        trainingImages: 1204,
        heldOutImages: 172,
        iterations: totalIterations,
        strategy: "MRNF",
        seed: 42,
        shSchedule: "0 → 3, +1 tous les 1 000 pas",
        maxGaussians: 1_500_000,
        resize: "facteur 4 · largeur max. 1 600 · tuilage 4",
        evaluator:
          "DroneGS dev38 FastGS commun · 172 vues · LPIPS AlexNet · replay caméra déterministe",
      },
      dronegs: makeRun(
        "dronegs",
        dronePoints,
        droneLatest,
        droneStats,
        droneManifest?.status === "completed",
        droneMetrics,
        droneManifest?.timings ?? null,
        fatalLines(droneLog),
      ),
      lichtfeld: makeRun(
        "lichtfeld",
        lichtfeldPoints,
        lichtfeldLatest,
        lichtfeldStats,
        lichtfeldDone,
        lichtfeldMetrics,
        lichtfeldTimings,
        fatalLines(lichtfeldLog),
      ),
      commonEvaluation: {
        status: commonDone
          ? "completed"
          : lichtfeldDone
            ? "pending"
            : "waiting",
        psnr: lichtfeldCommonPsnr,
        ssim: lichtfeldCommonSsim,
        lpips: lichtfeldCommonLpipsMean,
      },
      preview: {
        dronegs: await stats(
          `${paths.commonEvaluation.dronegs}/preview.png`,
        ).then(Boolean),
        lichtfeld: await stats(
          `${paths.commonEvaluation.lichtfeld}/preview.png`,
        ).then(Boolean),
      },
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
