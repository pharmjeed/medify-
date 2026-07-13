#!/bin/sh
set -eu
umask 077
db_password="$(openssl rand -hex 24)"
secret_key="$(openssl rand -hex 32)"
{
  echo "POSTGRES_DB=medify"
  echo "POSTGRES_USER=medify"
  echo "POSTGRES_PASSWORD=${db_password}"
  echo "SECRET_KEY=${secret_key}"
  echo "ACCESS_TOKEN_MINUTES=30"
  echo "REFRESH_TOKEN_DAYS=7"
  echo "DEMO_MODE=true"
  echo "NEXT_PUBLIC_APP_NAME=Medify"
} > /opt/medify/.env
chmod 600 /opt/medify/.env
