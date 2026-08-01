# Snapshot Collection

How to deploy and operate the daily collection of **overwrite-in-place** upstreams —
datasets that publish only their current state and keep no history.

Everything else in this platform can be re-fetched for any past window. These cannot.
A day that is not collected is gone permanently, so this pipeline is built and operated
differently from the rest, and the differences are deliberate.

Current snapshot source: `SRC-WPG-SNOW` (`config/sources/winnipeg_snow_clearing.yaml`) —
address-level snow clearing status, ~238k rows, no time field of any kind.

---

## 1. Why it does not run in Airflow

Airflow runs on the **compute node**, which is stateless and rebuildable by design.
Snapshot collection runs on the **storage node**, next to the bucket it writes to.

> An ingestion task that cannot be replayed should not depend on a component that
> is designed to be thrown away and rebuilt.

See [ADR 0006 §2.2](../dev/adr/0006-storage-compute-query-stack.md). The trade-off is
that this job gets none of Airflow's retry and alerting, so it carries its own —
section 4.

---

## 2. Deploy

### 2.1 Prerequisites on the storage node

- Python 3.11+ and the repo checked out
- `uv sync --all-extras` (or the project installed into a venv)
- Network access to MinIO and to the upstream Socrata domain

### 2.2 Environment

Collection is configured entirely by environment variable. Copy `.env.example` and fill in:

| Variable | Required | Purpose |
|---|---|---|
| `S3_ENDPOINT_URL` | yes | MinIO **API** port (9000), not the console (9001) |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | yes | MinIO credentials |
| `S3_BUCKET_NAME` | yes | `uoip` |
| `S3_REGION` | no | Any value; MinIO ignores it but SigV4 needs one |
| `SOCRATA_APP_TOKEN` | no | Raises the upstream rate limit |
| `SNAPSHOT_ALERT_WEBHOOK_URL` | no | POST target for failure notices |
| `SNAPSHOT_WATCHDOG_URL` | no | Dead-man check-in, pinged on success |

The two `SNAPSHOT_*` variables are optional **on purpose**: leaving one unset logs a
warning and continues. Losing an alert is bad; losing a day of irreplaceable history
because the alerting was misconfigured is worse.

### 2.3 Run it once by hand first

```bash
python -m scripts.collect_snapshot --source SRC-WPG-SNOW --dry-run
```

This walks the upstream and reports the record count without writing anything. Confirm
the count is in the expected range (~238k) before scheduling. Then do a real run:

```bash
python -m scripts.collect_snapshot --source SRC-WPG-SNOW
```

Exit codes: `0` collected · `1` usage/config error, nothing attempted · `2` at least
one dataset failed.

### 2.4 Schedule it

A systemd timer is preferred over crontab: it survives reboots cleanly, records exit
status in the journal, and takes an `EnvironmentFile` — cron runs with a nearly empty
environment, which is the usual reason a working command stops working under a
scheduler.

`/etc/systemd/system/uoip-snapshot.service`:

```ini
[Unit]
Description=UOIP daily snapshot collection
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=uoip
WorkingDirectory=/opt/uoip
EnvironmentFile=/etc/uoip/snapshot.env
ExecStart=/opt/uoip/.venv/bin/python -m scripts.collect_snapshot --source SRC-WPG-SNOW
```

`/etc/systemd/system/uoip-snapshot.timer`:

```ini
[Unit]
Description=Run UOIP snapshot collection daily

[Timer]
OnCalendar=*-*-* 06:30:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

```bash
sudo chmod 600 /etc/uoip/snapshot.env
sudo systemctl enable --now uoip-snapshot.timer
systemctl list-timers uoip-snapshot.timer
```

`Persistent=true` runs a missed schedule once the machine is back up. It cannot
recover a day whose window has fully passed — nothing can.

---

## 3. What lands in Bronze

```
bronze/raw/SRC-WPG-SNOW/snow_clearing_status/ingest_date=YYYY-MM-DD/
├── data.ndjson.gz    gzipped NDJSON, one record per line
└── manifest.json     uncompressed
```

Partitioned by **collection date**, not record date — the records have no date. Each
day is a separate partition, so yesterday is never overwritten.

Manifest fields worth knowing when debugging:

| Field | Describes |
|---|---|
| `record_count` | Records collected |
| `file_size_bytes`, `sha256_checksum` | The **uncompressed** payload |
| `compression`, `stored_bytes` | The stored object |

The checksum covers the uncompressed payload, so two runs over identical records
produce the same checksum regardless of compression.

> ⚠️ The `.gz` extension is mandatory and `Content-Encoding` is deliberately never set.
> Spark's `s3a://` reader chooses its decompression codec from the file extension and
> ignores HTTP headers: a gzip object named `.ndjson` is read as text and produces
> garbled rows **without raising an error**.

