# Stage 1: Build
FROM node:24-alpine@sha256:116b800c3754b440ee0aa3ed2f20f5bf3d99a554bfd9adeea09ea8017f6e85f5 AS builder

WORKDIR /app

COPY app4-dashboard/frontend/package*.json ./
COPY app4-dashboard/frontend/scripts/patch-playcanvas-fastgs.mjs \
    ./scripts/patch-playcanvas-fastgs.mjs
COPY app4-dashboard/frontend/scripts/patch-playcanvas-full-transform.mjs \
    ./scripts/patch-playcanvas-full-transform.mjs
COPY app4-dashboard/frontend/scripts/patch-playcanvas-incremental-world.mjs \
    ./scripts/patch-playcanvas-incremental-world.mjs
COPY app4-dashboard/frontend/scripts/patch-playcanvas-container-intervals.mjs \
    ./scripts/patch-playcanvas-container-intervals.mjs
COPY app4-dashboard/frontend/scripts/patch-playcanvas-workbuffer-view-direction.mjs \
    ./scripts/patch-playcanvas-workbuffer-view-direction.mjs
# Resolve with the packageManager version used by CI and local development.
RUN corepack npm ci

COPY app4-dashboard/frontend/ ./
RUN corepack npm run build

# Stage 2: Run
FROM node:24-alpine@sha256:116b800c3754b440ee0aa3ed2f20f5bf3d99a554bfd9adeea09ea8017f6e85f5 AS runner

WORKDIR /app

# The production server only needs Node.js. Removing npm/npx reduces the
# runtime attack surface and avoids shipping their unused dependency tree.
RUN apk upgrade --no-cache libcrypto3 libssl3 \
    && rm -rf /usr/local/lib/node_modules/npm \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx

COPY --chown=node:node --from=builder /app/.next/standalone ./
COPY --chown=node:node --from=builder /app/.next/static ./.next/static
COPY --chown=node:node --from=builder /app/public ./public

ENV NODE_ENV=production

EXPOSE 3000

USER node

ENV HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
