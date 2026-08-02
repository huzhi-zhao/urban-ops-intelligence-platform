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

The machine this runs on is small, unattended for months at a time, and holds the
only copy of a dataset that cannot be re-fetched. Every choice below trades
convenience for having fewer things that can break it.

### 2.1 Code on the storage node

Check the repo out to `/opt/uoip`, owned by a non-login `uoip` user, using a
**read-only SSH deploy key**:

```bash
sudo useradd --system --home-dir /opt/uoip --shell /usr/sbin/nologin uoip
sudo -u uoip git clone git@github.com:<org>/<repo>.git /opt/uoip
```

Not an HTTPS URL with a token in it: that copies a *writable* credential onto an
unattended machine, it ends up in `.git/config` in plaintext, and it cannot be
revoked without affecting everything else the token covers. A deploy key is
per-repo, read-only, and revocable on its own.

### 2.2 Dependencies — the four packages it actually imports

```bash
sudo -u uoip python3 -m venv /opt/uoip/.venv
sudo -u uoip /opt/uoip/.venv/bin/pip install -r /opt/uoip/requirements-snapshot.txt
```

Do **not** run `uv sync` here. The project's main dependency set includes pyspark
3.5.1 and shapely — hundreds of MB of compute stack that this node will never
import, and that an OS upgrade can break underneath the one job that must not
stop. `requirements-snapshot.txt` is the collection path's real import closure
(requests, boto3, pydantic, pyyaml, plus transitives), pinned to `uv.lock`.

### 2.3 A credential scoped to this job

Create a MinIO service account used by nothing else, with a policy limited to the
snapshot prefix, granting **write and multipart, never delete**:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
      "Resource": ["arn:aws:s3:::uoip/bronze/raw/SRC-WPG-SNOW/*"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucketMultipartUploads"],
      "Resource": ["arn:aws:s3:::uoip"]
    }
  ]
}
```

The multipart actions are not optional: the daily object is ~18.5 MB, above
boto3's 8 MB threshold, so it is uploaded in parts. A policy with `PutObject`
alone works on every small test and fails on the real dataset.

No `DeleteObject`, deliberately. Half a winter of history should not be one
typo — or one compromised unattended host — away from being erased. Reading the
data back (verification, Spark, `mc ls`) uses a different credential.

### 2.4 Environment file

Collection is configured entirely by environment variable. The file lives outside
the repo, is owned by root and only readable by the run user — so the process can
read its credentials but cannot rewrite them:

```bash
sudo install -d -m 750 -o root -g uoip /etc/uoip
sudo install -m 640 -o root -g uoip /dev/null /etc/uoip/snapshot.env
sudo -e /etc/uoip/snapshot.env
```

Fill in, using `.env.example` as the reference:

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

### 2.5 Run it once by hand first

```bash
sudo -u uoip /opt/uoip/.venv/bin/python -m scripts.collect_snapshot --source SRC-WPG-SNOW --dry-run
```

This walks the upstream and reports the record count without writing anything. Confirm
the count is in the expected range (~238k) before scheduling — an order-of-magnitude
mismatch means the upstream changed and the run should not proceed.

Exit codes: `0` collected · `1` usage/config error, nothing attempted · `2` at least
one dataset failed.

For the **first real run**, install the service unit from 2.6 and start it by hand:

```bash
sudo systemctl start uoip-snapshot.service
journalctl -u uoip-snapshot.service -n 50
```

Rather than exporting the variables into an interactive shell. A command that works
in your shell and fails under the scheduler is the most common way this job dies,
and it dies silently; starting the unit means the very first collection already ran
in the environment every later one will get.

### 2.6 Schedule it

A systemd timer is preferred over crontab: it survives reboots cleanly, records exit
status in the journal, retries on failure, and takes an `EnvironmentFile` — cron runs
with a nearly empty environment, which is the usual reason a working command stops
working under a scheduler.

`/etc/systemd/system/uoip-snapshot.service`:

```ini
[Unit]
Description=UOIP daily snapshot collection
After=network-online.target
Wants=network-online.target
# Bounds the retries below: at most 4 starts per 6h, so a persistently failing
# day gives up while there is still daylight to fix it by hand.
StartLimitIntervalSec=6h
StartLimitBurst=4

[Service]
Type=oneshot
User=uoip
WorkingDirectory=/opt/uoip
EnvironmentFile=/etc/uoip/snapshot.env
ExecStart=/opt/uoip/.venv/bin/python -m scripts.collect_snapshot --source SRC-WPG-SNOW
# Retry only on failure — never as an unconditional second run. Two successful
# collections at different times of day capture two different clearing states,
# and the later one would overwrite the earlier in the same ingest_date
# partition, silently changing what "the 2026-11-14 snapshot" means.
Restart=on-failure
RestartSec=30min
PrivateTmp=yes
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
```

`/etc/systemd/system/uoip-snapshot.timer`:

```ini
[Unit]
Description=Run UOIP snapshot collection daily

[Timer]
# The timezone is explicit. The partition is labelled with the machine's local
# date (date.today()), so a host on UTC would silently shift every partition
# label relative to the city the data describes.
OnCalendar=*-*-* 06:30:00 America/Winnipeg
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now uoip-snapshot.timer
systemctl list-timers uoip-snapshot.timer   # confirm NEXT is the local time you meant
```

> Timezone suffixes in `OnCalendar=` need systemd 252+. Check with
> `systemctl --version`; on older systems either set the host timezone
> (`sudo timedatectl set-timezone America/Winnipeg`) or write the UTC equivalent
> and re-check it at each DST change.

`Persistent=true` runs a missed schedule once the machine is back up. It cannot
recover a day whose window has fully passed — nothing can.

### 2.7 Register the watchdog before you walk away

Create the check at healthchecks.io (or equivalent), put its ping URL in
`SNAPSHOT_WATCHDOG_URL`, and set **period 1 day, grace 6 hours**.

The grace period is the single highest-leverage number in this deployment: it is
how long a silent failure stays undiscovered. With a 06:30 schedule, 6 hours means
an alert by 12:30 and most of the day left to re-run — the upstream still holds
its current state, so a same-day recovery loses nothing. A grace period longer
than a day converts every recoverable failure into a permanent gap.

Route the alert to something that reaches a phone. Email at 03:00 is not alerting.

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
