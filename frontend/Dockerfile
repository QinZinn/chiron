# Frontend — Vite + React, served by `vite preview`.

FROM node:24-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

# Built when the container STARTS, not when the image is built. The browser's
# public config (CHIRON_MNEMOSYNE_URL, timezone) is baked into
# the bundle at build time and comes from env_file, which only exists at run
# time — and secrets must never be in an image layer anyway.
#
# `vite preview` rather than a static server: the proxy that holds the KS
# secret is a Vite plugin (server/chironProxy.ts), and preview runs it. A plain
# static server would ship a UI whose Kiến thức and Scan ghi chép areas have
# nothing to call.
ENV CHIRON_BIND_HOST=0.0.0.0
EXPOSE 4173
CMD ["sh", "-c", "npx vite build && exec npx vite preview"]
