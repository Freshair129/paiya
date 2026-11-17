# Paiya Collector

Collector reads synthetic Thai HTML fixtures offline and publishes inbox bundles that match the v1 contract in `Freshair129/paiya` at commit `ba7c095ee340bb229ea8038407d99abef3b10042`. A separate, manually started visible-Chrome adapter is implemented for the owner-approved scope in paiya Issue #91. No Facebook pilot has been run from this project.

## Offline fixture run

Open PowerShell at the paiya repository root and run this one command:

```powershell
.\collector\scripts\run-collector.ps1 discover --fixture .\fixtures\rtx3060.html --group-config .\fixtures\group.json --inbox F:\paiya-collector-data\deterministic-demo\inbox --state-dir F:\paiya-collector-data\deterministic-demo\state --run-id 30303030-2222-4333-8444-555555555555
```

The script uses Python 3.12 managed by uv. Runtime, venv, cache, and temporary files stay under the `collector/` subproject. The single-line JSON report contains `run_id`, absolute `bundle_path`, cards seen/parsed, `coverage`, `stop_reason`, and partial reasons. Replaying the same run id and input returns the existing bundle; changed input under that id is rejected.

The synthetic post is Thai: RTX 3060 12GB for 5,500 baht, 50 baht shipping, and a buyer comment “จองครับ”. Product and shipping prices are separate. The comment speaker is `other`; text and image evidence point back to the same post.

## Optional local Ollama extraction

If Ollama is running locally with `mellum:latest`, the offline fixture can pass the post text to the model:

```powershell
.\collector\scripts\run-collector.ps1 discover --fixture .\fixtures\rtx3060.html --group-config .\fixtures\group.json --inbox F:\paiya-collector-data\mellum-demo\inbox --state-dir F:\paiya-collector-data\mellum-demo\state --run-id 31313131-2222-4333-8444-555555555555 --agent-model mellum:latest
```

Python sends only one post's visible text to `127.0.0.1:11434`. It does not send raw HTML, comments, images, URLs, cookies, or profile data. Deterministic parsing validates model and shipping suggestions. Asking prices and units use the deterministic rules alone so an optional model cannot restore a price rejected as ambiguous or non-ask.

## Manually started visible Chrome adapter

