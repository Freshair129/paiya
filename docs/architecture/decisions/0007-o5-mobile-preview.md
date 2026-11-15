# ADR 0007 · Synthetic O5 mobile preview

**Status** Proposed implementation for synthetic testing under [owner approval in Issue #112](https://github.com/Freshair129/paiya/issues/112#issuecomment-6893368560). The remote deployment and physical phone test are pending.

## Boundaries

- The browser uses one stable HTTPS Vercel origin for the Vite UI and `/v1/*`. An [external Vercel route](https://vercel.com/docs/routing/rewrites#restricting-your-origin-to-vercel-traffic) overwrites `X-Paiya-Proxy-Key` from a project secret. The Docker API accepts only that key and the configured HTTPS ingress Host. It compares the actual browser Origin to the configured Vercel origin on writes. Treat any untrusted forwarding headers as ordinary headers. The ingress must preserve its configured Host and the browser Origin across the hop.
- The preview requires the dedicated Compose synthetic database at startup, uses independent media and owner verifier volumes, and publishes only the API to the PC's loopback. A separately configured HTTPS ingress forwards only that port. The preview's read access is pinned to `synthetic-demo` and writes are limited to authentication. No Collector, B2 database, or real source data is part of this deployment. Keep [ADR 0004](0004-local-access-and-mcp.md) and [S1](../contracts/owner-session.md) as the default for local installs.
- Run one API container with the existing scrypt owner passphrase, pre-auth nonce, attempt limit, server-side sessions and CSRF. On a new phone or browser, supply the separate 32-byte pairing code along with the passphrase. It binds a host-only `__Host-paiya_o5_device` cookie to this preview installation for up to 30 days. The browser must present both its pairing cookie and unexpired login session on protected routes. HTTPS cookies are Secure, HttpOnly and SameSite=Strict. The API and gateway use `no-store`.
- This is a single-owner preview. Rotate the pairing code and recreate the API container when retiring a paired phone; this invalidates all previously paired browsers and they must pair again. Use the existing owner session revoke-all in addition when retiring active sessions. No per-device registry is used. To stop the preview, disable the HTTPS ingress/Vercel route and stop the Compose project.

## Run the synthetic preview

1. Set private Compose variables `PAIYA_O5_ORIGIN` (stable HTTPS Vercel origin), `PAIYA_O5_BACKEND_HOST` (the HTTPS ingress hostname), `PAIYA_O5_DB_PASSWORD` (URL-safe), `PAIYA_O5_PROXY_KEY`, and `PAIYA_O5_PAIRING_CODE`. Use separate random 64-character lowercase hex values for the two keys. Only the proxy key is installed in Vercel, as a server-side secret without a `VITE_` prefix. Keep credentials outside source and logs.
2. Provision the Compose stack from the repository root. It starts with an empty PostgreSQL database. Set a preview-only passphrase at the interactive prompt.

   ```sh
   docker compose -f compose.o5.yaml build api
   docker compose -f compose.o5.yaml run --rm api alembic upgrade head
   docker compose -f compose.o5.yaml run --rm api python -m paiya.cli demo seed
   docker compose -f compose.o5.yaml run --rm api python -m paiya.cli auth set-passphrase
   docker compose -f compose.o5.yaml up -d
   ```

3. Point the approved HTTPS ingress at `127.0.0.1:8785`. Link the `web` directory to the selected preview project, add the proxy key as a preview secret and require owner-only Vercel Authentication on the exact alias, including `/v1/*`. Pin `PAIYA_O5_ORIGIN` to that exact domain. From the repository root in PowerShell 7, generate the config and explicitly apply it to the preview deployment.

   ```powershell
   $previewConfig = Join-Path ([IO.Path]::GetTempPath()) 'paiya-o5-vercel.json'
   node web/scripts/o5-vercel-config.mjs https://paiya-api.example.test | Set-Content -LiteralPath $previewConfig -Encoding utf8
   vercel deploy --cwd web --local-config $previewConfig
   ```

4. On a real phone, open the protected URL, enter the preview owner passphrase and paste the pairing code when prompted. Verify the synthetic dashboard at `/admin/health`, reload, and sign out. A request without login or to the API origin directly must fail. Retest with the old device cookie after rotating the pairing code. Scope remains synthetic until a separate decision on real-data access.

## Evidence

Hosted `o5-preview` CI builds Compose, migrates and seeds its own PostgreSQL, and tests trusted gateway request handling, pairing, cookies, CSRF, data and logout. It emulates downstream gateway headers at the container. The actual Vercel route, HTTPS transmission and physical phone are checked in the separately provisioned preview.
