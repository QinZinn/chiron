# Mnemosyne — multi-stage: build with the full Rust toolchain, ship one binary.
#
# Written for the classic builder too, not only BuildKit: `RUN --mount=type=cache`
# would be neater, but a Docker without buildx rejects it outright, and "runs on
# any Docker" is the point of this file.

FROM rust:1.98-trixie AS build
WORKDIR /src

# Dependencies first, against placeholder sources. This layer is cached until a
# Cargo.toml or Cargo.lock changes, so an ordinary code edit recompiles the two
# crates of this workspace rather than every dependency (minutes, not seconds).
COPY Cargo.toml Cargo.lock ./
COPY backend/Cargo.toml backend/Cargo.toml
COPY mnemosyne-core/Cargo.toml mnemosyne-core/Cargo.toml
RUN mkdir -p backend/src mnemosyne-core/src \
 && echo 'fn main() {}' > backend/src/main.rs \
 && touch mnemosyne-core/src/lib.rs \
 && cargo build --release -p backend \
 && rm -rf backend/src mnemosyne-core/src

# Real sources. touch: cargo compares mtimes, and COPY can hand files an older
# timestamp than the placeholder build left behind, which would make cargo
# think nothing changed and ship the empty `fn main() {}`.
COPY . .
RUN touch backend/src/main.rs mnemosyne-core/src/lib.rs \
 && cargo build --release -p backend \
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
