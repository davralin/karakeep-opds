# 0010. Rewrite The Service In Rust

Date: 2026-10-23

## Status

Accepted

## Context

The service has a validated public contract: Karakeep HTTP requests, Basic-authenticated OPDS feeds,
asset proxying, text-first EPUB generation, and an EPUBCheck-backed deployed-image test. The Python
implementation required a full Python runtime and its web framework in the production image.

## Decision

Replace the Python/FastAPI application implementation in ADR 0009 with one Rust binary. Preserve the
same single, stateless service topology, HTTP route surface, environment configuration, and on-demand
EPUB behavior.

Build a statically linked musl binary in a digest-pinned Rust builder and run it in a `scratch` image as
the existing non-root numeric user. Copy system CA certificates into the image because the service makes
outbound HTTPS requests to Karakeep. The binary owns the healthcheck request so the `scratch` image does
not need Python, a shell, or a HTTP client utility.

Rust unit tests and the existing deployed-container contract harness are required before publication.
The harness continues to run EPUBCheck against an EPUB obtained from the actual final image.

## Consequences

ADR 0009's service architecture and operational constraints remain in force, except for its Python and
FastAPI implementation choice.

The runtime image has no language interpreter or package manager. The Rust dependency lockfile becomes
the source of dependency update tracking under ADR 0005.
