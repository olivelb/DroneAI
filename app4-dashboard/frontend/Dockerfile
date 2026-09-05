# Stage 1: Build
FROM node:24-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf AS builder

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
RUN npm ci

COPY app4-dashboard/frontend/ ./
RUN npm run build
RUN npm prune --omit=dev && npm cache clean --force

# Stage 2: Run
FROM node:24-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf AS runner

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
