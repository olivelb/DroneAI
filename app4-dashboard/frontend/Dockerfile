# Stage 1: Build
FROM node:20-alpine AS builder

WORKDIR /app

COPY app4-dashboard/frontend/package*.json ./
RUN npm ci

COPY app4-dashboard/frontend/ ./
RUN npm run build

# Stage 2: Run
FROM node:20-alpine AS runner

WORKDIR /app

COPY --from=builder /app/package*.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.ts ./

ENV NODE_ENV=production

EXPOSE 3000

CMD ["npm", "run", "start"]
