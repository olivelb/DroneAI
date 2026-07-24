import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import { promisify } from "node:util";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const execFileAsync = promisify(execFile);
const workspace =
  process.env.COLMAP_PROGRESS_WORKSPACE ??
  "/home/olivier/droneAI-workspaces/albagnac-mavic3e-full";
const logPath =
  process.env.COLMAP_PROGRESS_LOG ??
  "/home/olivier/droneAI-workspaces/.albagnac-mavic3e-full.sparse.log";
const totalImages = Number(process.env.COLMAP_PROGRESS_TOTAL_IMAGES ?? "1376");

type Stage = "preparation" | "extraction" | "matching" | "mapping" | "completed";

function maximumMatch(content: string, expression: RegExp): number {
  let maximum = 0;
  for (const match of content.matchAll(expression)) {
    maximum = Math.max(maximum, Number(match[1] ?? 0));
  }
  return maximum;
}

function recentEvents(content: string): string[] {
  const interesting =
    /Processed file \[|Processing image \[|Registering image #|Global bundle adjustment|Keeping successful reconstruction|Elapsed time:/;
  return content
    .split(/\r?\n/)
    .filter((line) => interesting.test(line))
    .slice(-8)
    .map((line) =>
      line
        .replace(/^I\d{4}\s+\d{2}:\d{2}:\d{2}\.\d+\s+\d+\s+/, "")
        .trim(),
    );
}

function stageFromLog(content: string, completed: boolean): Stage {
  if (completed) return "completed";
  const positions: Array<[Stage, number]> = [
    ["extraction", content.lastIndexOf("Processed file [")],
    ["matching", content.lastIndexOf("Processing image [")],
    ["mapping", content.lastIndexOf("Registering image #")],
  ];
  positions.sort((left, right) => right[1] - left[1]);
  return positions[0][1] >= 0 ? positions[0][0] : "preparation";
}

function stageProgress(
  stage: Stage,
  extracted: number,
  matched: number,
  registered: number,
): number {
  if (stage === "completed") return 100;
  if (stage === "extraction") return (extracted / totalImages) * 25;
  if (stage === "matching") return 25 + (matched / totalImages) * 25;
  if (stage === "mapping") return 50 + (registered / totalImages) * 50;
  return 0;
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
    const [content, logStats, gpu] = await Promise.all([
      fs.readFile(logPath, "utf8"),
      fs.stat(logPath),
      gpuSnapshot(),
    ]);
    const metricsPath = `${workspace}/metrics.json`;
    let metrics: Record<string, number | string> | null = null;
    try {
      metrics = JSON.parse(await fs.readFile(metricsPath, "utf8"));
    } catch {
      metrics = null;
    }

    const extracted = maximumMatch(
      content,
      /Processed file \[(\d+)\/\d+\]/g,
    );
    const matched = maximumMatch(
      content,
      /Processing image \[(\d+)\/\d+\]/g,
    );
    const registeredInLog = maximumMatch(
      content,
      /Registering image #\d+ \(num_reg_frames=(\d+)\)/g,
    );
    const registered = Number(metrics?.registered_images ?? registeredInLog);
    const stage = stageFromLog(content, metrics !== null);
    const progress = Math.min(
      100,
      Math.max(0, stageProgress(stage, extracted, matched, registered)),
    );
    const ageSeconds = Math.max(
      0,
      (Date.now() - logStats.mtimeMs) / 1000,
    );
    const fatalLines = content
      .split(/\r?\n/)
      .filter((line) =>
        /Traceback|out of memory|CUDA error|returned non-zero exit status/i.test(
          line,
        ),
      )
      .slice(-3);
    const status =
      stage === "completed"
        ? "completed"
        : fatalLines.length > 0
          ? "error"
          : ageSeconds > 600
            ? "stalled"
            : "running";

    return NextResponse.json(
      {
        status,
        stage,
        progress,
        totalImages,
        extracted,
        matched,
        registered,
        completedModels: (content.match(/Keeping successful reconstruction/g) ?? [])
          .length,
        elapsedSeconds: Math.max(
          0,
          (Date.now() - logStats.birthtimeMs) / 1000,
        ),
        lastActivityAt: logStats.mtime.toISOString(),
        updatedAt: new Date().toISOString(),
        gpu,
        metrics,
        fatalLines,
        events: recentEvents(content),
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return NextResponse.json(
      {
        status: "unavailable",
        message: error instanceof Error ? error.message : "Progress unavailable",
        updatedAt: new Date().toISOString(),
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
