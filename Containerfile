FROM rust:1-slim-bookworm@sha256:24707e70a4f1e23d9a7b25202ec43126d28a52294b96aad5c670df1fd1f1d400 AS builder

RUN apt-get update \
  && apt-get install --yes --no-install-recommends ca-certificates musl-tools \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY Cargo.toml Cargo.lock ./
COPY src ./src

FROM builder AS test

RUN rustup component add clippy rustfmt \
  && cargo fmt --check \
  && cargo clippy --locked -- -D warnings \
  && cargo test --locked

FROM builder AS build

ARG TARGETARCH

RUN case "$TARGETARCH" in \
      amd64) target=x86_64-unknown-linux-musl ;; \
      arm64) target=aarch64-unknown-linux-musl ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
  && rustup target add "$target" \
  && cargo build --locked --release --target "$target" \
  && cp "target/$target/release/karakeep-opds" /karakeep-opds

FROM scratch

LABEL org.opencontainers.image.title="karakeep-opds"
LABEL org.opencontainers.image.description="OPDS bridge for Karakeep bookmarks"

COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /karakeep-opds /karakeep-opds

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["/karakeep-opds", "healthcheck"]

USER 65532:65532

ENTRYPOINT ["/karakeep-opds"]
