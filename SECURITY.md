# Medify Security Policy

Medify processes sensitive health information. Do not submit real patient data to demo or development environments.

## Reporting

Report a suspected vulnerability privately to the security contact configured by the operating organization. Do not open a public issue containing patient data, credentials, tokens, logs, or exploitation details.

## Production invariants

- Human approval is required before a clinical note can be exported.
- Tenant-scoped queries must include the facility identifier and have an automated isolation test.
- Integration secrets are encrypted and never returned by the API.
- Production uses HTTPS, secure cookies, least-privilege access, encrypted backups, and Saudi data residency.
- Every security or privacy event is recorded in the chained audit log.
- External AI/STT providers remain disabled until contractual, residency, and privacy approval is recorded.
