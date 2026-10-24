# Stage load test runtime

The exact lifecycle, mount, control and Prometheus interface is in
[CONTRACT.md](CONTRACT.md). This package starts **idle**; only an authenticated
UI/CLI run starts users. The AWS lifecycle owner must install the exact Stage
manifest, bounded role/SG access, protected ephemeral files and independent
maximum expiry before bringing up Compose. It owns secret fetch, routing,
result retention and complete resource deletion. Runtime makes no AWS calls.

1. Installer prepares the protected files and `/var/lib/getddo-loadtest/reports`,
   then runs `LOCUST_BIND_IP=<private IPv4> docker compose -f loadtest/compose.yaml
   up -d --build --scale worker=2` from the extracted repository root.
2. Open the existing CloudFront HTTPS `/locust/` and log in. All mutations need
   the same exact HTTPS Origin and a session CSRF token; native UI integration
   supplies them. Five failed logins lock the single account for 60 seconds.
   Cookie lifetime is 30 minutes. Reset, user editing and nonempty profiles are
   disabled. Enter users, ramp-up and an explicit positive duration under
   Advanced options; the Stage host is fixed. The private readiness endpoint is
   `/locust/health`, and both expected workers must report before run.
3. Alternatively use a Python environment with `loadtest/requirements.txt`:

   ```sh
   python loadtest/control.py --origin https://EXISTING.cloudfront.net \
     --username-file /protected/admin-user --password-file /protected/admin-password \
     run --users 2 --spawn-rate 1 --duration 30
   ```

   The same global options precede `stop` or `collect --output <empty-private-directory>`.
   Credentials remain in protected files and memory. The client validates TLS,
   refuses alternate origins/redirects, ignores proxy environment variables and
   never prints response bodies or authentication data. Only the existing
   authenticated master is controlled; no local bypass exists.
4. Observe the Stage dashboard and private `:9646/metrics` concurrently. Check
   worker health and freshness before reading request/latency values. Counters
   are cumulative; RPS and rolling percentiles are rolling; response times are
   seconds. NaN or an absent scrape is no data. A zero-request run is not proof
   that requests succeeded. Do not expose the exporter through CloudFront/ALB.
5. Stop, wait for final worker reports, then collect. Output includes stats,
   full history, bounded failures, redacted exceptions, HTML, safe metadata and
   checksums. Keep it in a private destination. A fresh collection is required
   before another run; download it before replacing the mounted snapshot.
   Verify checksums/readback through the owning lifecycle before destroy.

The initial fixture makes only `GET /api/v1/events?page=1&size=1`, with a seeded
synthetic USER and its matching membership headers. It checks ResponseEnvelope
and PageResult shape, permits empty published results, and never stores response
bodies or actor identifiers. Existing rate limits remain active. It defines no
performance pass threshold or expected rejection policy. Author and review the
proper scenario before full performance testing: use `LockedSession` with
relative paths, stable names, a callable `validate_data`, and explicitly allowed
`(HTTP status, envelope code)` rejection pairs in `envelope_result`. Record only
the bounded result vocabulary; keep health requests out of business counters.
The default manifest selects `smoke_events`. The installed host operator can
select a trusted personal scenario using an immutable read-only snapshot.
Unsupported/missing configuration fails closed.

## Personal scenarios on the Stage host

Connect to the Stage Locust host using the documented SSH tunnel. Copy
`loadtest/examples/events.py` and `seed.sql` into your own directory under
`/opt/getddo-loadtest/scenarios/<name>/`. Edit them there. Use `loadtest db` for
an interactive verified-TLS MySQL session with the Stage test-only DML account;
`SOURCE /opt/getddo-loadtest/scenarios/<name>/seed.sql;` executes your SQL file.

A personal Python file imports `StageUser` from `loadtest.runtime`. This supplies
the exact Stage target and current seeded actor headers. Use relative API paths,
explicit request timeouts and stable request names. Use `self.check(response,
validator)` inside `catch_response=True` to record bounded business outcomes.
Native Locust HTTP metrics work for every request; business counters require this
explicit check. Actor headers can be overridden per request for deliberately
prepared test members whose data and memberships you control.

```sh
loadtest apply /opt/getddo-loadtest/scenarios/<name>/events.py
# Open /locust/: users=2, ramp up=1, run time=30s, Profile empty.
loadtest collect
loadtest db
```

`apply` refuses an active test, validates the candidate syntax/imports/tasks with
the installed image, saves previous results with S3 hash readback, selects an
immutable snapshot and recreates only master and two workers. Readiness failure
restores the previous manifest and snapshot. File edits alone do not reload
Python. `collect` stops users, waits for final worker reports, archives Locust
reports plus current Python/SQL sources and verifies S3 readback. Another web
START requires collection of the previous run; a 409/unknown-error popup can
mean uncollected results, an active apply, or unavailable workers. Keep Profile
empty and the Stage host unchanged. A web Stop never deletes AWS resources.

This is trusted team Python, not sandboxed untrusted code. Do not put secrets in
scenario/SQL files; these files are included in retained evidence. The installed
host wrapper uses its instance role and root-owned operator configuration;
credentials are fetched in memory. MySQL receives a temporary 0600 client file
on tmpfs, verified server TLS, and no password in command arguments. `db` does
not lock the shared environment: coordinate data changes with the current test
owner and avoid changing fixtures during another person's run. All team folders
and test data share the same temporary host/database and are deleted at expiry;
keep required data dumps separately before the scheduled cleanup.

Local verification (no Docker/AWS/Java):

```sh
python -m pip install -r loadtest/requirements-test.txt
python -m playwright install --with-deps chromium
python -m pytest -q loadtest/tests
```

Tests use synthetic credentials and a local API only. Chromium checks actual
HTTPS login/cookies/native UI submission; native Locust master and two workers
check idle startup, duration stop, metrics/report reconciliation and shutdown.
`LOCUST_TEST_CHROMIUM` optionally selects an already installed Chromium binary.
The suite needs loopback sockets, `openssl` and browser launch permissions.
Root ownership is simulated for configuration fixtures when tests run as an
unprivileged user; actual mode/symlink/ownership rejection remains tested.

CloudFront/ALB cookie/header forwarding, SG isolation, image build, real seeded
Stage responses, dashboard agreement and AWS expiry/deletion still require the
coordinator's live acceptance. Preserve the three persistent Locust SSM settings
on every cleanup path. Stop by itself leaves the temporary infrastructure alive.
