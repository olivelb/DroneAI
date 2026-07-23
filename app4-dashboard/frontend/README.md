# DroneAI dashboard frontend

Next.js 16 and React 19 operator UI for the DroneAI pipeline.

The frontend:

- uploads and browses S3-backed datasets through the dashboard API;
- submits, resumes, cancels and deletes missions;
- renders pipeline parameters and configured COLMAP work drives;
- displays persisted mission summaries and live WebSocket status;
- previews mission rasters and links to generated S3 objects.

## Local development

The dashboard API must be reachable on port `30080` of the same host used to
open the frontend.

```bash
npm ci
npm run dev
```

Open `http://localhost:3000`. The client calls
`http://localhost:30080` and connects to
`ws://localhost:30080/ws/status`.

## Checks

```bash
npm run lint
npm run build
```

Use the committed `package-lock.json`; do not replace `npm ci` with an
unreviewed dependency update.

## Production image

From the repository root:

```bash
docker build \
  -f app4-dashboard/frontend/Dockerfile \
  -t drone-dashboard-frontend:latest \
  .
```

The image runs `npm run start` on container port `3000`. The local Helm values
publish it on NodePort `30000`.

## Current endpoint limitation

`app/lib/api.ts` currently derives the API and WebSocket endpoints from the
browser hostname and fixed port `30080`. It does not consume a
`NEXT_PUBLIC_API_URL` setting yet.

This works for the local NodePort deployment. A TLS ingress, separate API
hostname, reverse-proxy path, or non-default port requires updating the
endpoint resolution and rebuilding the frontend. Do not assume the generic
two-host ingress example is usable until that configuration surface is added.

See the repository-level [`README.md`](../../README.md) for the full stack and
[`DEVELOPMENT.md`](../../DEVELOPMENT.md) for the supported Node/npm workflow.
