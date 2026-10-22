# GD-309 / GD-310 runtime handoff

Validated locally on 2026-10-23 KST, Python 3.14.6, official Locust 2.47.0,
Playwright 1.64.1 and existing Chromium 1243. No AWS calls, platform secret reads,
pushes, PRs, merges, Java/Gradle or Docker daemon were used.

Result: **23 passed in 11.00 seconds**, with seven upstream deprecation warnings
from gevent's subprocess fork and TLS fixture handling. Dependency compatibility:
`uv pip check` checked 40 packages successfully. Final command:

```sh
LOCUST_TEST_CHROMIUM=<installed-Chromium-path> python -m pytest -q loadtest/tests
```

Evidence exercised by actual application/native library tests:

- Real local HTTPS + Chromium: login redirect, native assets under `/locust/`,
  secure HttpOnly SameSite Strict session cookie, immutable UI host input,
  native React form AJAX carrying Origin/CSRF and accepted empty profile.
- Actual Flask routes: unauthenticated start/stop/reset/collect refusal, missing
  or foreign CSRF/Origin rejection, five-failure login lockout, unknown Unicode
  username handling, arbitrary host/profile/user editing rejection, concurrent
  start refusal and private-only metrics URL.
- Automation client against the same Flask master: login with CSRF rotation,
  run/stop/collect, all six report downloads and private file checksums.
- Real native Locust master + two workers over loopback RPC, and read-only local
  HTTP fixture: no startup requests, explicit two-user run, duration stop,
  post-stop worker reports, exact request/failure/429/business reconciliation,
  CSV agreement, full-history + HTML collection, worker loss/NoData and verified
  server/user/runner shutdown.
- Native imbalanced global histograms: p95/p99 use the merged distribution,
  not worker percentile averages, with millisecond-to-second conversion.
  Missing/stale/zero-request/transport-only latency yields NaN.
- Duplicate/invalid custom outcome reports do not recount.
  Bounded business classification distinguishes success, explicit expected
  rejection, HTTP429, server/HTTP/envelope/transport failures.
- Protected manifest/secret/actor modes, ownership rejection, no symlinks,
  missing membership rejection, exact internal ALB configuration and HTTPS-only
  automation origin. Root ownership alone is simulated for user-owned fixtures.

The integration test found and fixed a duration callback cancelling its own stop
greenlet before final stats. Collection now requires a completed stop and a new
report from every expected worker after its checkpoint, then appends/flushes the
final full-history row and snapshots into `reports/collected`. This also avoids
truncating files held open by native background CSV writers. Untouched idle
master collection is supported.

The backend source contract was checked in EventController, CurrentUser,
CurrentUserArgumentResolver, ResponseEnvelope and PageResult: the seeded USER
needs matching ID, role **and membership** headers. Actor JSON therefore has
required `membership` (`excellent`, `vip`, `vvip`), chosen from the actual seed.
No performance acceptance threshold or rejection policy was invented.

Stable interfaces: [CONTRACT.md](CONTRACT.md), operator steps:
[README.md](README.md). Metrics names/types/labels remain exactly as announced.
UI 8089, exporter 9646 and bridge-only RPC 5557 retain separate access scopes;
root-protected host manifest is the target authority. Automation remains
`python loadtest/control.py --origin ORIGIN --username-file FILE --password-file
FILE run|stop|collect`, with explicit run parameters and collect output directory.

CI: install `loadtest/requirements-test.txt`, run
`python -m playwright install --with-deps chromium`, then
`python -m pytest -q loadtest/tests` as a separate process from scripts unittest
discovery. The tests require loopback sockets, browser execution and openssl.

Remaining coordinator acceptance: Docker image build/Compose validation
(Docker is unavailable in this WSL distro), actual root-owned installer mounts,
matching seeded membership, CloudFront/ALB cookie/header forwarding and auth,
SG isolation/private scrape, actual Stage smoke/dashboard/CSV reconciliation,
result retention/readback and independent AWS expiry/destroy/residual checks.
Live deployment, PR reviews and teammate approval are coordinator-owned.

Official APIs reviewed: [configuration](https://docs.locust.io/en/2.47.0/configuration.html),
[authentication/extensions](https://docs.locust.io/en/2.47.0/extending-locust.html),
[distributed execution](https://docs.locust.io/en/2.47.0/running-distributed.html).
Pinned installed native web, runner and stats implementations were inspected
and exercised for login routes, request/worker hooks, histogram aggregation,
CSV writers and runtime timers.
