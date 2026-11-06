# Scheduled batch memory budget

Production Compose places disclosure-indexer, news-preprocessor, news-clusterer,
news-graph-builder, portfolio-builder, portfolio-rebalancer, market-syncer, and
market-collector containers under `ktb-batch.slice`. They share the budget without
individual container memory limits. PostgreSQL, QuestDB, HTTP services, the persistent
report-builder worker, and market-streamer remain outside this slice and retain their
existing limits.

The slice starts reclaiming memory at 50% of physical RAM (`MemoryHigh`) and enforces
a combined hard limit at 60% (`MemoryMax`). Swap is disabled for the slice. On the
current production host this is approximately 7.7 GiB / 9.3 GiB, leaving approximately
6.2 GiB outside the batch budget. This is headroom, not a DB memory reservation, and
does not protect against unrelated unbounded host processes. Concurrent jobs compete
within the shared budget; a slice OOM can kill a batch other than the one growing.

Both EC2 deployment workflows install and start the slice before launching the new
Compose configuration. Docker must use the systemd cgroup driver with cgroup v2.
For manual installation, install `infrastructure/systemd/ktb-batch.slice` into
`/etc/systemd/system/`, run `systemctl daemon-reload`, then
`systemctl enable --now ktb-batch.slice` before starting batch containers.
Existing containers retain their old parent and limits until recreated; running jobs
should finish normally before the next scheduled launch uses the new configuration.

Check the shared budget with `systemctl show ktb-batch.slice -p MemoryCurrent
-p MemoryPeak -p MemoryHigh -p MemoryMax`. Read `memory.events` under the slice's
`ControlGroup` in `/sys/fs/cgroup` for high-limit and OOM counters. Check container
placement with `docker inspect --format '{{.HostConfig.CgroupParent}}' CONTAINER`.
Docker's per-container display does not include the inherited shared hard limit.

Retain the clusterer's `clustering cost:` / `peak_rss_mib` logs to track growth.
Review the shared budget when adding services or changing the host size; percentage
limits scale with RAM but the required non-batch headroom must still be checked.
