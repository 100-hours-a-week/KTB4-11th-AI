# Participant cadence implementation plan

Spec: ../specs/2026-11-07-participant-cadence-design.md

- [x] Add failing tests for account snapshot mapping validation and authenticated lookup.
- [x] Add failing PostgreSQL tests for active-owner scheduled selection, KST daily bound, unready resume/cap, and stale selection.
- [x] Implement scheduled participant generation and independent common/participant orchestration, preserving initial-only worker semantics.
- [x] Add opt-in settings and Compose/env documentation, with no new dependencies or schema.
- [x] Independently implement participant rebalancing in #514 worktree and review its diff.
- [ ] Run complete regression suites, image builds/imports and final review; push #484 and separate rebalancer PR.

Verification: combined builder flow 1809 passed,49 skipped,2 subtests; PostgreSQL race reproductions fixed and re-reviewed; builder/app images built and entrypoints verified. Rebalancer 183 PostgreSQL tests and 1260 repository tests pass; duplicate mappings and common/participant exit history are explicitly reviewed.
