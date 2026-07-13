#!/bin/sh
set -eu
env_file="/opt/medify/.env"
chmod 600 "$env_file"
set_value() {
  key="$1"; value="$2"
  if grep -q "^${key}=" "$env_file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$env_file"
  fi
}
if ! grep -q '^FIELD_ENCRYPTION_KEY=' "$env_file"; then
  field_key="$(openssl rand -base64 32 | tr '+/' '-_')"
  set_value FIELD_ENCRYPTION_KEY "$field_key"
fi
set_value DEMO_MODE false
set_value ENVIRONMENT production
set_value COOKIE_SECURE false
set_value PUBLIC_REGISTRATION_ENABLED false
set_value DATA_REGION saudi-arabia
set_value SUPPORT_ACCESS_ENABLED false
chmod 600 "$env_file"
echo "MEDIFY_ENV_HARDENED"
