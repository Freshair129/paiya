# Changelog

All notable changes are recorded here. Versions follow [Semantic Versioning](https://semver.org/) and stay at `v0.x` until the go-live gate C1 in [the delivery plan](docs/delivery/ROADMAP.md) passes ([Issue #42](https://github.com/Freshair129/paiya/issues/42)).

## [Unreleased]

- Nothing yet.

## [0.1.0] - 2026-11-09

### Phase A — synthetic fixture workflow

- Added Thai synthetic scenarios and labelled answers (A1, PR #9); fixture ingest, evidence reads, discovery groups and queue (A2–A3, PR #14, #17, #24, #25).
- Added item status and price facts, timeline, seller reads, watches and revisits (A4, PR #28–#36).
- Added the mobile read API, demo seed and PWA (A5, PR #39, #41, #45).
- Added extraction tags, local-model enrichment, model filters and read-only MCP (A6a–A6d, PR #49–#62).
- Added asking-price trends (A7a, PR #40, #47). A7b moved to B1a and is not included (Issue #38, PR #63).
- Added grant revocation, scoped purge and startup replay refusal (A8, PR #64–#71).
- Added the Phase A acceptance map, labelled corpus and acceptance report (A9, PR #73–#75).

### Operations

- Added backup create, verify and restore for O1a (PR #79, #80). O1b is not included: the owner decided in Issue #78 on BitLocker for the backup disk, `backup prune --dry-run`/`--apply` and a manual in-place restore, but these are not implemented yet.
- Added system health reporting, `paiya health` and the admin health view for O2 (PR #82, #83).
- Added the fixture worker `paiya worker run`, gated fault points and the continuity drill for O4 (PR #85, #86).
- Added the Windows install/upgrade/rollback runbook and offline release smoke check for O3 (Issue #87, PR #88).

### Decisions and contracts

- Accepted ADR 0002 (manual import for phase M) and ADR 0005 (owner-directed automated collection; the bot-detection evasion ban stays) (PR #90, #93).
- Added the M1 approval package (PR #90), the collector inbox contract (PR #92) and the owner-session contract (PR #95). Their implementations are not in this release.
