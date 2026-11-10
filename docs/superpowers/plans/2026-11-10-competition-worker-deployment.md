# Competition worker deployment implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Keep the competition worker's environment, image and rollback state aligned with each release.

**Architecture:** Both CD workflows source the same deployment helper. It snapshots the existing worker, validates the new image's settings before stopping it, replaces it, and restores the prior running/stopped/absent state after release files are rolled back. Compose reads the generated root `.env` and uses a 150-second disclosure retrieval timeout.

**Tech Stack:** Bash, Docker Compose, GitHub Actions, pytest.

## Constraints

- No committed secrets or changes to application dependencies.
- Preserve unrelated containers and existing deployment recovery behavior.
- Keep the original deployment failure status when worker recovery also fails.
- No production deployment or main merge as part of this PR.

## Review focus

- First deployment and rollback to a release without the service.
- Previously stopped or absent workers.
- Invalid settings and failed migrations before worker replacement.
- A partially started new worker and failed recovery.
- The source `.env` and shared helper are wired into both workflows.

## Tasks

- [x] Add executable deployment tests for both workflows, watch missing lifecycle behavior fail.
- [x] Implement the shared helper, workflow wiring, environment source and timeout defaults.
- [x] Document configuration ownership and deployment prerequisites without secret values.
- [x] Run relevant tests, shell/Compose/workflow checks and obtain a fresh code review.
- [ ] Commit, push the issue branch and open a PR targeting dev that closes #546.
