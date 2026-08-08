# 0009. Build Stateless Karakeep OPDS Service

Date: 2026-08-08

## Status

Accepted

## Context

This repository now hosts an application instead of only inherited repository plumbing.

The service needs to expose Karakeep bookmarks to OPDS clients while keeping the runtime simple to
operate in Kubernetes and compatible with the existing single-image release workflow.

## Decision

Build one Python/FastAPI service image that exposes OPDS 1.2 Atom, OPDS 2 JSON, authenticated asset
proxying, and on-demand EPUB generation.

The service reads Karakeep through its HTTP API using a read-only application token. OPDS clients use
HTTP Basic Auth configured separately from the Karakeep token.

The service is stateless. It must not require a database, persistent volume, cache, queue, or
background worker. EPUB files are generated per request from bookmark metadata, readable HTML content,
or Karakeep content assets.

The image is a long-running service image, so it defines a real healthcheck for `/healthz` as required
by ADR 0008.

## Consequences

The existing single-image workflow remains the correct release topology.

Deployments only need environment variables and network access to Karakeep.

Large bookmarks are converted on demand, so slow EPUB requests are possible if Karakeep asset fetches
are slow.
