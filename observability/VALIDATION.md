# GD-297 validation evidence — 2026-10-22

All Terraform plans below were real, full-root plans using `getddo-dev`, with no `-target`, fake infrastructure IDs, apply, import or State move. The authenticated caller was root; non-root operator policy effectiveness and all runtime acceptance remain GD-299 gates. Saved binary plans, JSON, original private prod variables and logs are outside Git in `/tmp/gd297-private/` (directory 0700, files 0600); do not attach them to a PR.

| Root | Detailed exit | Create | Update | Delete | Replace | No-op |
| --- | --- | --- | --- | --- | --- | --- |
| monitoring | 2 | 15 | 0 | 0 | 0 | 0 |
| prod baseline | 0 | 0 | 0 | 0 | 0 | 74 |
| bootstrap | 2 | 1 | 4 | 0 | 0 | 23 |

Monitoring creates one EC2, one retained EBS, one volume attachment, one SG, three ingress rules (API 9091/9100 and monitor self-9100), five egress rules, one role/profile and two inline runtime policies. No existing API EC2, root disk, database or application resource is replaced/deleted/updated. Bootstrap proposes one planning-access inline policy and updates the four existing operator identity/network/operations/parameters policies; existing policy/resource addresses and descriptions are preserved. None of these grants has been applied.

Prod baseline detects only external RDS `latest_restorable_time` movement, a computed backup/restore timestamp. There are no desired prod changes. The baseline used privately copied existing variables and random **plan-only ephemeral write-only inputs**, which are not credentials read from the application and trigger no password update at the unchanged version. Nothing was applied.

## Plan source identification

Each SHA256 hashes sorted relative filenames plus NUL-delimited contents for root `.tf`, templates `.json/.sh/.tftpl` and `.terraform.lock.hcl`; excludes tests, `.terraform/`, State and private variables. The private binary plan embeds its complete configuration. Code or State changes require fresh plans before approval.

- `monitoring`: plan timestamp `2026-10-22T10:19:29Z`, source SHA256 `244978f1a801ef7c709d2667a311662eea49cd05e5847b6bd4823f2a655470c4`.
- `prod`: plan timestamp `2026-10-22T10:11:12Z`, source SHA256 `abfdc78b5e801d9e43043899ccdc799295c258387978d7cd14f7545a18906277`.
- `bootstrap`: plan timestamp `2026-10-22T10:19:45Z`, source SHA256 `ca0bf509116dd435ad9eaf69ac17dedad4cbc790a41e33b9786e30fb51094ea6`.

## Executed verification

- Terraform 1.16.5 / AWS provider 6.68.0: fmt and validate on all three roots; 26 mock runs passed (bootstrap 3, prod 20, monitoring 3).
- Python/shell/JSON validation and 12 unit tests passed, including absent mount, wrong UUID/volume marker and Docker independent-start guard ordering and quiescing containers when their unit is inactive, and transferred-archive checksum validation.
- Checksum-pinned Compose 5.6.1 validates configuration without a daemon; Prometheus/promtool 3.15.1 validates scrape syntax and all 14 dashboard PromQL expressions.
- Live read-only CloudWatch dashboard conversion produced 23 Grafana panels using current actual dimensions; generated output stays private.
- AWS Access Analyzer validation of all four modified operator policies returned no findings. This is syntax/security-policy validation, not proof that every future request is authorized.
- CI runs Terraform tests, shell/JSON/Python tests, YAML parsing, pinned Compose configuration and promtool/PromQL checks. CI outcome is recorded in the PR and final handoff.

## Candidate monthly cost

AWS Pricing API queried on 2026-10-22 for Asia Pacific (Seoul), Linux/shared on-demand: t3.medium USD 0.052/hour; gp3 USD 0.0912/GB-month; in-use public IPv4 USD 0.005/hour. At 730 hours, EC2 = 37.96, 50 GiB total EBS = 4.56 (20 root + 30 data), IPv4 = 3.65: **USD 46.17/month**, before tax/credits. Existing IGW/public IPv4 outbound avoids a new NAT. Traffic (including C cross-AZ scraping), CloudWatch API/query usage, external backups/snapshots and future capacity changes are additional. Standard CPU credits avoid unlimited-credit surplus charges but may throttle sustained work. This is a candidate estimate, not measured utilization or a capacity guarantee.

Sources: [EC2 pricing](https://aws.amazon.com/ec2/pricing/on-demand/), [EBS pricing](https://aws.amazon.com/ebs/pricing/), [VPC public IPv4 pricing](https://aws.amazon.com/vpc/pricing/). IMDS/container configuration follows [AWS metadata options](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-options.html), retention follows [Prometheus storage](https://prometheus.io/docs/prometheus/latest/storage/), and the SSM parameter-read pitfall is documented in [ManagedInstanceCore](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AmazonSSMManagedInstanceCore.html).

## Unapplied approval boundary

No cloud resource changes, IAM/SG mutations, password registration/read, SendCommand, live installation/restart, merge, auto-merge or deployment occurred. GD-298 must supply the compatible private management listener and histogram metrics. GD-299 must approve and apply reviewed operator policies/resources, securely register the dedicated secret, install the artifacts and verify SSM/IMDSv2/Grafana credentials, actual metrics, reboot/crash persistence and backup restoration. No runtime success is claimed before that work.
