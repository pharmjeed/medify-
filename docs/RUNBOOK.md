# Medify Production Runbook

## Health

Run `sudo /opt/medify/deploy/healthcheck.sh`. The API `/health` reports process health and `/ready` verifies PostgreSQL and Redis.

## Backup and restore

Run `sudo /opt/medify/deploy/backup.sh` daily from cron. Copy the encrypted backup to a second Saudi-hosted location. Test the latest backup monthly with `sudo /opt/medify/deploy/restore-test.sh <dump>` and retain the output as evidence.

## Deployment

1. Back up the database.
2. Pull the reviewed Git commit.
3. Run `docker compose -p medify build`.
4. Run `docker compose -p medify up -d`.
5. Alembic migrations execute before the API starts.
6. Run the health check and smoke test.
7. Verify the separate `vm` Compose project is still running.

## Incident handling

Isolate the affected service without deleting evidence, preserve logs and audit hashes, rotate affected secrets, identify impacted facilities and data subjects, and start the PDPL breach assessment. Regulatory notification decisions and communications must be owned by the appointed privacy/security officers.

## Recovery objectives

Until managed high availability is provisioned, the operational target is RPO 24 hours and RTO 4 hours. These are not contractual SLAs and must be revised after managed database and multi-node deployment.
