# Temporary staging lifecycle

`terraform/staging` owns the disposable workload in `staging/terraform.tfstate`.
`terraform/staging-control` owns persistent CodeBuild projects, the failure-event
route and Scheduler group in `staging-control/terraform.tfstate`. Bootstrap owns
their service roles. Only the coordinator applies bootstrap/control with the
reviewed administrator credentials; runners never receive those credentials.
Existing VPC/subnets, shared ALB/listener, persistent monitor and CloudFront are
read-only dependencies. The workload adds its own priority-30 `/locust` rule,
Locust target group and one TCP 8089 egress rule on the existing ALB SG.
It also owns exactly four monitor-SG egress rules: API 9091/9100 and Locust
9100/9646, with matching Stage-SG ingress from the monitor. Runtime metric
permissions are restricted to Seoul and GETDDO/API (API role) / CWAgent namespaces.
The API target health check uses `/v3/api-docs` on traffic port 8080, matching the
existing application startup check; management/actuator remains on 9091.

The control design uses on-demand Linux CodeBuild outside the VPC and SSM for
private host operations. No NAT, GitHub token, source connection, new domain or
public listener is required. Before deployment the coordinator must verify Seoul
CodeBuild Linux small concurrency of at least two (up and cleanup can overlap),
Scheduler support/quota, EC2/RDS/Redis capacity and exact engine/AMI availability.
Mock tests do not establish those account-level facts.

## Submission and recovery

Requires Linux, Python 3.12+ and boto3 1.40.59. Use the existing `getddo-dev`
profile for submissions, never root in a runtime. Supply a reviewed immutable
tar.gz bundle containing the Infra tree (including provider locks, staging
roots, scripts and `terraform/prod/templates/api-user-data.sh`), parent hooks,
Locust runtime and fixed backend source/image artifacts. Each file must be a
regular file or directory, with no links, devices or parent-path escapes.

```sh
AWS_PROFILE=getddo-dev python3 scripts/staging.py up \
  --max-duration 120 --bundle /private/source.tar.gz --config /private/stage.json
AWS_PROFILE=getddo-dev python3 scripts/staging.py status --run-id RUN_ID
AWS_PROFILE=getddo-dev python3 scripts/staging.py collect --run-id RUN_ID
AWS_PROFILE=getddo-dev python3 scripts/staging.py destroy --run-id RUN_ID
```

The up result is `{run_id,build_id,expires_at}`. Submission and subsequent
collection/deletion run in AWS even after the terminal closes. Status returns
the non-secret S3 manifest; inspect its phase and the returned CodeBuild ID.
An explicit run ID also works after successful deletion releases the singleton
lock. Repeated destroy of a destroyed run returns its destroyed phase.

The non-secret config has this shape (substitute the actual Infra commit,
reviewed Amazon Linux 2023 x86_64 AMI and existing CloudFront origin):

```json
{
  "source_sha": "0000000000000000000000000000000000000000",
  "public_origin": "https://example.cloudfront.net",
  "terraform": {
    "ami_id": "ami-0123456789abcdef0",
    "monitoring_security_group_id": "sg-04954d4932de5fa69",
    "shared_alb_security_group_id": "sg-09e12310ecd082ffb"
  },
  "hooks": {
    "prepare": "scripts/staging_hooks/prepare.py",
    "collect": "scripts/staging_hooks/collect.py",
    "detach": "scripts/staging_hooks/detach.py"
  }
}
```

Bundle SHA256 is computed locally and checked again by the persistent trusted
bootstrap before any bundled code runs. Conditional S3 creation of
`staging/control/active.json` serializes runs; only its matching run and digest
can release it. The run's exact expiry schedule must exist and match before
workload apply and again before ready. Failed up builds independently trigger
the cleanup project; stale events cannot clean another run. If submission is
interrupted after lock acquisition, inspect the run manifest and Scheduler
before recovery; retain ownership until cleanup succeeds. Never force-delete
the singleton lock to bypass an unresolved cleanup.

## Integration and secret contracts

