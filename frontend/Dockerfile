# Frontend container image.
# ---------------------------------------------------------------------------
# Three stages so the runtime image carries only what is needed to SERVE the
# app: no source, no dev dependencies, no build toolchain.

# --- Stage 1: dependencies -------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /app

# Copy only the manifests, so this layer is cached unless dependencies change.
COPY package.json package-lock.json* ./
# `npm ci` installs exactly what the lockfile pins — reproducible, unlike
# `npm install`, which may resolve newer versions and make builds differ.
RUN npm ci

# --- Stage 2: build --------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Next.js inlines NEXT_PUBLIC_* variables at BUILD time, not runtime — they end
# up baked into the JavaScript bundle. Changing the API URL therefore requires
# a rebuild, not just a restart.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

# --- Stage 3: runtime ------------------------------------------------------
FROM node:22-alpine AS runtime
WORKDIR /app

ENV NODE_ENV=production
# Opt out of Next.js's anonymous build telemetry.
ENV NEXT_TELEMETRY_DISABLED=1

# `output: "standalone"` in next.config.ts would let this copy a minimal
# server bundle instead. Left off for now because it complicates local
# development; switch it on when optimising the deployed image.
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./package.json

RUN addgroup -g 1001 -S nodejs \
    && adduser -S nextjs -u 1001 \
    && chown -R nextjs:nodejs /app
USER nextjs

EXPOSE 3000

CMD ["npm", "start"]
