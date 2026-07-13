#!/bin/sh
set -eu
curl -fsS http://127.0.0.1:3100/api/health >/dev/null
curl -fsS http://127.0.0.1:3100/api/ready >/dev/null
cd /opt/medify
docker compose -p medify ps --status running | grep -q medify-api
docker compose -p medify ps --status running | grep -q medify-web
echo "MEDIFY_HEALTHY"
