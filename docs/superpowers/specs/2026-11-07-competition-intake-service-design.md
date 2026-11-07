# Competition intake and generation within portfolio-builder

The user superseded the separate-service design. SQS intake, settings persistence and initial portfolio generation run together in portfolio_builder.competition through the competition-portfolio-builder entrypoint. There is no separate competition service or database polling service.

The worker uses ordinary builder model/news/market settings and separate PORTFOLIO_BUILDER_COMPETITION_* queue/Jev settings. It acknowledges committed settings before generation; the next poll scans pending settings for recovery if it dies after acknowledgement. Ready portfolios are not generated again by this worker.

The existing 06:00 portfolio-builder entrypoint independently runs common generation and opted-in participant generation. Participant lookup uses the approved explicit nullable participant_id Backend contract. Common submission and briefing composition remain independent of participant policy. Participant portfolio/link persistence is atomic. Retry budgets, generation keys and claim guards are preserved.

The standalone service/image, its requirements, deployment and import-isolation tests were removed. Intake tests now live under portfolio-builder/tests/intake. See the participant cadence design for mapping, daily selection and rollout controls.
