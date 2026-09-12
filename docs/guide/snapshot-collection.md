# Snapshot Collection

Winnipeg's clearing-status endpoint shows current state and overwrites it in
place. UOIP records a daily observation so a history can accumulate. This page
shows how to run that collector independently of the compute stack.

The example uses a Linux host with systemd, Python 3.11 and access to your own
S3-compatible bucket. For a local learning exercise without a persistent service,
start with [Getting Started](getting-started.md).

## Why collection runs independently

A replayable request window can be fetched again. Yesterday's clearing-status
observation cannot. The reference deployment therefore runs the collector on the
storage host, outside Airflow, so compute maintenance does not stop the archive.
You can use another reliable host; the important requirement is continuity.

The source is `SRC-WPG-SNOW`, dataset `snow_clearing_status`, configured in
[winnipeg_snow_clearing.yaml](../../config/sources/winnipeg_snow_clearing.yaml).
A collected day is one observation, not exact clearing-completion timestamps.
Forecast data has similar snapshot semantics but is not operated by this
clearing-status unit.

## Install the collector

On a new host, create a service user and clone the public repository. If the user
or checkout already exists, reuse it rather than repeating creation.

```bash
sudo useradd --system --home-dir /opt/uoip --shell /usr/sbin/nologin uoip
sudo install -d -m 755 -o uoip -g uoip /opt/uoip
sudo -u uoip git clone https://github.com/huzhi-zhao/urban-ops-intelligence-platform.git /opt/uoip
sudo -u uoip python3 -m venv /opt/uoip/.venv
sudo -u uoip /opt/uoip/.venv/bin/pip install -r /opt/uoip/requirements-snapshot.txt
```

The public clone needs no repository credential. The dedicated requirements file
installs the collector's dependencies without the Spark and ML stack.

## Configure storage and notifications

Use a dedicated storage credential scoped to the snapshot prefix. The collector
needs object writes and multipart upload permissions; readback can use a separate
operator credential. For a bucket named `uoip`, an example policy is:

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

Change both resource paths if you use another bucket. Omitting delete permission
does not prevent overwriting an existing object with `PutObject`; avoid a second
successful collection on the same day unless replacement is intended.

Create a protected environment file:

```bash
sudo install -d -m 750 -o root -g uoip /etc/uoip
sudo install -m 640 -o root -g uoip /dev/null /etc/uoip/snapshot.env
sudo -e /etc/uoip/snapshot.env
```

The creation command is for a new file; edit an existing one without replacing it
with an empty file. Populate the following from your own configuration:

| Variable | Purpose |
|---|---|
| `S3_ENDPOINT_URL` | Storage API endpoint reachable from this host |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | Dedicated collector credentials |
| `S3_BUCKET_NAME`, `S3_REGION` | Destination bucket and signing region |
| `SOCRATA_APP_TOKEN` | Optional upstream application token |
| `SNAPSHOT_ALERT_WEBHOOK_URL` | Notification when a running collection fails |
| `SNAPSHOT_WATCHDOG_URL` | Successful-run check-in for an independent missed-run monitor |

Keep the two notification mechanisms: a process can report its own failure,
but only an external observer can detect that it never started. Missing notification
settings warn without blocking collection.

## Verify a read-only pull

Run from the checkout so Python can resolve the collector module:

```bash
cd /opt/uoip
sudo -u uoip /opt/uoip/.venv/bin/python -m scripts.collect_snapshot \
  --source SRC-WPG-SNOW --dry-run
```

This dry run checks public-source access and reports counts without uploading.
It does not load the systemd environment file or test storage credentials.
A measured source pull was about 238,000 rows; investigate a large unexpected
change rather than treating that old number as today's exact count.

The collector uses exit 0 for success, 1 for configuration/usage failure and 2
for a dataset failure. Its small-pull guard rejects fewer than `--min-records`
(default 1,000) before upload; this catches an empty or grossly truncated response,
not every data-quality problem.

## Install the service and timer

Create `/etc/systemd/system/uoip-snapshot.service`:

```ini
[Unit]
Description=UOIP daily clearing-status observation
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=6h
StartLimitBurst=4

[Service]
Type=oneshot
User=uoip
WorkingDirectory=/opt/uoip
EnvironmentFile=/etc/uoip/snapshot.env
Environment=TZ=America/Winnipeg
ExecStart=/opt/uoip/.venv/bin/python -m scripts.collect_snapshot --source SRC-WPG-SNOW
Restart=on-failure
RestartSec=30min
PrivateTmp=yes
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
```

Create `/etc/systemd/system/uoip-snapshot.timer`:

```ini
[Unit]
Description=Collect clearing status each morning

[Timer]
OnCalendar=*-*-* 06:30:00 America/Winnipeg
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

The timer's timezone selects when to run. The service's `TZ` selects the local
date used to label the partition. Set both consistently. Confirm that your
systemd version parses the calendar before enabling it:

```bash
systemd-analyze calendar '*-*-* 06:30:00 America/Winnipeg'
sudo systemctl daemon-reload
sudo systemctl start uoip-snapshot.service
sudo journalctl -u uoip-snapshot.service -n 50
```

The manual start is the first **real upload**, using the service environment.
Verify it before scheduling. Enable the timer for the next observation and check
its next run; account for any persistent catchup so it does not unintentionally
replace today's successful pull.

```bash
sudo systemctl enable --now uoip-snapshot.timer
systemctl list-timers uoip-snapshot.timer
```

`Persistent=true` runs after downtime; it does not retrieve past observations.
A recovered run collects current state. Configure the independent watchdog for
one daily success with a grace period that leaves time to investigate the same
day, for example six hours after the expected morning run.

## Verify the stored observation

The collector writes:

```text
bronze/raw/SRC-WPG-SNOW/snow_clearing_status/ingest_date=YYYY-MM-DD/
    data.ndjson.gz
    manifest.json
```

Using a read-capable storage credential, inspect both objects for the current
Winnipeg date. Check record count, compressed size and the checksum of the
uncompressed payload. [Ingestion and Bronze](ingestion-bronze.md) explains those
fields and the required `.gz` suffix.

A later same-day pull observes a different moment and may overwrite this path.
A same-day recovery preserves an observation of that day, but cannot recover
the exact missed morning state. Never put today's response under yesterday's date.

## Troubleshooting

| Symptom | Check or action |
|---|---|
| Missing configuration | Verify the service's `EnvironmentFile` path and permissions without printing secrets |
| Storage connection error | Confirm API endpoint and host reachability; do not use the console port |
| Small-pull failure | Check upstream response and retry once the source has recovered |
| Timer active, no observation | Inspect service journal and last exit status; timer activation alone is not collection success |
| No process ran | Check the host and independent watchdog |
| A historical day is missing | Record the archive gap and restore current collection; do not backfill it |

To stop unattended collection, disable the timer with
`sudo systemctl disable --now uoip-snapshot.timer`. Stopping the compute Compose
stack does not stop this service. Keep the archive and its persistence policy
separate from disposable local experiments.
