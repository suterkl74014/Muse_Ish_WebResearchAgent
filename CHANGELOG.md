# Changelog

This file is a concise index. Detailed implementation notes for each release remain in the versioned Markdown files in the repository root.

## v0.3.9.4

- Added human-verification/CAPTCHA detection outside the model loop.
- Automation pauses while the user completes verification in visible Chromium.
- Resumes after three consecutive clean observations.
- Does not attempt to solve or bypass the challenge.

See `V0.3.9.4-HUMAN-VERIFICATION-HANDOFF.md`.

## v0.3.9.3

- Added same-chat conversational memory for recent turns.
- Added browser action-schema validation and semantic repair.

See `V0.3.9.3-CONVERSATIONAL-MEMORY.md` and `V0.3.9.3-ACTION-SCHEMA-HOTFIX.md`.

## v0.3.9.2

- Added viewport-aware observations, element prioritization, and default scrolling.

See `V0.3.9.2-VIEWPORT-AWARE-SCROLLING.md`.

## v0.3.9.1

- Fixed Activity-pane autoscroll/position preservation.

See `V0.3.9.1-ACTIVITY-AUTOSCROLL.md`.

## v0.3.9

- Added same-model JSON repair for malformed planner/browser actions.

See `V0.3.9-JSON-REPAIR.md`.

## v0.3.8

- Restored intended browser behavior while preserving later routing, quota, cancellation, and diagnostics fixes.

See `V0.3.8-BROWSER-REGRESSION-RESTORE.md`.

## v0.3.7

- Refined true Manual-mode behavior.

See `V0.3.7-TRUE-MANUAL-MODE.md`.

## v0.3.6

- Made Manual routing strict and role-aware.
- Preserved fallback behavior in Hybrid/Automatic modes.
- Fixed token reservation and OpenRouter empty-response handling.

See `V0.3.6-MANUAL-ROUTING-AND-PROVIDER-FIXES.md`.

## v0.3.5

- Improved browser/research navigation reliability.
- Added per-run scratch workspace and stronger stop behavior.

See `V0.3.5-BROWSER-RESEARCH-RELIABILITY.md`.

## v0.3.4

- Added free-only model policy enforcement, nonblocking discovery, and stronger OpenRouter quota safety.

See `V0.3.4-FREE-MODELS-AND-QUOTA-SAFETY.md`.

## v0.3.3

- Expanded provider/model/key management and live model discovery.

See `V0.3.3-PROVIDER-MANAGEMENT.md`.

## v0.3.2

- Added rate-limit/token-aware routing and automatic failover.

See `V0.3.2-RATE-LIMITS.md`.

## v0.3.1

- Added diagnostic logs and ZIP export.
- Enforced success criteria and evidence grounding.
- Improved research/browser recovery.

See `V0.3.1-RELIABILITY-FIXES.md`.

## v0.3

- Added persistent task DAG planning, parallel direct research, browser escalation, evidence persistence, and research-depth modes.

See `V0.3-IMPLEMENTATION.md`.
