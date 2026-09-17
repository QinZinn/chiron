# syntax=docker/dockerfile:1
# Mnemosyne — multi-stage: build with the full Rust toolchain, ship one binary.

FROM rust:1.98-trixie AS build
WORKDIR /src
COPY . .
# Cache mounts keep the crate registry and target/ between builds, so a code
# change recompiles the backend rather than every dependency. The binary is
# copied out because target/ is a cache mount and does not survive the step.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target \
    cargo build --release -p backend \
 && cp target/release/backend /backend

FROM debian:trixie-slim
# ca-certificates: reqwest (rustls) talks TLS to DeepSeek and Todoist.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /backend /usr/local/bin/backend

# In a container 127.0.0.1 is the container's own loopback, unreachable from the
# host and from other services; the host side stays 127.0.0.1-only through the
# port mapping in docker-compose.yml.
ENV MNEMOSYNE_BIND=0.0.0.0:8081
EXPOSE 8081

# `migrate` builds an empty database from schema.sql and applies anything
# pending on an existing one, then the server starts. It needs no LLM key, so a
# misconfigured provider fails the server, not the schema.
CMD ["sh", "-c", "backend migrate && exec backend"]
