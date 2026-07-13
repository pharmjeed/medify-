#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then echo "usage: restore-test.sh /opt/medify/backups/file.dump"; exit 2; fi
root="/opt/medify"
file="$1"
test -f "$file"
test -f "${file}.sha256"
sha256sum -c "${file}.sha256"
cd "$root"
set -a; . ./.env; set +a
test_db="medify_restore_test_$(date +%s)"
cleanup() { docker compose -p medify exec -T db dropdb -U "$POSTGRES_USER" --if-exists "$test_db" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker compose -p medify exec -T db createdb -U "$POSTGRES_USER" "$test_db"
docker compose -p medify exec -T db pg_restore -U "$POSTGRES_USER" -d "$test_db" --no-owner < "$file"
docker compose -p medify exec -T db psql -U "$POSTGRES_USER" -d "$test_db" -v ON_ERROR_STOP=1 -c 'SELECT COUNT(*) FROM facilities' >/dev/null
echo "RESTORE_TEST_OK"
