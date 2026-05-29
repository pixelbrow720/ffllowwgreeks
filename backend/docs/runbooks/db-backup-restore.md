# Database backup, restore, and disaster recovery

> Living document. If you change the backup mechanism, update this file
> in the same commit.

FlowGreeks runs **TimescaleDB** (Postgres extension) on a single host.
The `dealer_state_1s` hypertable is the only data we cannot regenerate
from upstream sources — losing it means losing the historical record
that backtests and calibration depend on. Everything else (api_keys,
schema_version, alert rules) is small and re-creatable but expensive
to recover from operator memory.

This runbook covers the four operations every operator must know:

1. **Verifying the backup job is running.**
2. **Restoring to a fresh database from a snapshot.**
3. **Restoring a single table without losing the rest.**
4. **Drilling the restore on a non-prod box quarterly.**

A backup that has not been restored is a wishful directory listing.
**You are required to drill the restore at least once per quarter.**

---

## 1. Backup mechanism

`deploy/backup/docker-compose.backup.yml` runs a sidecar container that
calls `pg_dump --format=custom --compress=9` against the live database
once per day at 02:30 UTC. The dump lands in the `pg-backup-data` Docker
volume under `/backups/daily/{YYYYMMDD}.dump`. First-of-month dumps are
copied to `/backups/monthly/` and kept for a year.

Bring it up alongside the main stack:

```sh
docker compose \
  -f deploy/docker-compose.yml \
  -f deploy/backup/docker-compose.backup.yml \
  --profile app --profile backup up -d
```

**Required offsite copy.** Local-disk backup does not survive the host
dying. Mirror the volume off-box every night:

```sh
# Adjust to your storage. Backblaze B2 or AWS S3 are both fine.
rclone sync /var/lib/docker/volumes/flowgreeks_pg-backup-data/_data \
            b2:flowgreeks-backups/$(hostname) \
            --transfers 4 --checksum
```

Schedule via the host's crontab at 04:00 UTC (after the 02:30 dump
plus a 30-min retention sweep).

---

## 2. Daily verification (5 minutes)

Run on the host that owns the backup volume:

```sh
# 1. Most recent dump < 26h old?
LAST=$(docker exec fg-pg-backup ls -1t /backups/daily/*.dump | head -1)
docker exec fg-pg-backup stat -c %y "$LAST"

# 2. Non-zero size?
docker exec fg-pg-backup du -h "$LAST"

# 3. Smoke-restore the catalog only — fast (~5s) and proves the dump
#    isn't corrupt. Does NOT touch the live DB.
docker exec fg-pg-backup pg_restore --list "$LAST" | head -20
```

If any check fails: alert immediately and investigate before the next
scheduled run.

---

## 3. Full restore from snapshot

**Use case:** the live database volume is dead, corrupted, or wrong.
This is a destructive procedure on the target — do not run against the
live database without thinking.

Pre-requisites:

- The backup `.dump` file you intend to restore from (from the
  `pg-backup-data` volume, or pulled from offsite).
- A fresh, empty Postgres host with TimescaleDB extension installed.
- The DB credentials matching the dump's role (`flowgreeks` user).

Steps:

