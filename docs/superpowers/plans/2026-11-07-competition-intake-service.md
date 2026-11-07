# Competition flow plan

Supersedes the standalone-service extraction. User requested one portfolio-builder/competition module.

- [x] Keep intake, canonicalization, storage and initial generation in one SQS worker.
- [x] Remove separate competition service, Dockerfile, requirements and DB polling deployment.
- [x] Keep common submission/briefing policies separate from participant policies.
- [x] Verify folded worker, existing generation regressions, scheduled cadence and images.

The participant cadence and rebalancer plan remains active.
