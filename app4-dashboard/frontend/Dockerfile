# Stage 1: Build
FROM node:20-alpine AS builder

WORKDIR /app

COPY app4-dashboard/frontend/package*.json ./
RUN npm ci

COPY app4-dashboard/frontend/ ./
RUN npm run build
RUN npm prune --omit=dev && npm cache clean --force

# Stage 2: Run
FROM node:20-alpine AS runner

WORKDIR /app

COPY --chown=node:node --from=builder /app/package*.json ./
COPY --chown=node:node --from=builder /app/node_modules ./node_modules
COPY --chown=node:node --from=builder /app/.next ./.next
COPY --chown=node:node --from=builder /app/public ./public
COPY --chown=node:node --from=builder /app/next.config.ts ./

ENV NODE_ENV=production

EXPOSE 3000

USER node

CMD ["npm", "run", "start"]
