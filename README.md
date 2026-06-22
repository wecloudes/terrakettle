<h1 align="center">Terrakettle</h1>
<p align="center"><strong>Where Terrahawk reports come to rest</strong></p>

Terrakettle is a lightweight web service that **stores and serves [Terrahawk](../terrahawk) scan reports** for many projects. Terrahawk runs in CI and pushes its report to Terrakettle; Terrakettle keeps the history per project and serves the original interactive HTML report (plus the raw JSON) from a browser.

- **Per-project history** — every run is filed under a project and listed newest-first with status counts (clean / drift / error / timeout), a drift/error trend sparkline, status filtering and pagination.
- **Push API** — a single authenticated endpoint Terrahawk uploads to after each scan.
- **Object storage for payloads** — report files live in S3 / Azure Blob / GCS (or local disk for dev); a small SQLite index holds the metadata.
- **Per-project tokens** — each project gets its own bearer token (with optional expiry and rate limiting); project/token management is guarded by an admin key.
- **Serves the real report** — the Terrahawk HTML and its `_data.js` are served as siblings, so the interactive report works unchanged.
- **View authentication** — optionally gate the whole web UI behind a password login; status badges stay public for embedding.
- **Status badges, diffs & feeds** — an SVG badge, a run-to-run compare view, and JSON/RSS feeds per project.
- **Notifications** — fire a Slack / Teams / generic webhook when a pushed run has drift or errors.
- **Observability** — Prometheus `/metrics` and a deep `/healthz` that checks the DB and storage.

---

## Architecture

```
  Terrahawk (CI)                 Terrakettle                     Browser
 ┌───────────────┐   push     ┌──────────────────────┐   view   ┌─────────┐
 │ terrahawk_*.  │ ─────────► │ FastAPI              │ ───────► │ project │
 │  json/html/js │  POST      │  ├─ SQLite (index)   │   HTML   │ history │
 └───────────────┘  /api/v1/  │  └─ object storage   │          │ + report│
                    runs      │     (S3/Azure/GCS)   │          └─────────┘
                              └──────────────────────┘
```

- **Metadata** (projects, tokens, run summaries) → SQLite (`TERRAKETTLE_DB_PATH`).
- **Payloads** (`.html`, `_data.js`, `.json`) → object storage under `prefix/{project}/{run_id}/`.

---

## Quick Start

```bash
pip install -e .          # local backend, no cloud SDK needed

export TERRAKETTLE_ADMIN_KEY="$(openssl rand -hex 16)"

# Create a project and mint a push token (CLI talks to SQLite directly):
terrakettle create-project acme --name "Acme Infra"
terrakettle mint-token acme --label ci      # prints the token once

terrakettle serve --port 8000               # open http://localhost:8000
```

