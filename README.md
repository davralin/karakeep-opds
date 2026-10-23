# Karakeep OPDS

Karakeep OPDS exposes Karakeep bookmarks as OPDS catalogs for e-readers.

This project recreates and continues the idea from
[`yazdipour/karakeep-opds`](https://github.com/yazdipour/karakeep-opds), which appears inactive.

## Features

- OPDS 1.2 Atom catalog endpoints.
- OPDS 2 JSON catalog endpoints.
- Basic Auth for catalog, asset, and EPUB routes.
- Unauthenticated `/healthz` endpoint for container and Kubernetes probes.
- On-demand EPUB generation for bookmarks.
- Karakeep asset proxying for cover images and readable content assets.
- Stateless runtime: no database, cache, persistence volume, or background jobs.

## Endpoints

- `GET /healthz`: health check, no authentication.
- `GET /opds`: OPDS 1.2 navigation feed.
- `GET /opds.atom`: OPDS 1.2 navigation feed.
- `GET /opds.json`: OPDS 2 navigation feed.
- `GET /opds/bookmarks.atom`: OPDS 1.2 bookmark acquisition feed.
- `GET /opds/bookmarks.json`: OPDS 2 bookmark acquisition feed.
- `GET /opds/archived.atom`: OPDS 1.2 archived bookmark acquisition feed.
- `GET /opds/archived.json`: OPDS 2 archived bookmark acquisition feed.
- `GET /opds/bookmarks/{bookmark_id}.epub`: generated EPUB for one bookmark.
- `GET /opds/assets/{asset_id}`: authenticated proxy for Karakeep-owned assets.

## Configuration

Runtime configuration is provided through environment variables.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `KARAKEEP_BASE_URL` | yes | | Karakeep base URL, for example `https://karakeep.example.org`. |
| `KARAKEEP_API_TOKEN` | yes | | Karakeep API token. Needs bookmark and asset read access. |
| `OPDS_USERNAME` | yes | | Basic Auth username for OPDS clients. |
| `OPDS_PASSWORD` | yes | | Basic Auth password for OPDS clients. |
| `KARAKEEP_API_PATH` | no | `/api/v1` | Karakeep API path below the base URL. |
| `OPDS_PAGE_SIZE` | no | `50` | Bookmarks per catalog page, 1 to 100. |
| `SERVICE_BASE_URL` | no | request URL | External base URL used when rendering absolute OPDS links. |
| `LOG_LEVEL` | no | `INFO` | Application log level (`ERROR`, `WARN`, `INFO`, `DEBUG`, or `TRACE`). |

## Docker

Build locally:

```sh
docker build -f Containerfile -t karakeep-opds:local .
```

Run locally:

```sh
docker run --rm \
  -p 8000:8000 \
  -e KARAKEEP_BASE_URL="https://karakeep.example.org" \
  -e KARAKEEP_API_TOKEN="..." \
  -e OPDS_USERNAME="reader" \
  -e OPDS_PASSWORD="change-me" \
  -e SERVICE_BASE_URL="http://localhost:8000" \
  karakeep-opds:local
```

Test from the host:

```sh
curl http://localhost:8000/healthz
curl -u reader:change-me http://localhost:8000/opds
curl -u reader:change-me http://localhost:8000/opds/bookmarks.json
```

## Development

Run checks:

```sh
docker build --target test -f Containerfile .
```

Build and exercise the complete final image against a local Karakeep API stub and EPUBCheck:

```sh
docker build -f Containerfile -t karakeep-opds:local .
python3 tests/deployment_check.py --image karakeep-opds:local
```

## Release

This repository publishes a single container image to GHCR using the inherited CalVer, SLSA, and
vulnerability scanning workflows documented in `adr/`.
