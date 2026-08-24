# Stage 1: Build
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS builder

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
RUN npm ci

COPY app4-dashboard/frontend/ ./
RUN npm run build
RUN npm prune --omit=dev && npm cache clean --force

# Stage 2: Run
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS runner

WORKDIR /app

# The production server only needs Node.js. Removing npm/npx reduces the
# runtime attack surface and avoids shipping their unused dependency tree.
RUN rm -rf /usr/local/lib/node_modules/npm \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx

COPY --chown=node:node --from=builder /app/package*.json ./
COPY --chown=node:node --from=builder /app/node_modules ./node_modules
COPY --chown=node:node --from=builder /app/.next ./.next
COPY --chown=node:node --from=builder /app/public ./public
COPY --chown=node:node --from=builder /app/next.config.ts ./

ENV NODE_ENV=production

EXPOSE 3000

USER node

CMD ["node", "node_modules/next/dist/bin/next", "start"]
