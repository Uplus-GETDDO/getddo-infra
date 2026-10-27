# 스테이징 Locust 실행 계약

Official Locust **2.47.0** (`locustio/locust:2.47.0`), Flask-Login native
`--web-login`, `--web-base-path /locust`. No automatic load at startup.
Runtime has no AWS SDK/calls. Lifecycle owner provisions files, expiry and SGs.

## Host interface

Run from extracted repository root:

```
LOCUST_BIND_IP=<EC2-private-IP> docker compose -f loadtest/compose.yaml up -d --scale worker=2
```

`LOCUST_BIND_IP` is mandatory, never 0.0.0.0/public. Host ports **8089** (existing
ALB SG only), **9646** (existing monitoring SG only). Worker RPC **5557** has no
host publish and stays on a dedicated Compose bridge. Containers also need
outbound Stage ALB:80. Do not put /metrics on CloudFront/ALB; UI returns 404 there.
Health check: private `GET /locust/health` returns only ready/alive state.

Read-only mounts (host → container):

- `/etc/getddo-loadtest/manifest.json` → `/etc/getddo-loadtest/manifest.json`
- `/run/getddo-staging/locust` → `/run/secrets` (master only)
- `/run/getddo-staging/locust-scenario` → `/run/scenario` (all processes)
- `/opt/getddo-loadtest/active` → `/run/personal` (all processes, immutable root-owned snapshot)
- `/var/lib/getddo-loadtest/reports` → `/reports` (master writable)

Root-owned manifest, not group/world writable:

```json
{
  "environment": "staging",
  "target": "http://internal-EXACT-STAGE-ALB.ap-northeast-2.elb.amazonaws.com",
  "public_origin": "https://EXISTING.cloudfront.net",
  "expected_workers": 2,
  "scenario": "smoke_events",
  "actor_file": "/run/scenario/actor.json"
}
```

A personal manifest replaces `scenario` with `personal` and adds exactly
`"scenario_file": "/run/personal/scenario.py"`. `loadtest.runtime.StageUser` is
the common base. Only concrete subclasses with tasks are loaded. Scenario import
executes trusted team Python; target locking is a convenience constraint, not a
security sandbox. The host CLI owns syntax/import validation, immutable releases,
archival/readback and rollback.

The lifecycle owner writes the exact Stage Terraform output into this trusted
manifest; runtime validates internal ALB syntax and locks every request to that
exact origin. There is no user target option. Manifest/actor files never enter
Git/reports. Actor JSON is a seeded synthetic Stage user only:
`{"user_id":"<seeded UUID>","role":"USER","membership":"vip"}` (root 0600).
Membership must match the seeded database user: `excellent`, `vip` or `vvip`.
The published events controller requires `X-User-ID`, `X-User-Role` and
`X-User-Membership`; runtime supplies all three from this protected file.
Master secret files root 0600, no symlinks: `admin-user`,
`admin-password-hash` (Werkzeug scrypt hash), `session-secret` (at least 32 chars).
Installer reads existing SSM password and hashes in memory using
`werkzeug.security.generate_password_hash`; never put originals in arguments,
environment, Docker inspect, logs, Terraform or reports. Preserve all persistent
SSM settings on destroy. Root runs inside containers to read these bounded files;
capabilities dropped, read-only filesystem, no Docker socket.

## Control

`python loadtest/control.py --origin https://EXISTING.cloudfront.net --username-file
<protected-file> --password-file <protected-file> run --users 2 --spawn-rate 1
--duration 30` authenticates and controls the same master as the UI. `stop` stops
users; `collect --output <directory>` downloads CSV/full history/failures/exceptions,
HTML and safe metadata from the stopped master. CLI stdout contains state only.
Passwords are read only from protected files, never flags. CLI uses HTTPS only,
secure session cookies, a session CSRF header and exact Origin. No alternate
localhost authentication bypass. Installer can use the same public HTTPS origin.

Native UI start/stop/reset use the same guarded master runner. Start requires an
explicit positive duration (`run_time` in seconds or Locust duration format),
user count and spawn rate; connected fresh workers and idle state. Concurrent
start returns 409. Repeated runs require collect first (409 otherwise). Export
artifacts have fixed names inside mounted reports; collect before next run or
destroy. Separate AWS expiry remains mandatory and owns deletion.

Authenticated `POST /locust/control/maintenance` with `{"enabled":true}`
atomically refuses running/pending/stopping states and blocks new web START.
Collection remains available. Apply enables maintenance before collection and
switching; a new master starts without maintenance. A failed validation restores
maintenance=false on the existing master. Local `flock` serializes apply/collect.

`GET /locust/control/session` returns authenticated `csrf`, `state` and
`collectible`. Collection waits for completed stop and fresh reports from every
expected connected worker after the stop checkpoint, including final requests.
An untouched idle master can collect zero-request evidence. Mounted live files
are `/reports/run_*`; safe fixed report snapshots are `/reports/collected/*`.
The client writes six private files plus `SHA256SUMS` into an empty private output
directory and exits nonzero on any failed download; stdout contains state only.
Snapshot exception output preserves counts and redacts exception text/tracebacks.

## Prometheus schema (private :9646/metrics)

All metrics have no labels unless specified. Statistics are master global,
merged native histograms, never worker percentile averages. Time unit seconds.
Counters reset when process/test resets; use Prometheus rate with reset handling.

| Name | Type | Definition |
| --- | --- | --- |
| `locust_users` | gauge | master runner user_count |
| `locust_requests_total` | counter | native global cumulative requests |
| `locust_failures_total` | counter | native global cumulative failed requests |
| `locust_rps` | gauge | native rolling current_rps; NaN if no recent data |
| `locust_response_time_seconds{quantile="0.5\|0.95\|0.99",scope="cumulative\|rolling"}` | gauge | native global histogram percentile / 1000; NaN if missing/stale/zero requests |
| `locust_data_fresh` | gauge | recent request within 10 seconds and fresh expected workers |
| `locust_workers_expected` | gauge | manifest count |
| `locust_workers_connected` | gauge | native nonmissing worker count |
| `locust_workers_fresh` | gauge | worker reports received within 10 seconds, no ID label |
| `locust_workers_healthy` | gauge | fresh count >= expected and connected count >= expected |
| `locust_business_total{scenario="smoke_events\|personal",method="GET\|MIXED",result="success\|expected_rejection\|rate_limited\|server_error\|http_error\|invalid_envelope\|transport_error"}` | counter | deduplicated cumulative worker business reports |

`rate_limited` is HTTP429. `server_error` is HTTP5xx; expected business rejection
is explicitly configured `(status, envelope code)` and never server_error.
Smoke has no expected rejection pairs. Success requires HTTP200, boolean true,
code SUCCESS, string message and data.items list (page shape checked); HTTP200
alone is insufficient. Health is excluded. Missing samples are NaN, not zero,
including missing latency histograms on transport-only failures. Native rolling
percentiles use an approximate 10-second cache window and can initially be NaN;
cumulative percentiles cover the current test. Full-history CSV spans process
lifetime; filter by metadata run start/stop times when repeating a collected run.
Prometheus `up` must also be graphed; absent exporter is no data. Full exact
schema is stable for lifecycle/dashboard integration; changes will be announced.

Official references: https://docs.locust.io/en/stable/extending-locust.html,
https://docs.locust.io/en/stable/configuration.html,
https://docs.locust.io/en/stable/running-distributed.html.