```sh
# 1. Stop the live api / compute / ingest binaries so no writes hit
#    the target database during restore.
docker compose --profile app stop api compute ingest replay

# 2. Drop and recreate the empty database.
docker exec -it fg-postgres psql -U flowgreeks -d postgres -c \
  "DROP DATABASE IF EXISTS flowgreeks;"
docker exec -it fg-postgres psql -U flowgreeks -d postgres -c \
  "CREATE DATABASE flowgreeks;"

# 3. Pre-install Timescale extension (must exist BEFORE pg_restore loads
#    hypertables, or pg_restore will fail to register them).
docker exec -it fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "CREATE EXTENSION IF NOT EXISTS timescaledb;"

# 4. Restore the dump.
docker exec -i fg-postgres pg_restore \
  --username=flowgreeks \
  --dbname=flowgreeks \
  --jobs=4 \
  --no-owner \
  --no-privileges \
  --verbose \
  < /path/to/20260528.dump

# 5. Re-run any migrations that landed AFTER the dump was taken.
#    The migrate sidecar is idempotent (uses schema_version) so this
#    is safe to invoke unconditionally.
docker compose --profile app up -d migrate

# 6. Bring app services back up.
docker compose --profile app start api compute ingest replay

# 7. Verify.
curl -s http://localhost:8080/health/ready | jq .
docker exec -it fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT count(*) FROM dealer_state_1s WHERE ts_ns > extract(epoch from now() - interval '1 day') * 1e9;"
```

Expected RTO on a 50 GB dump with 4-way parallel restore on commodity
hardware: ~20 minutes.

---

## 4. Single-table restore

When only one hypertable (e.g. `dealer_state_1s`) is corrupted but the
rest of the database is healthy:

```sh
# 1. Extract the table from the dump into a SQL file.
pg_restore \
  --table=dealer_state_1s \
  --data-only \
  --file=dealer_state_1s.sql \
  /path/to/dump.dump

# 2. On the live database, rename the bad table aside (so you can
#    diff later if needed) and create a fresh empty hypertable.
docker exec -it fg-postgres psql -U flowgreeks -d flowgreeks -c "
  ALTER TABLE dealer_state_1s RENAME TO dealer_state_1s_BROKEN;
  -- Re-apply the schema fragment that defines dealer_state_1s.
  -- See scripts/migrations/000004_dealer_state_1s.up.sql
"

# 3. Replay the migration that recreates the empty table.
docker compose --profile app up -d migrate

# 4. Load the data.
docker exec -i fg-postgres psql -U flowgreeks -d flowgreeks < dealer_state_1s.sql

# 5. Once you've verified the restore worked, drop the broken table.
docker exec -it fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "DROP TABLE dealer_state_1s_BROKEN;"
```

---

## 5. Quarterly restore drill (mandatory)

A backup that has never been restored is a backup that does not work.

Add a calendar reminder for the first Monday of each quarter. The drill:

1. Pull the latest dump from offsite into a non-prod box.
2. Run section 3 against it. Time the restore.
3. Run a sanity query: `SELECT count(*), min(ts_ns), max(ts_ns) FROM dealer_state_1s;`.
4. Confirm `count(*)` matches what the dump's manifest claimed.
5. Update `docs/runbooks/_drill-log.md` with date, dump file, restore
   duration, sanity-query result, and any gotchas.

If the drill fails at any step, the backup mechanism is broken. Stop
all other work until it is fixed.

---

## 6. Failure modes seen before

| Symptom | Cause | Fix |
|---|---|---|
| `pg_restore: extension "timescaledb" must be installed` | Timescale not on the target | `CREATE EXTENSION timescaledb;` BEFORE the restore |
| Restore hangs at "creating CONSTRAINT" | Too many parallel jobs vs CPUs | Reduce `--jobs` to N-1 |
| `dealer_state_1s` empty after restore | Hypertable chunks not auto-created | Migration must run AFTER restore so policies attach to the rehydrated chunks |
| Free space drops to 0% during restore | Docker volume on root partition | Mount backup volume on a dedicated partition |

---

## 7. Out of scope (and what to do instead)

- **Continuous archiving / PITR** — not configured. We can recover to
  the previous 02:30 UTC, not a specific second. To upgrade: configure
  `archive_mode=on` + `archive_command` shipping WAL to S3, then point
  pg_basebackup + recovery target at it. Plan ~1 day of work.
- **Logical replication standby** — not configured. For a hot standby
  add a second Postgres instance and set up logical replication on the
  hypertable. Useful pre-public-launch.
- **Multi-region** — not in scope. The product is single-region by design.
