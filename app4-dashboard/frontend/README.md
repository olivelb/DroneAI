# DroneAI dashboard frontend

Next.js 16 and React 19 operator UI for the DroneAI pipeline.

The frontend:

- establishes a role-bearing HttpOnly session from an operator-provided API
  key without storing that key in JavaScript or browser storage;
- uploads and browses S3-backed datasets through the dashboard API;
- submits, resumes, cancels and deletes missions;
- renders pipeline parameters and configured COLMAP work drives;
- displays persisted mission summaries and live WebSocket status;
- previews mission rasters and links to generated S3 objects.

## Local development

By default the dashboard API must be reachable on port `30080` of the same
host used to open the frontend. Authentication is disabled by the local Helm
values.

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

## Runtime API origin and authentication

The Next.js server reads `DRONEAI_PUBLIC_API_URL` at runtime and emits the
value in the page metadata. Browser HTTP and WebSocket clients use that origin;
an empty value preserves the local `http://<browser-host>:30080` fallback. The
Helm production example sets
`https://api.droneai.example.com`, so changing the ingress hostname does not
require rebuilding the image.

When API authentication is enabled, Mission Studio presents a sign-in screen.
The operator enters a provisioned viewer/operator/admin key once. The API
validates it and returns a bounded HttpOnly, Secure, SameSite=Lax cookie used
for both credentialed CORS requests and WebSocket authentication. The key is
never compiled into the frontend, written to local/session storage, stored in
the signed cookie or added to the WebSocket URL. Sign-out clears the cookie.

See the repository-level [`README.md`](../../README.md) for the full stack and
[`DEVELOPMENT.md`](../../DEVELOPMENT.md) for the supported Node/npm workflow.
