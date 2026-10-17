# ktb-aws Package Design

## Intent and scope

Issue #279 separates the AWS-specific Python code already used by the AI services into `packages/aws` (`ktb-aws`, imported as `ktb_aws`). Success means all three Market services use one ElastiCache IAM credential provider, portfolio-rebalancer uses the relocated SQS transport, `ktb-core` has no AWS-specific queue code, and existing IAM, Valkey, and SQS behavior remains unchanged. The package contains these two existing implementations only. CloudWatch metric shell scripts and log configuration, ECR image publishing, and ECS/EC2 deployment remain outside this Python package change.

## Package boundaries

`ktb_aws.elasticache` owns the current `ElastiCacheIAMProvider` implementation, including SigV4 presigning and synchronous and asynchronous credential methods. Its constructor and token behavior stay the same. `market-http.valkey`, `market-streamer.valkey`, and `market-collector.valkey` retain their respective settings checks and Redis client construction, importing the shared provider.

`ktb_aws.sqs` owns the current `Message` value type, `Queue` protocol, and injected-client `SqsQueue` implementation from `ktb_core.queue`. Their method signatures, FIFO send fields, receive defaults and mapping, and delete behavior stay the same. Portfolio-rebalancer imports `Queue` and `SqsQueue` from `ktb_aws.sqs`; it still constructs its boto3 client. Remove `ktb_core.queue` after updating all callers. `ktb-aws` does not depend on `ktb-core`.

Report-builder also calls SQS directly, but its polling and acknowledgement flow uses a different message shape and visibility timeout. It stays as-is; moving it into `SqsQueue` would expand the behavior and scope of this issue.

For a future AWS service, keep a simple SDK call in its owning service. Add a focused `ktb_aws` module when shared Python behavior has a real consumer, and declare only the dependencies that implementation needs. Revisit dependency packaging when a concrete new use makes the shared package costly for unrelated consumers. This design does not add modules or abstractions for hypothetical services.

## Workspace and distribution

Add `packages/aws/pyproject.toml` using the existing `uv_build` package pattern. Declare `botocore` and `redis` there for the credential provider. Keep each Market service's direct `redis` dependency because it creates Redis clients. Remove their direct `botocore` dependency if no other import needs it. Declare `ktb-aws` as a workspace dependency in all three Market services and portfolio-rebalancer; portfolio-rebalancer retains `boto3` for client creation. Add `ktb_aws` and the four consumer relationships to `tach.toml`.

Add `packages/aws` to the four service Dockerfiles and the production app Dockerfile installation list. Run `uv lock`, then regenerate the hash-pinned requirements for market-http, market-streamer, market-collector, portfolio-rebalancer, and app using the exact export paths and commands in `AGENTS.md`. The root uv workspace already includes `packages/*`.

## Verification

Move the existing SQS transport tests from core to the new package and the provider token test from market-streamer to the new package. Keep service Valkey tests focused on each service's connection settings and client construction; verify all three import the same provider class. Run lint and formatting checks, relevant unit tests, `tach` boundary checks, and image builds for the affected services and production image. The change must not require live AWS credentials or alter runtime environment variables.
