#!/bin/sh
set -eu
root="/opt/medify"
backup_dir="${root}/backups"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
cd "$root"
set -a; . ./.env; set +a
file="${backup_dir}/medify-${stamp}.dump"
docker compose -p medify exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner > "$file"
sha256sum "$file" > "${file}.sha256"
chmod 600 "$file" "${file}.sha256"
find "$backup_dir" -type f -name 'medify-*.dump*' -mtime +30 -delete
echo "$file"
