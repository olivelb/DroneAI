# DroneAI dashboard frontend

Next.js 16 and React 19 operator UI for the DroneAI pipeline.

The frontend:

- establishes a role-bearing HttpOnly session from an operator-provided API
  key without storing that key in JavaScript or browser storage;
- obtains durable, quota-checked multipart sessions from the API, uploads file
  parts directly to S3 and finalizes a verified dataset manifest;
- presents aerial mapping and HD facade as explicit production
  processes; maps use five stages through AI detection, while facades omit
  that stage and terminate after local DroneGS raster production;
- discovers the work drives actually mounted by the runtime instead of
  exposing host-specific drive letters;
- keeps production presets prominent while resolution, BA passes,
  retriangulation, quality gates and expert parameters remain accessible in
  collapsible advanced sections;
- submits, resumes, cancels and deletes missions and exposes live status in a
  dedicated monitor drawer;
- renders aerial orthomosaics/height maps and clearly labelled local facade
  orthophoto/depth products;
- launches retryable YOLO/SAM analyses, controls campaign visibility and
  searches persisted objects with automatic map framing;
- provides a full-screen GIS viewer with measurements and manual
  point/line/polygon creation, editing, tags, descriptions and colors;
- downloads COG/GeoTIFF rasters and QGIS-ready GeoPackage/GeoJSON layers.
  GeoPackages default to the raster CRS or use WGS84/custom EPSG; GeoJSON stays
  in EPSG:4326.
- defaults to English and offers a persistent English/French locale switch for
  authentication, mission launch and monitoring, and geospatial workflows.

Chromium-based browsers open a native save-file picker and stream large
downloads directly to the selected file. Other browsers use their configured
download folder.

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
npm run test
npm run lint
npm run build
npx playwright install chromium
npm run test:e2e
```

The browser suite mocks API transport while exercising the production Next.js
application in Chromium. Its nine journeys cover dataset selection and mission
launch, durable mission detail, locale persistence, versioned quality profiles,
operator cancellation, the terminal cancelled state, browser-session renewal,
live-event delivery after a WebSocket reconnect and projected GeoPackage export
from a completed mission. Unit tests separately verify exact selected-mission
polling, stale WebSocket protection and the five-stage projection. CI installs
Chromium with its Linux system dependencies and retains the Playwright report
on failure.

`npm ci` also applies three fail-closed patches to pinned PlayCanvas 2.21.4:
the FastGS off-axis covariance bound, lossless float32 quaternion/scale work
streams and stable incremental unified-world allocations. Every patch is
idempotent, rejects an unaudited engine source shape and is covered by a source
transformation test. Keep all three scripts available before `npm ci` in
container builds.

Use the committed `package-lock.json`; do not replace `npm ci` with an
unreviewed dependency update.

## Internationalization

The dependency-free locale contract lives in `app/lib/i18n`. English is the
source and default catalog; French implements the same typed `MessageKey`
record. Adding an English key without its French value fails TypeScript, while
`catalog.test.ts` also rejects missing or empty values. Use `useI18n().t()` for
operator-facing copy and interpolation instead of adding raw bilingual text to
a migrated component. The selected locale is stored as `droneai.locale`; no
mission or authentication data is attached to that preference.

## Production image

From the repository root:

```bash
docker build \
  -f app4-dashboard/frontend/Dockerfile \
  -t "drone-dashboard-frontend:$(git rev-parse --short=7 HEAD)" \
  .
```

The image launches the Next.js server directly with Node.js on container port
`3000`; npm/npx and their unused dependency tree are removed from the runtime
stage. The local Helm values publish it on NodePort `30000`.

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
The API also validates the browser `Origin` during the WebSocket handshake and
periodically revalidates the durable session. Revocation, suspension or a role
change closes the socket; the existing reconnect flow then returns to normal
HTTP authentication instead of continuing with stale privileges.

Session lifecycle is isolated in `app/lib/auth.tsx` behind `AuthProvider` and
`useAuth`; it owns API credentials, login errors and session renewal state.
`MissionRuntimeProvider` separately owns mission summaries, active selection,
logs, polling and WebSocket reconnection. `WorkspaceDataProvider` caches the
remote dataset listing and pod health, while the remaining `StoreProvider`
keeps only local mission input, parameter, upload and navigation state. The
providers consume only the authentication status needed to start or stop
protected work.

See the repository-level [`README.md`](../../README.md) for the full stack and
[`DEVELOPMENT.md`](../../DEVELOPMENT.md) for the supported Node/npm workflow.