All hooks receive the full manifest as JSON on stdin and their action name as
the only argument. The parent owns these bundled hooks:

- `prepare`: deploy immutable backend, initialize/seed the synthetic read-only
  smoke scenario, invoke the Locust installer, publish monitor targets and wait
  for matching acknowledgment. Return success only after readiness; never start
  load. Backend identities live in `scripts/staging_hooks/backend.json`.
- `collect`: stop load, save results under
  `staging/runs/RUN_ID/results/`, and print only `{key,sha256}` on stdout. The
  lifecycle verifies object digest by readback. Collection has a 360-second bound
  for Locust and monitor snapshots together.
- `detach`: remove UI/monitor publication via
  `staging/monitoring/request.json` and matching status acknowledgment. It must
  tolerate partial up and missing hosts. The Terraform rule is removed by destroy.

The parent owns HTTP UI/run/stop commands. The `deployment` output/manifest
contains `account_id`, `region`, `vpc_id`, `api_instances` keyed a/c with
`{id,private_ip}`, `locust_instance` with `{id,private_ip}`,
`target_group_arn`, `api_url`, `redis_endpoint`, `db_endpoint`, `db_identifier`,
`redis_cluster_id`, `load_balancer_arn` and `locust_target_group_arn`.
The full manifest also contains `run_id` and `expires_at`.

The parent runs `scripts/staging_locust_install.py` as root on the dedicated
Locust host with stdin `{manifest,actor:{user_id,role:"USER"}}` after seeding.
It reads the three existing persistent UI parameters in host memory, hashes
the password and writes root-only host configuration. It starts an idle
master/two-worker Compose runtime; no test is automatically started.

App parameters are confined to `/getddo/staging/app/`, tagged with the run ID.
Generated DB/Redis passwords enter Terraform via private stdin as ephemeral
variables and write-only provider attributes. They do not enter State or
command arguments. Command output is captured and omitted from failure messages.
The three `/getddo/staging/locust/` credentials are outside workload State and
cleanup lists; control roles explicitly cannot read/mutate their values.
Metadata/version/tags are compared after cleanup without decrypting values.

Normal destroy pauses if collection/readback or detach fails. TTL and rollback
bound collection and attempt deletion even if collection/detach fails. They
terminate instances and delete the DB, cache, ALBs/TGs, temporary rules, SGs,
roles/profiles, app log group and run-owned app parameters. After Terraform
destroy succeeds, `staging/locks/app.json` is deleted only when its `run-id`
metadata matches this run, using its ETag as a conditional-delete guard.
Per-run release artifacts remain preserved. Residual inventory
polls independently of State for up to ten minutes; failures retain ownership
and propagate. Persistent State, run manifests/reports, control services,
shared resources and three login parameters remain deliberately preserved.

## Local validation and apply gates

```sh
uv run --python 3.12 --with boto3==1.40.59 --with Werkzeug==3.1.3 \
  python -m unittest discover -s scripts/tests -v
terraform -chdir=terraform/staging init -backend=false -lockfile=readonly
terraform -chdir=terraform/staging validate
terraform -chdir=terraform/staging test
terraform -chdir=terraform/staging-control init -backend=false -lockfile=readonly
terraform -chdir=terraform/staging-control validate
terraform -chdir=terraform/staging-control test
terraform -chdir=terraform/bootstrap test
```

Coordinator gates: validate rendered policies with Access Analyzer, simulate
representative allows/denies, review full bootstrap/control plans and quotas,
then apply. The shared-SG revoke permission is scoped to the exact existing ALB
SG and monitor SG; EC2 does not support per-rule tag conditions for that action. Live acceptance
must cover partial-up failure, terminal disconnect, TTL expiry, collection
failure, persistent credential metadata preservation and complete residual
deletion. This code and mock validation alone provide no live acceptance proof.

References: [Scheduler group trust](https://docs.aws.amazon.com/scheduler/latest/UserGuide/cross-service-confused-deputy-prevention.html),
[CodeBuild quotas](https://docs.aws.amazon.com/codebuild/latest/userguide/limits.html),
[EC2 authorization](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonec2.html).
