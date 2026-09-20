FROM node:22-alpine AS builder

WORKDIR /app
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY apps/frontend/package.json ./apps/frontend/package.json
COPY apps/frontend ./apps/frontend

RUN pnpm install --frozen-lockfile --filter frontend...
RUN pnpm --filter frontend build

FROM nginx:1.27-alpine AS runtime

COPY apps/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/apps/frontend/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD wget --quiet --output-document=- http://127.0.0.1:8080/healthz | grep -q ok

CMD ["nginx", "-g", "daemon off;"]
