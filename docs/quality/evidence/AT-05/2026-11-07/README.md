# AT-05 synthetic mobile evidence — 2026-11-07

- Tested commit: `ed0bd7e54a8a20d5f210005839cdc2ec6f94f83a` (the evidence-only commit follows it).
- Dataset: `synthetic-demo` from `fixtures/demo`. All posts, people, links and images are synthetic.
- Chromium, viewport 360 × 800 CSS px. AT-05 measured **2 taps** from the visible captured-image card to its evidence image and focusable permalink. The crop and link were both in the viewport after navigation.
- `feed.png`, `post.png`, `evidence-image.png`, `evidence-missing.png`, `seller.png`, `watches.png`, `groups.png` are screenshots from the Playwright run. Screenshots open each route directly at the top; the tap test uses the actual navigation path.

Commands (PowerShell, repository root; PostgreSQL 17 already available at 127.0.0.1:55432, with an isolated empty database named `paiya_a5b_round2_20261107`):

```powershell
$env:UV_NO_SYNC='1'; $env:UV_OFFLINE='1'
$env:PAIYA_DATABASE_URL='postgresql+psycopg://paiya:paiya_dev_only@127.0.0.1:55432/paiya_a5b_round2_20261107'
$env:PAIYA_FIXTURE_MEDIA_ROOT='F:/paiya-wt/a5b-round2-temp-20261107/media'
$env:PAIYA_READ_CONTEXT_GROUP_KEY='synthetic-demo'; $env:PAIYA_PORT='18037'
uv run alembic upgrade head
uv run python -m paiya.cli demo seed
# Start .venv/Scripts/python.exe -m paiya.serve with the same environment.
cd web
corepack pnpm install --frozen-lockfile --offline
corepack pnpm build
$env:PLAYWRIGHT_BASE_URL='http://127.0.0.1:18037'
$env:PAIYA_EVIDENCE_DIR='F:/paiya-wt/feat-37b/docs/quality/evidence/AT-05/2026-11-07'
corepack pnpm e2e
```

Cleanup of the dedicated server, temporary media directory and database was rejected by automatic command approval and remains outstanding. These results are synthetic fixture evidence, not a live pilot or MCP image comparison.