Project and token management is also available over HTTP (guarded by the admin key) — see [API](#api).

---

## Configuration

All settings are environment variables prefixed `TERRAKETTLE_`.

| Variable | Default | Description |
|----------|---------|-------------|
| `TERRAKETTLE_ADMIN_KEY` | `change-me` | Bearer key for project/token management. **Set this.** |
| `TERRAKETTLE_DB_PATH` | `terrakettle.db` | SQLite metadata index path |
| `TERRAKETTLE_MAX_UPLOAD_BYTES` | `67108864` | Per-file upload cap (64 MiB) |
| `TERRAKETTLE_PAGE_SIZE` | `50` | Run-listing pagination size on the project page |
| `TERRAKETTLE_INSECURE` | `false` | When `false`, the server **refuses to start** if `ADMIN_KEY` is still the default `change-me`. Set `true` only for trusted/dev use |
| `TERRAKETTLE_VIEW_PASSWORD` | — (empty) | When set, the web UI requires a password login (signed session cookie). Empty = open viewing (only OK behind a trusted network) |
| `TERRAKETTLE_SESSION_SECRET` | — (empty) | Secret for signing session cookies. Empty = derived from `ADMIN_KEY` |
| `TERRAKETTLE_SESSION_TTL` | `604800` | Session cookie lifetime in seconds (7 days) |
| `TERRAKETTLE_TOKEN_TTL_DAYS` | `0` | Default push-token expiry in days. `0` = never expires |
| `TERRAKETTLE_PUSH_RATE_PER_MIN` | `0` | Per-token push rate limit (pushes/minute). `0` = unlimited |
| `TERRAKETTLE_NOTIFY_WEBHOOK` | — (empty) | Webhook URL called on pushes with drift/errors. Empty = disabled |
| `TERRAKETTLE_NOTIFY_FORMAT` | `slack` | Webhook payload flavor: `slack` \| `teams` \| `generic` |
| `TERRAKETTLE_PUBLIC_URL` | — (empty) | Public base URL of this server, used to build absolute links in notifications and feeds |
| `TERRAKETTLE_REPORT_CSP` | *(see note)* | `Content-Security-Policy` header applied when serving stored report HTML |
| `TERRAKETTLE_MAX_RUNS_PER_PROJECT` | `0` | Keep at most N runs/project (oldest pruned on push). `0` = unlimited |
| `TERRAKETTLE_SIGNED_URLS` | `true` | Redirect sidecar files (`data.js`/`json`) to presigned object-store URLs when supported |
| `TERRAKETTLE_SIGNED_URL_TTL` | `300` | Presigned URL lifetime (seconds) |
| `TERRAKETTLE_STORAGE_BACKEND` | `local` | `local` \| `s3` \| `azure` \| `gcs` |
| `TERRAKETTLE_STORAGE_BUCKET` | `terrakettle_data` | Bucket/container name, or base dir for `local` |
| `TERRAKETTLE_STORAGE_PREFIX` | `reports` | Key prefix inside the bucket |
| `TERRAKETTLE_S3_ENDPOINT_URL` | — | S3-compatible endpoint (e.g. MinIO) |
| `TERRAKETTLE_S3_REGION` | — | S3 region |
| `TERRAKETTLE_AZURE_CONNECTION_STRING` | — | Azure Blob connection string (required for SAS presigning) |
| `TERRAKETTLE_AZURE_ACCOUNT_URL` | — | Azure account URL (uses `DefaultAzureCredential`; no presigning) |

The default `REPORT_CSP` permits the inline scripts and the jsDelivr CDN that
Terrahawk reports need, while forbidding framing (`frame-ancestors 'none'`). See
[Security hardening](#security-hardening) for stronger isolation options.

### Serving & signed URLs

The report HTML is always proxied through Terrakettle (so the page URL stays on
your domain). Its sidecar files — the large `_data.js` and the raw `.json` — are
**redirected to short-lived presigned object-store URLs** when the backend can
sign them (`s3`, `gcs`, and `azure` with a connection string), so the object
store serves those bytes directly. When signing is unavailable (`local`, or
Azure with `DefaultAzureCredential`), Terrakettle transparently proxies them
instead. Toggle with `TERRAKETTLE_SIGNED_URLS`.

Cloud SDKs are optional extras — install only the one you use:

```bash
pip install ".[aws]"     # boto3
pip install ".[azure]"   # azure-storage-blob
pip install ".[gcp]"     # google-cloud-storage
```

GCS credentials come from the standard `GOOGLE_APPLICATION_CREDENTIALS` chain.

---

## View authentication

By default the web UI is **open** — anyone who can reach the server can browse
projects and reports. That is only acceptable behind a trusted network. Set
`TERRAKETTLE_VIEW_PASSWORD` to gate the entire UI behind a login:

```bash
export TERRAKETTLE_VIEW_PASSWORD="$(openssl rand -hex 16)"
```

When set, unauthenticated browser `GET`s are redirected to `/login`; a correct
password issues a signed, HTTP-only session cookie (`tk_session`, signed with
`SESSION_SECRET` or a value derived from `ADMIN_KEY`, valid for `SESSION_TTL`
seconds). `/logout` clears it.

What stays reachable without a session:

- **API push** (`/api/v1/...`) — authenticated with project bearer tokens, as before.
- **Status badges** (`/p/{slug}/badge.svg`) — public, so they can be embedded in
  READMEs even with view-auth on.
- **Health & metrics** (`/healthz`, `/metrics`) and the `/login` / `/logout` routes.

---

## Security hardening

- **No insecure default key.** Unless `TERRAKETTLE_INSECURE=true`, the server
  refuses to start while `ADMIN_KEY` is still `change-me`.
- **Hardened report serving.** Stored report HTML (and its sidecars) are served
  with a `Content-Security-Policy` (`REPORT_CSP`) and `X-Content-Type-Options:
  nosniff`. For strong isolation against malicious report content, serve reports
  from a **separate origin** from the rest of the UI.
- **SQLite in WAL mode** for better concurrency; **schema migrations are
  applied automatically** on startup.

---

## API

### Push a report (Terrahawk → Terrakettle)

`POST /api/v1/runs` — `Authorization: Bearer <project-token>`, `multipart/form-data`:

| Field | Required | Description |
|-------|----------|-------------|
| `report` | yes | The `terrahawk_*.json` results file |
| `html` | no | The `terrahawk_*.html` report |
| `data_js` | no | The `terrahawk_*_data.js` sidecar |
| `run_id` | no | Run identifier (default: report filename stem) |

The project is determined by the token. Status counts are parsed from the JSON.

### Manage projects & tokens (admin)

| Method | Path | Body | Auth |
|--------|------|------|------|
| `POST` | `/api/v1/projects` | `{"slug","name"}` | admin key |
| `DELETE` | `/api/v1/projects/{slug}` | — | admin key |
| `POST` | `/api/v1/projects/{slug}/tokens` | `label`, `ttl_days` (form) | admin key |
| `GET` | `/api/v1/projects/{slug}/tokens` | — | admin key |
| `DELETE` | `/api/v1/projects/{slug}/tokens/{id}` | — | admin key |

`DELETE /api/v1/projects/{slug}` removes the project, **all** of its runs, and
their stored report files (the storage delete cascades).

Token listing never returns the secret (only id, label, created/last-used) —
the plaintext is shown once at mint time. Revoke by id. Pass `ttl_days` to
override the server's default token expiry (`TOKEN_TTL_DAYS`). The same
operations are available from the CLI:

```bash
terrakettle mint-token acme --label ci --ttl-days 90
terrakettle list-tokens acme
terrakettle revoke-token acme 3
terrakettle delete-project acme          # project + runs + stored files
```

### Token expiry & rate limiting

- **Expiry** — `TOKEN_TTL_DAYS` (or `--ttl-days` / the `ttl_days` form field) sets
  how long a minted token stays valid. `0` = never expires. Expired tokens are
  rejected on push.
- **Rate limiting** — `PUSH_RATE_PER_MIN` caps how many pushes a single token may
  make per minute (`0` = unlimited).

### Notifications

When `NOTIFY_WEBHOOK` is set, a webhook fires on every pushed run that has drift
or errors (clean runs are silent). The payload shape follows `NOTIFY_FORMAT`:
`slack` (default), `teams` (MessageCard), or `generic` (raw JSON with project,
run id, summary, and a report link). Notifications are fire-and-forget — a
failed webhook is logged but never blocks or fails the push. Set `PUBLIC_URL`
so the links in notifications point at a reachable address.

### View (browser)

| Path | Description |
|------|-------------|
| `/` | Project list |
| `/login`, `/logout` | Session login / logout (only active when `VIEW_PASSWORD` is set) |
| `/p/{slug}` | Run history: status counts, drift/error trend sparkline, status filter, pagination |
| `/p/{slug}/runs/{run_id}` | Run detail page — links to the full report, raw JSON, and compare |
| `/p/{slug}/runs/{run_id}/` | The interactive Terrahawk HTML report |
| `/p/{slug}/runs/{run_id}/{file}` | Sibling files (`_data.js`, `.json`) |
| `/p/{slug}/badge.svg` | **Public** SVG status shield for the latest run (clean / drift / error) |
| `/p/{slug}/compare?base=&target=` | Diff two runs — which units changed status |
| `/p/{slug}/feed.json`, `/p/{slug}/feed.xml` | Recent-runs feed (JSON and RSS 2.0) |

The project page renders a status badge, a drift/error sparkline over recent
runs, a status filter (clean / drift / error / timeout) and pagination (page
size `PAGE_SIZE`). The run detail page wraps a run with Terrakettle chrome and
links out to the full report, its raw JSON, and the compare view.

### Status badge

Embed the latest-run shield in a project README — it stays reachable even with
view authentication enabled:

```markdown
![terrahawk](https://terrakettle.example.com/p/acme/badge.svg)
```

It reads `clean` (green), `N drift` (orange), or `N error` (red) from the most
recent run, and `no data` (grey) when the project has no runs yet.

### Ops endpoints

| Path | Description |
|------|-------------|
| `/healthz` | Deep health check — returns `{status, version, db, storage}` (`status` is `ok` only when both the DB and storage probes pass) |
| `/metrics` | Prometheus text exposition — build info, project/run totals, and last drift/error per project |

---

## Retention

Old runs (index rows **and** their stored files) are pruned two ways:

- **Automatic** — set `TERRAKETTLE_MAX_RUNS_PER_PROJECT=N`; each push keeps only
  the newest N runs of that project.
- **Manual** — the `prune` CLI:

  ```bash
  terrakettle prune --keep 30            # newest 30 per project, all projects
  terrakettle prune --keep 30 --project acme
  terrakettle prune --older-than 90      # runs older than 90 days
  ```

Deleting a project (`delete-project` CLI or `DELETE /api/v1/projects/{slug}`)
also cascades to storage, removing every stored report file for that project.

---

## Pushing from CI

Terrahawk pushes natively — point it at Terrakettle and it publishes the report
at the end of every scan:

```bash
terrahawk --root-dir /workspace \
  --push-url https://terrakettle.example.com \
  --push-token "$TERRAKETTLE_TOKEN"     # or set $TERRAKETTLE_TOKEN
```

Both can also live in `.terrahawk.yml` (`push_url:` / `push_token:`). A push
failure prints a warning but never fails the scan.

Alternatively, push out-of-band with the bundled stdlib-only client (no
dependencies — runs inside the Terrahawk image):

```bash
python3 scripts/push.py \
  --url   https://terrakettle.example.com \
  --token "$TERRAKETTLE_TOKEN" \
  --results-dir terrahawk_results
```

Or with `curl`:

```bash
RID=$(basename terrahawk_results/terrahawk_*.json .json)
curl -X POST https://terrakettle.example.com/api/v1/runs \
  -H "Authorization: Bearer $TERRAKETTLE_TOKEN" \
  -F "run_id=$RID" \
  -F "report=@terrahawk_results/$RID.json" \
  -F "html=@terrahawk_results/$RID.html" \
  -F "data_js=@terrahawk_results/${RID}_data.js"
```

### GitHub Actions

```yaml
      - name: Run Terrahawk & publish
        env:
          TERRAKETTLE_TOKEN: ${{ secrets.TERRAKETTLE_TOKEN }}
        run: |
          docker run --rm -v "$PWD":/workspace -e TERRAKETTLE_TOKEN terrahawk:aws \
            --root-dir /workspace --push-url https://terrakettle.example.com
```

---

## Docker

```bash
docker build --build-arg CLOUD=azure -t terrakettle:azure .

docker run --rm -p 8000:8000 \
  -e TERRAKETTLE_ADMIN_KEY="$ADMIN_KEY" \
  -e TERRAKETTLE_STORAGE_BACKEND=azure \
  -e TERRAKETTLE_STORAGE_BUCKET=terrakettle \
  -e TERRAKETTLE_AZURE_CONNECTION_STRING="$AZURE_CONN" \
  -v terrakettle-data:/data \
  terrakettle:azure
```

`CLOUD` ∈ `local` | `aws` | `azure` | `gcp`. The `/data` volume persists the
SQLite index (and, for the `local` backend, the report files).

---

## Project Structure

```
terrakettle/
├── terrakettle.py            # entrypoint shim (python3 terrakettle.py ...)
├── pyproject.toml
├── Dockerfile                # multi-cloud images via --build-arg CLOUD
├── scripts/push.py           # stdlib-only CI push client
├── tests/                    # pytest suite
├── .github/workflows/        # CI (test + syntax) and publish (Docker images)
└── src/terrakettle/
    ├── __main__.py           # CLI: serve / create-project / mint-token / delete-project / ...
    ├── app.py                # FastAPI app factory + view-auth middleware
    ├── config.py             # env-based settings
    ├── db.py                 # SQLite metadata index (WAL, auto-migrations)
    ├── storage.py            # object-storage backends (put/get/delete/presign)
    ├── retention.py          # run pruning (files + index rows)
    ├── auth.py               # admin key + push tokens + view sessions
    ├── api.py                # push + admin (project/token) endpoints
    ├── web.py                # HTML views, login, run detail + report serving
    ├── badge.py              # public SVG status badge
    ├── compare.py            # run-to-run diff view
    ├── feed.py               # per-project JSON + RSS feeds
    ├── metrics.py            # Prometheus /metrics + deep health check
    ├── notify.py             # Slack/Teams/generic push notifications
    ├── schemas.py            # response models + report summarizer
    └── templates/            # Jinja2 pages (index, project, run, compare, login, ...)
```

---

## Testing

```bash
pip install -e ".[dev]"
pytest
```

CI (`.github/workflows/ci.yml`) runs the suite on Python 3.12 plus a compile
check; `publish.yml` builds and pushes the multi-cloud Docker images
(`local` / `aws` / `azure` / `gcp`) to a registry on `v*` tags.

---

## License

[MIT](LICENSE) · Made with <3 by [WeCloud](https://www.wecloud.es/).
