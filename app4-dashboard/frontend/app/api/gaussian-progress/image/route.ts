import { promises as fs } from "node:fs";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const previewPath =
  process.env.DRONEGS_PROGRESS_PREVIEW ??
  "/home/olivier/droneAI-workspaces/albagnac-dronegs-dev38-fastgs-15000/preview.png";

export async function GET() {
  try {
    const image = await fs.readFile(previewPath);
    return new NextResponse(image, {
      headers: {
        "Content-Type": "image/png",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return NextResponse.json(
      { error: "Preview unavailable" },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }
}
