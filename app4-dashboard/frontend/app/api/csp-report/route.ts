const MAX_REPORT_BYTES = 64 * 1024;
const ALLOWED_CONTENT_TYPES = new Set([
  "application/csp-report",
  "application/json",
  "application/reports+json",
]);

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
  while (reader) {
    const { done, value } = await reader.read();
    if (done) break;
    received += value.byteLength;
    if (received > MAX_REPORT_BYTES) {
      await reader.cancel();
      return Response.json({ detail: "CSP report too large" }, { status: 413 });
    }
  }
  return new Response(null, { status: 204 });
}
