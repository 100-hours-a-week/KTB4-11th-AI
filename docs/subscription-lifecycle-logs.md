# Market subscription lifecycle logs

`market-http` emits these lifecycle events for its shared Redis Pub/Sub listener:

| Event | Meaning |
| --- | --- |
| `market_subscription_starting` | Application is about to subscribe. |
| `market_subscription_ready` | Redis acknowledged both `market:prices` and `market:orderbooks`. |
| `market_subscription_resubscribing` | First observed channel ACK after initial readiness. |
| `market_subscription_recovered` | Both channel ACKs were observed again. |
| `market_subscription_start_failed` | Initial subscribe or ACK wait raised an exception. |
| `market subscription listener failed` | Listener terminated with an exception. |
| `market_subscription_ended` | Listener iterator ended unexpectedly without an exception. |
| `market_subscription_stopped` | Owner is shutting down; `reason` distinguishes startup cancellation. |

These records include `pid`, a fresh `listener_id` per run, and `channels`.
`listener_id` identifies the application's subscription lifetime, not a physical
Redis socket. Redis-py may reconnect the socket within that lifetime. Recovery
is inferred from re-subscription ACKs; these logs cannot identify the exact
server disconnect time or internal retry attempts. No library hooks are patched.
A partial ACK cycle cannot establish recovery, and late ACKs during listener
shutdown do not emit readiness or recovery records.

`market-streamer` emits one `kiwoom_registering` event per attempt and
`kiwoom_registered` after a successful REG acknowledgement. It includes
`requested_count` (after the 200-symbol limit), `feed_types`, `attempt`, `pid`,
and `listener_id`. Duplicate successful REG acknowledgements within the same
attempt do not emit duplicate readiness records.

`kiwoom_stream_closed` identifies an unexpected iterator end;
`kiwoom_stream_failed` identifies an exception and includes `error_type`.
Both include `phase` (`startup` or `running`). `kiwoom_stream_retrying` records
the existing retry delay. `kiwoom_stream_stopped` records cancellation, including
cancellation during cleanup or backoff. Scheduled shutdown still emits
`market_streamer_stopped` with `stop_at` from the run owner.

No per-tick or per-SSE-client lifecycle logs are added. IAM tokens and credentials
are not logged. In ECS, use the CloudWatch log stream to identify the ECS task;
these changes do not make metadata HTTP requests or add task-ID configuration.
IAM session renewal and Redis retry behavior are unchanged.
