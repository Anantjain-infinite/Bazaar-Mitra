# syntax=docker/dockerfile:1
#
# Development-mode image for the Next.js frontend. This intentionally runs
# `next dev` rather than a production build: the demo needs fast iteration
# on the new /buyer and /merchant/* pages, and a production build requires
# every NEXT_PUBLIC_* var to be baked in at build time. Swap to a multi-stage
# `next build && next start` image before any real deployment.
FROM node:20-bookworm-slim

RUN corepack enable && corepack prepare pnpm@latest --activate

WORKDIR /app

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

EXPOSE 3000

CMD ["pnpm", "dev"]
