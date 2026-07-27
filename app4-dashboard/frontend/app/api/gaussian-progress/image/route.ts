import { promises as fs } from "node:fs";
import { NextResponse } from "next/server";

export async function GET() {
  const runPath =
    process.env.DRONEGS_PROGRESS_RUN ??
    "/home/olivier/droneAI-workspaces/saveres-dronegs-dev46-checkpoint-canary-15000";
  try {
    const image = await fs.readFile(`${runPath}/preview.png`);
    return new NextResponse(image, {
      headers: {
        "Content-Type": "image/png",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return NextResponse.json(
      { error: "Preview not available" },
      { status: 404 },
    );
  }
}