---

## 4. Alerting: two different failures

**The run failed.** The process ran and something went wrong. It reports this itself by
POSTing to `SNAPSHOT_ALERT_WEBHOOK_URL`.

**The run never happened.** The machine was down, the timer was removed, a package
upgrade masked the unit. *No process exists to send anything*, so no amount of
in-process error handling detects it — and for a source that cannot be re-collected,
this is the failure that actually costs history.

That second case needs an external watchdog with the inverted contract: the run checks
in on success, and the watchdog alerts when a check-in **fails to arrive**. Register the
job with a service such as healthchecks.io, set its period to slightly longer than the
schedule, and put its ping URL in `SNAPSHOT_WATCHDOG_URL`. The check-in is sent only
after a genuinely successful collection — a ping on a failed run would tell the watchdog
the opposite of the truth.

### The small-pull guard

A pull smaller than `--min-records` (default 1000) is treated as a failed collection:
nothing is uploaded and the previous partition is left untouched. This exists because an
upstream having a bad day may return an empty list rather than an error, and writing
that would leave a partition that looks collected but is not — indistinguishable from a
real day at read time.

Raise the floor with `--min-records` once a stable baseline is known. Keep it well below
the true row count: it is a smoke alarm for "the API returned nothing", not a
data-quality threshold. Row-count baselines belong in the quality framework.

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Missing required object-storage environment variable(s)` | Timer's `EnvironmentFile` not loaded or incomplete | Check `systemctl show uoip-snapshot.service -p Environment`; cron/systemd do not read your shell profile |
| `Could not connect to the endpoint URL` | `S3_ENDPOINT_URL` points at the console port | Use 9000, not 9001 |
| `NoSuchBucket` / signature errors | Bucket missing, or a client not using path-style addressing | `mc ls` the bucket; all project code goes through `build_s3_client()`, which sets path style — MinIO has no virtual-host DNS |
| `SnapshotTooSmallError`, nothing written | Upstream returned little or nothing | Check the dataset in a browser; re-run the same day once the upstream recovers |
| Timer shows as active, no new partitions | Service failing on every run | `journalctl -u uoip-snapshot.service -n 100` |
| Garbled rows when Spark reads the data | An object was written without the `.gz` extension | See the warning in section 3 |
| A day is simply missing | The run did not happen | It cannot be recovered. Confirm the watchdog is registered so the next one is caught |

Verify the last few days landed:

```bash
mc ls --recursive local/uoip/bronze/raw/SRC-WPG-SNOW/snow_clearing_status/ | tail -20
```

---

## 6. Adding another snapshot source

1. Add `config/sources/<name>.yaml` with `partition_strategy: snapshot`. It is the only
   strategy that permits `timestamp_field: null`.
2. Add a `scripts/backfill/backfill_<slug>.py` for manual runs (auto-discovered — no
   registry edit needed).
3. Point a second timer unit at it with `--source <SRC-ID>`.

No loader or collector change is required; both are strategy-driven.

---

## See also

- [Overview](overview.md) — BO-7 and why this archive is a contribution in its own right
- [ADR 0006](../dev/adr/0006-storage-compute-query-stack.md) — why MinIO, why gzip, why
  this job is outside Airflow
- [Ingestion & Bronze](ingestion-bronze.md) — the other three partition strategies
- [Data Sources](data-sources.md) — what `g3p4-h83y` contains and what it is missing
