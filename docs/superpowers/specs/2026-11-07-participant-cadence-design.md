# Participant account mapping and scheduled generation

Backend account snapshots use participant_id: explicit null identifies common accounts and a strict positive integer identifies participant accounts. Both scheduled generation and rebalancing use this contract. Current Backend rollout is still a deployment prerequisite.

Portfolio-builder's existing weekday 06:00 pipeline remains the scheduled entry point. Initial generation follows settings intake in the same competition SQS worker. Scheduled participant generation is opt-in until the account snapshot contract is deployed; enable PORTFOLIO_BUILDER_COMPETITION_SCHEDULED_ENABLED with Backend URI and lookup JWT settings.

Fetch one authenticated HTTP snapshot for scheduled participant selection. Reject missing/invalid mappings and duplicate account/participant identities before calling the model. Select only active participant accounts, matching both participant and stored user ID. A missing participant, removed/inactive account, or ownership mismatch does not generate. Do not infer identities from names or user ID alone.

Ready participants with a portfolio linked on the current KST date need no second generation that day. Previously linked portfolios may seed a subsequent daily generation. Unready portfolios resume explanation within the existing cap. Select work with its expected previous portfolio ID; if that changes before claiming, skip stale work instead of creating another round. Initial worker must also recheck that a ready portfolio did not appear after selection.

Keep existing generation key representation and claim locks. Participant links commit atomically with portfolios. Run common and participant scheduled generation independently so one failed common run does not prevent other eligible work. Preserve common exit/error behavior when the participant mode is disabled.

The separate rebalancer PR #514 uses the same snapshot contract, skips missing/unready/foreign portfolios without fallback, and keeps leftovers scoped to the participant. It never cancels orders for an account with no selected ready portfolio. Existing account FIFO, cancellation-before-placement, one initial snapshot, and pricing/sizing remain unchanged. No new production database schema is required beyond #484's tables.

Deployment requires Backend to emit the explicit mappings before enabling either rollout flag. Both flags default to disabled.
