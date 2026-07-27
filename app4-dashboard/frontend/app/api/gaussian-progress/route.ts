import { promises as fs } from "node:fs";
import { NextResponse } from "next/server";

const runPath =
  process.env.DRONEGS_PROGRESS_RUN ??
  "/home/olivier/droneAI-workspaces/saveres-dronegs-dev46-checkpoint-canary-15000";
const logPath =
  process.env.DRONEGS_PROGRESS_LOG ??
  `${runPath}.log`;

async function readText(path: string) {
  try {
    return await fs.readFile(path, "utf8");
  } catch {
    return "";
  }
}

async function readJson(path: string) {
  try {
    return JSON.parse(await fs.readFile(path, "utf8"));
  } catch {
    return null;
  }
}

async function exists(path: string) {
  try {
    await fs.access(path);
    return true;
  } catch {
    return false;
  }
}

export async function GET() {
  const [log, manifest, canary, lpips, logStats] = await Promise.all([
    readText(logPath),
    readJson(`${runPath}/trainer_run.json`),
    readJson(`${runPath}/canary_result.json`),
    readJson(`${runPath}/evaluation/lpips.json`),
    fs.stat(logPath).catch(() => null),
  ]);
  const events = log
    .split(/\r?\n/)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
  const progress = events.filter((event) => event.event === "progress").at(-1);
  const checkpoint = events
    .filter((event) => event.event === "checkpoint_saved")
    .at(-1);
  const evaluation = events
    .filter((event) => event.event === "evaluation")
    .at(-1);
  const metrics = manifest?.metrics ?? null;
  const fatal = log
    .split(/\r?\n/)
    .filter((line) => /failed|fatal|exception|cuda error/i.test(line))
    .slice(-5);
  const completed = manifest?.status === "completed";
  const fresh = logStats
    ? Date.now() - logStats.mtimeMs < 5 * 60 * 1000
    : false;

  return NextResponse.json({
    generatedAt: new Date().toISOString(),
    scene: process.env.DRONEGS_PROGRESS_SCENE ?? "Savères Mavic 3E RTK",
    status: completed ? "completed" : fatal.length ? "failed" : fresh ? "running" : "idle",
    iteration: progress?.iteration ?? 0,
    iterations: progress?.iterations ?? 15000,
    loss: progress?.loss ?? null,
    gaussians: progress?.gaussians ?? null,
    checkpoint: checkpoint
      ? {
          iteration: checkpoint.iteration,
          path: checkpoint.path,
        }
      : null,
    evaluation: {
      view: evaluation?.view ?? null,
      views: evaluation?.views ?? null,
      psnr: metrics?.psnr ?? null,
      ssim: metrics?.ssim ?? null,
      lpips: lpips?.mean ?? metrics?.lpips ?? null,
    },
    timings: manifest?.timings ?? null,
    canary,
    fatal,
    preview: await exists(`${runPath}/preview.png`),
  });
}
