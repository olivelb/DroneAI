import { promises as fs } from "node:fs";
import { type NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const previewPaths = {
  dronegs:
    "/home/olivier/droneAI-workspaces/albagnac-dronegs-dev39-lfexact-prune-15000-cross-eval/preview.png",
  lichtfeld:
    "/home/olivier/droneAI-workspaces/albagnac-lichtfeld-parity-15000-dev39-deterministic-cross-eval/preview.png",
};

export async function GET(request: NextRequest) {
  const requested = request.nextUrl.searchParams.get("engine");
  const engine = requested === "lichtfeld" ? "lichtfeld" : "dronegs";
  try {
    const image = await fs.readFile(previewPaths[engine]);
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
