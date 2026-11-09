# Changelog

All notable changes are recorded here. Versions follow [Semantic Versioning](https://semver.org/) and stay at `v0.x` until the go-live gate C1 in [the delivery plan](docs/delivery/ROADMAP.md) passes ([Issue #42](https://github.com/Freshair129/paiya/issues/42)).

## [Unreleased]

### Phase A — synthetic fixture workflow

- Added Thai synthetic scenarios and labelled answers (A1, PR #9); fixture ingest, evidence reads, discovery groups and queue (A2–A3, PR #14, #17, #24, #25).
- Added item status and price facts, timeline, seller reads, watches and revisits (A4, PR #28–#36).
- Added the mobile read API, demo seed and PWA (A5, PR #39, #41, #45).
- Added extraction tags, local-model enrichment, model filters and read-only MCP (A6a–A6d, PR #49–#62).
- Added asking-price trends (A7a, PR #40, #47). A7b moved to B1a and is not included (Issue #38, PR #63).
- Added grant revocation, scoped purge and startup replay refusal (A8, PR #64–#71).
- Added the Phase A acceptance map, labelled corpus and acceptance report (A9, PR #73–#75).

### Operations

- Added backup create, verify and restore for O1a (PR #79, #80). O1b encryption, key handling, retention and in-place restore are not included; these remain undecided in Issue #78.
- Added system health reporting, `paiya health` and the admin health view for O2 (PR #82, #83).
- Added the O4 continuity contract (PR #85). The worker and drill implementation is present on the stacked `feat/o4-continuity` branch; its implementation PR number is not available in `origin/main` yet.
- Added the Windows install/upgrade/rollback runbook and offline release smoke check for O3 (Issue #87; PR pending).