The live adapter uses the owner-approved scope documented in [paiya Issue #91](https://github.com/Freshair129/paiya/issues/91): one approved group, visible post text, seller/item/price fields, visible comments, and product images; a dedicated browser profile; manual owner sign-in; explicit manual run; seven-day retention; and stop on login, checkpoint, or challenge. It does not enumerate members, read DMs or reactions, export session data, use stealth/fingerprint changes, or poll in the background.

Set up the optional Python Playwright client once from the repository root:

```powershell
.\collector\scripts\setup-browser.ps1
```

This installs the Python client into the F: project environment. It does not install or download a browser binary. The adapter uses installed Google Chrome through its local DevTools endpoint. Playwright documents that CDP attachment is Chromium-only and lower fidelity than its Playwright protocol; this code uses the documented connected-browser close operation to disconnect the client when the run ends ([connection docs](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp), [browser close docs](https://playwright.dev/python/docs/api/class-browser#browser-close)).

Create a private local group config outside the repository:

```powershell
New-Item -ItemType Directory -Force F:\paiya-collector-data | Out-Null
Copy-Item .\collector\fixtures\owner-group-01.example.json F:\paiya-collector-data\owner-group-01.json
```

Edit the copied file locally. Replace the `example.invalid` URL with the approved group's plain HTTPS group URL, use the matching active grant UUID and `identity_scope` from the local paiya mapping, and replace the example CSS selectors with selectors verified against the visible page. The example's grant is synthetic and its URL intentionally cannot run. The Collector checks the local config's `active`, scopes, and retention fields; it does **not** query paiya's grant database. The owner or operator must verify that the configured grant is active and matches this group before any live run. Keep the real URL and config out of Git and public issues.

Open a dedicated, visible Chrome profile on F: (never Profile 0 or an everyday Chrome/Edge profile):

```powershell
.\collector\scripts\open-dedicated-chrome.ps1
```

Sign in manually in that Chrome window. Do not enter credentials into the Collector. Leave the dedicated browser open. When the local mapping, matching active grant, and selectors have been verified, start one manual run with:

```powershell
.\collector\scripts\run-collector.ps1 discover-browser --group-config F:\paiya-collector-data\owner-group-01.json --profile-dir F:\paiya-collector-data\browser-profiles\owner-group-01 --inbox F:\paiya-collector-data\live-inbox --state-dir F:\paiya-collector-data\live-state --confirm-live
```

This command navigates the dedicated visible tab to the configured group, reads the visible feed within the configured scroll/time budgets, and atomically publishes a bundle. It is intentionally not run as part of offline validation. The image is captured from the visible product-image element. The optional `--agent-model mellum:latest` sends only post text to local Ollama; comments, images, profile data, and URLs remain in Python.

If the sort order cannot be verified, coverage stays partial and the checkpoint does not advance. For browser discovery, missing pinned or suggested selectors or an absent/false `post_markers_verified` mean effective chronological order is unverified: no overlap stop or checkpoint advance is allowed, even when the UI says newest posts. Set this local boolean to true only after verifying both selectors against the actual dedicated-profile page, including representative pinned and suggested posts. A configured selector alone does not prove it is correct. Login/challenge stops publish an empty `coverage=none` bundle and leave the checkpoint unchanged. Challenge text checks exclude recognized post and comment content but retain security dialogs and page-level notices; URL stop checks remain in force. If the security text scan exceeds its bounds, stop before reading cards. An unmarked challenge inside a post-text or comment subtree cannot be distinguished reliably from user-authored text; assess the actual page before any pilot. HTTP 429 creates only local cooldown state, honors `Retry-After`, publishes no invented v1 `error_code` or `stop_reason`, and keeps the checkpoint. If the response has no usable `Retry-After`, wait manually and then pass `--clear-manual-rate-limit-pause` to a later manual run.

Each discovery run removes evidence older than seven days before processing the feed. This includes published bundles, abandoned temporary bundles with a matching run record or temporary retention marker, comparison keys, checkpoint `top_keys`, and replay metadata. The operational checkpoint's `run_id` and `advanced_at` remain so a partial/none run never advances it. A cleanup run also takes the group's lock, so it cannot delete files during a live run. Inbox and state paths that traverse a symlink or junction are rejected.

Before any live pilot, install the cleanup-only Windows task. It runs at 3:00 AM and at user logon, never opens Chrome or contacts Facebook, and runs the same expiry command. It runs only while the machine is available and the current Windows user is logged in; an offline or sleeping device can apply the cleanup after it next wakes/logs in. The installer does not register a Facebook polling schedule:

```powershell
.\collector\scripts\install-retention-task.ps1
```

To inspect the task use `Get-ScheduledTask -TaskName PaiyaCollectorRetention-owner-group-01`; remove it with `Unregister-ScheduledTask -TaskName PaiyaCollectorRetention-owner-group-01` when retiring this local setup. For an immediate/manual cleanup, run:

```powershell
.\collector\scripts\run-collector.ps1 prune-expired --inbox F:\paiya-collector-data\live-inbox --state-dir F:\paiya-collector-data\live-state
```

It deletes only expired UUID bundles and abandoned temporary bundles with `group_key=owner-group-01`, and expires matching local state. It ignores unknown-group and unsafe entries, and rejects linked inbox/state paths. The task's dedicated action never reads browser state or source data. It inherits the current user's PowerShell execution policy.

If a process is forcibly terminated, its per-group lock can remain. The lock contains a run ID and process ID; the Collector deliberately does not remove a lock automatically. Verify no process is using this state directory before removing a stale lock. For the default live state directory, inspect all matches first:

```powershell
$LockPath = 'F:\paiya-collector-data\live-state\.owner-group-01.run.lock'
$LockBefore = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
$Active = Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -eq $LockBefore.process_id -or
    $_.CommandLine -like '*F:\paiya-collector-data\live-state*'
}
$Active | Select-Object ProcessId, Name, CommandLine
```

If `$Active` is empty, re-read the lock and confirm its `run_id` still matches `$LockBefore.run_id`, then remove only that exact lock:

```powershell
$LockAfter = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
if ($LockAfter.run_id -ne $LockBefore.run_id) { throw 'Lock changed; inspect again.' }
Remove-Item -LiteralPath $LockPath
```

If any matching process appears or ownership is unclear, leave the lock in place and inspect the run first.

Issue #76 is resolved using first-sighting wins: a repeated stable key is skipped, with changed content recorded in the local run summary. Revisit semantics and HTTP 429 still do not have a compatible inbox-v1 representation in upstream; do not invent a `sort`, `stop_reason`, or `error_code` value to make validation pass. Issue #91 remains open. Its owner comments approve implementation and synthetic tests; a live pilot still requires the actual local group mapping, a matching active grant, a dedicated visible browser profile, and a manual start.

## Tests

```powershell
.\collector\scripts\test-collector.ps1
```

All tests use offline synthetic data. The suite covers successful publication, unverified sorting, login/challenge, duplicate cards and changed duplicate content, overlap and budgets, 429 with and without a `Retry-After`, atomic publication interrupted before rename, replay idempotency, dedicated profile checks, seven-day expiry (including an abandoned temp bundle without `run.json`), linked-path rejection, and stale-lock preservation.

## Phase B offline acceptance

B1 pins the controlled Chrome tab to the configured scheme, host, and port. The runtime blocks cross-origin document requests before redirects or links reach the destination and checks the configured group before DOM extraction. Main-frame documents outside the configured group are also rejected. Same-origin login and challenge URLs are handled as stop conditions without card extraction. An in-flight navigation invalidates the snapshot.

The integration test runs direct links, HTTP 302, and a same-origin HTTP 307 followed by a cross-origin redirect against two loopback HTTP origins. Each path runs with an invented Authorization header, explicit Cookie header, and challenged HTTP Basic credentials. A same-origin redirect confirms that each credential is actually sent to the authorized origin.

Run from `collector/` with Python 3.12, the browser extra and dev dependencies installed. Set `PAIYA_B1_CHROME` to use the installed Chrome binary. CI installs Chromium as a separate setup step.

```powershell
uv run --locked --extra browser python -m pytest -q tests/b1_browser_origin_integration.py
$env:PYTHONPATH = (Get-Location).Path
uv run --locked python scripts/score_b1a.py --report b1a-score.json --review b1a-review.md
```

B1a scores the unassisted Collector parser on [100 synthetic cases](fixtures/b1a-price-cases.jsonl). The owner confirmed all 100 expected labels for [PR #111](https://github.com/Freshair129/paiya/pull/111). The rerun matched amount, currency, and item jointly in 64 of 64 clear-price cases and excluded all 36 non-comparable controls. The scorer marks the confirmed corpus as owner-reviewed by its content digest. A changed corpus requires a new owner review.

## Validate a bundle with paiya

For the deterministic fixture bundle, run this from `F:\paiya`:

```powershell
.\collector\scripts\validate-paiya-bundle.ps1 F:\paiya-collector-data\deterministic-demo\inbox\incoming\30303030-2222-4333-8444-555555555555
```

The validator wrapper keeps uv runtime, cache, environment, and temp on F:. For another Collector-created run, replace the final path with the absolute `bundle_path` returned by its CLI report.
