const MAX_REPORT_BYTES = 64 * 1024;
const ALLOWED_CONTENT_TYPES = new Set([
  "application/csp-report",
  "application/json",
  "application/reports+json",
]);

function boundedString(value: unknown, maximum = 200): string | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim();
  return normalized ? normalized.slice(0, maximum) : undefined;
}

function sanitizedLocation(value: unknown): string | undefined {
  const raw = boundedString(value, 2_048);
  if (!raw) return undefined;
  if (["inline", "eval", "self"].includes(raw)) return raw;
  try {
    const parsed = new URL(raw);
    if (!new Set(["http:", "https:"]).has(parsed.protocol)) {
      return parsed.protocol;
    }
    return `${parsed.protocol}//${parsed.host}${parsed.pathname}`.slice(0, 500);
  } catch {
    return "invalid-url";
  }
}

function reportBodies(payload: unknown): Record<string, unknown>[] {
  if (Array.isArray(payload)) {
    return payload.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const record = item as Record<string, unknown>;
      return record.body && typeof record.body === "object"
        ? [record.body as Record<string, unknown>]
        : [];
    });
  }
  if (!payload || typeof payload !== "object") return [];
  const record = payload as Record<string, unknown>;
  const legacy = record["csp-report"];
  return legacy && typeof legacy === "object"
    ? [legacy as Record<string, unknown>]
    : [record];
}

function emitSanitizedReports(payload: unknown): void {
  for (const body of reportBodies(payload).slice(0, 20)) {
    const event = {
      event: "droneai_csp_violation",
      document: sanitizedLocation(body.documentURL ?? body["document-uri"]),
      blocked: sanitizedLocation(body.blockedURL ?? body["blocked-uri"]),
      directive: boundedString(
        body.effectiveDirective ?? body["effective-directive"] ?? body["violated-directive"],
      ),
      disposition: boundedString(body.disposition, 32),
      statusCode:
        typeof (body.statusCode ?? body["status-code"]) === "number"
          ? body.statusCode ?? body["status-code"]
          : undefined,
    };
    console.warn(JSON.stringify(event));
  }
}

export async function POST(request: Request) {
  const contentType = (request.headers.get("content-type") ?? "")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (!ALLOWED_CONTENT_TYPES.has(contentType)) {
    return Response.json({ detail: "Unsupported CSP report type" }, { status: 415 });
  }
  const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (!Number.isFinite(declaredLength) || declaredLength > MAX_REPORT_BYTES) {
    return Response.json({ detail: "CSP report too large" }, { status: 413 });
  }
  const reader = request.body?.getReader();
  let received = 0;
  const chunks: Uint8Array[] = [];
  while (reader) {
    const { done, value } = await reader.read();
    if (done) break;
    received += value.byteLength;
    if (received > MAX_REPORT_BYTES) {
      await reader.cancel();
      return Response.json({ detail: "CSP report too large" }, { status: 413 });
    }
    chunks.push(value);
  }
  try {
    const buffer = new Uint8Array(received);
    let offset = 0;
    for (const chunk of chunks) {
      buffer.set(chunk, offset);
      offset += chunk.byteLength;
    }
    emitSanitizedReports(JSON.parse(new TextDecoder().decode(buffer)));
  } catch {
    return Response.json({ detail: "Invalid CSP report" }, { status: 400 });
  }
  return new Response(null, { status: 204 });
}
