# The web UI as static files behind nginx, which also proxies /v1 to the API.
# Build from web/: docker build -t da-agent-web:local web
# node:22.21.1-alpine3.22
FROM node@sha256:b2358485e3e33bc3a33114d2b1bdb18cdbe4df01bd2b257198eb51beb1f026c5 AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# nginxinc/nginx-unprivileged:1.29.3-alpine3.22: runs as a non-root user on port 8080.
FROM nginxinc/nginx-unprivileged@sha256:5aea7cc516b419e3526f47dd1531be31a56a046cfe44754d94f9383e13e2ee99
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
