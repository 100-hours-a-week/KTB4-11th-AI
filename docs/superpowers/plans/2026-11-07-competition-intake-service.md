# Competition intake separation implementation plan

Goal: separate intake from portfolio generation, keeping initial SQS-driven creation and scheduled subsequent rounds.
Spec: ../specs/2026-11-07-competition-intake-service-design.md

- [x] Extract intake into services/competition: message, theme resolution, canonicalization, Jev, frozen settings persistence, queue worker, independent Settings and image. No portfolio_builder source imports.
- [x] Keep generation and participant reads in portfolio-builder; remove intake dependencies from its runtime settings and package. Keep generation hashes unchanged.
- [x] Add builder database worker tests before implementation: committed participant produces initial work; ready participant is not generated again; generation still proceeds independently of queue delivery.
- [x] Separate participant submission/persistence and briefing policies from common ones, retaining atomic ownership linkage and evidence validation.
- [x] Update Compose, CI exports, Docker images and documentation, regenerate lock and requirements.
- [x] Run complete regression tests, image imports, and independent code review; update #484.
- [ ] Implement recurring selection and rebalancing in separate #514 worktree after the Backend participant/account mapping contract is confirmed. Preserve existing common behavior behind a staged opt-in.

Ledger: contract-dependent work is pending; no account mapping is inferred.

Verification: 1787 passed, 49 skipped, 2 subtests passed; full Ruff/tach checks pass; three images built and isolated runtime imports pass; independent branch review found no actionable issues. Recurrence and #514 remain pending the explicit participant/account mapping contract.
