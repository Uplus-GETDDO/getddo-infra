# Prod monitoring preparation (GD-297)

This independent Terraform root observes the existing production API A/C hosts. Existing AWS names, the `terraform/prod` resource addresses, and its legacy `dev/terraform.tfstate` key stay unchanged. GD-298 owns application instrumentation and the private 9091 listener; GD-299 owns approval, cloud changes, installation and runtime verification. Nothing in this PR applies Terraform or installs software on existing hosts.

## Infrastructure and access

`terraform/monitoring` reuses the existing State bucket with `monitoring/terraform.tfstate`. It reads the existing VPC, public-a subnet/IGW route, API SG and running A/C hosts by their exact existing names. It never reads remote application State or application secrets. The API SG has standalone rules, so the monitoring root can own the new source-monitoring-SG rules for 9091/9100 before that SG exists. No API user data, API EC2, database, ALB, existing SG rule or production parameter changes are required.

The candidate host is x86_64 AL2023, t3.medium, standard CPU credits (sustained CPU can throttle), encrypted gp3 root 20 GiB plus independently attached encrypted gp3 data 30 GiB. The data resource has `prevent_destroy` and is not an EC2 block-device deletion dependency. Replacement requires stopping monitoring, unmounting and orderly detachment first; never force-detach. Reattach in the same AZ and reuse the filesystem UUID. Cross-AZ recovery requires an approved snapshot/restore procedure. EBS persistence is not an automatic backup.

Management ingress is absent. Grafana publishes only `127.0.0.1:3000`; use SSM local port forwarding. Prometheus 9090 has no host publish. node_exporter binds the host private IP on 9100. Existing API rules keep 9091/9100 private to the monitoring SG. HTTPS egress uses the existing IGW plus a new public IPv4, without a NAT addition. DNS uses the existing VPC resolver.

Grafana obtains short-lived EC2 role credentials through required IMDSv2 with hop limit 2 for the trusted Docker bridge containers. No AWS access keys or credentials file are provisioned. The role has CloudWatch metric read/list (these APIs require wildcard resources), scoped existing API log reads, session-agent connectivity, and `GetParameter` only on `/getddo/monitoring/grafana/admin-password`. It deliberately avoids `AmazonSSMManagedInstanceCore`, whose wildcard parameter reads would expose application secrets. StopQuery/DescribeLogGroups require wildcard resources; GetQueryResults supports the log-group resource. IAM alone cannot isolate all CloudWatch metric namespaces for read APIs.

Register the dedicated SecureString only after GD-299 approval through a secure operator input flow, using the default `alias/aws/ssm` key. A customer-managed key would require separately reviewed decrypt scope. Terraform manages no password resource/value and does not read this parameter. The host fetches it into a 0400 UID 472 file under `/run` with no shell tracing. Grafana uses its file setting; changing this bootstrap parameter does not rotate an already-initialized Grafana database password. Never paste secret values into commands, logs, PRs or this repository.

## Installation after approval

The files are installation artifacts, not executed SSM commands. Review and transfer only `observability/` and required `scripts/` to `/opt/getddo-monitoring` on the new host; do not transfer Terraform State, plans or private variables. AL2023 must provide working AWS CLI, SSM agent, HTTPS egress and Python. Use the actual created data-volume ID from Terraform output:

```sh
sudo /opt/getddo-monitoring/scripts/monitoring-host.sh prepare "$DATA_VOLUME_ID" --format-empty
sudo /opt/getddo-monitoring/scripts/monitoring-host.sh install
```

`prepare` accepts only the matching EBS NVMe by-id device, refuses a mounted device, refuses existing unknown signatures/filesystems and formats only an explicitly approved empty volume. Existing ext4 data is reused without formatting. It records the volume ID and a required UUID fstab mount. Retrying a partly completed preparation requires operator inspection; never delete a filesystem to make the script proceed.

Use an approved operator identity with EC2 describe and CloudWatch GetDashboard to render current private IPs and current AWS CloudWatch dimensions outside Git, after the monitor exists:

```sh
python3 scripts/monitoring-targets.py \
  --output "$PRIVATE_RENDER_DIR/prometheus" \
  --cloudwatch-dashboard "$PRIVATE_RENDER_DIR/cloudwatch-generated.json"
```

Copy the two target JSON files into `/opt/getddo-monitoring/observability/prometheus/` and the generated dashboard into its Grafana dashboards directory. Those generated repository paths are ignored by Git. Refresh targets after any A/C replacement; targets keep the default private-IP:port `instance` label, `environment=prod`, `server=A|C|monitoring`, and `job=api|node`. CloudWatch panels reuse the existing `getddo-dev-overview` dimensions and legacy `environment=dev` searches; their display remains Prod. Do not rename existing metrics or resource IDs.

Run `scripts/install-node-exporter.sh` locally on each approved A/C/monitoring host, then start `getddo-monitoring` only on the new monitor. The exporter, Prometheus, Grafana and Compose releases are checksum/digest pinned. The Docker drop-in is installed **only on the new monitor**, before Docker is enabled: Docker itself checks the expected mounted EBS and prepares the volatile secret before it can restart containers. The main unit repeats the mount guard. Bind mounts disable automatic path creation, so missing disks or secret files fail closed. No global API Docker settings change.

After these gates, enable `getddo-monitoring.service`. Stop via systemd, not `compose down -v`. If EBS or the SecureString is unavailable after reboot, Docker intentionally fails; restore those dependencies and explicitly restart Docker and monitoring. Never bypass the drop-in to get a partial stack running.

Forward local port 3000 through `AWS-StartPortForwardingSession` to the monitoring instance's port 3000 using an approved operator session policy. Verify Grafana's CloudWatch data-source health using instance-role credentials, and verify an unsigned IMDS request is denied while IMDSv2 works from the trusted container. Live credential, SSM tunnel and browser checks await creation; local configuration tests do not prove them.

## Capacity, dashboards and backups

Prod scraping is 15s with 10s timeout, body/sample/label/target limits. The API dashboard covers scrape health, HTTP rate/errors/p95, JVM/GC, Hikari, Tomcat, CPU/memory/filesystems and Prometheus series. An absent metric remains absent, rather than a synthetic zero. p95 requires GD-298 histogram series; Tomcat metrics require the BE registry configuration. A second dashboard is rendered from the actual existing CloudWatch dashboard for EC2/ALB/RDS/Redis/app metrics. Its 1m refresh does not increase CloudWatch source resolution.

Prometheus uses both 14d and 20GB retention, whichever binds first. The 30 GiB data disk must also fit WAL/head chunks, compaction, Grafana SQLite and filesystem overhead. These limits do not guarantee a 14-day history or prevent every disk-full condition; observe disk-free/head-series panels and adjust only after measurement. Containers have memory/CPU limits and bounded Docker logs; the 4 GiB host retains OS/agent headroom. This single host provides no HA or alert delivery.

`monitoring-backup.sh backup /absolute/private/archive.tar.gz` stops the stack, archives the complete offline TSDB (including WAL) and Grafana SQLite, writes a checksum, and resumes the service even on backup failure. Use a separate approved destination with enough space; the 20 GiB OS disk is not a full-data backup destination. Archive contents are sensitive (Grafana DB may contain credentials); mode 0600 is only local access control. Encrypt and export off-host through an approved operator mechanism, retain the matching code revision and filesystem configuration, and preserve the external SecureString independently. No backup upload IAM or automatic cloud backup is added.

For restore, stop monitoring and Docker first to prevent restart policies writing data. Keep a rollback copy/snapshot of the current volume. On the verified replacement mount, create empty `prometheus/` and `grafana/` directories, then run `monitoring-backup.sh restore` with the verified archive. The tool refuses nonempty destinations, validates archive paths/types, restores ownership and leaves services stopped. Start Docker and monitoring explicitly; verify target ingestion, historical queries and Grafana users/datasources before declaring success. Never copy a live TSDB and call it a consistent backup.

## Checks and approval boundary

Run `python3 scripts/validate.py terraform` and `python3 scripts/validate.py scripts`. For daemon-free Compose/Prometheus/PromQL validation, install local check tools with `scripts/install-monitoring-check-tools.sh` into a temporary directory, install the CI-pinned PyYAML, and run `scripts/check-monitoring.py` with `PROMTOOL_BIN`/`COMPOSE_BIN`. CI executes those same checks without personal HTML or AWS credentials.

The existing operator policies exclude the new State key and monitoring IAM read ARNs. `terraform/bootstrap/monitoring.tf` prepares those planning permissions. The existing operator network/identity/parameters/operations policies also prepare exact-name/role/parameter-scoped monitoring launch, encrypted volume create/attach, EC2-only PassRole, password registration and SSM local forwarding/install permissions. All policy changes are unapplied. Current real plans used the authenticated root caller in `getddo-dev`, which does not prove non-root operator readiness. GD-299 must review and approve these bootstrap policy changes before operator execution; existing live operator permissions cover API-sized launches only. IAM write access is limited to the dedicated monitoring role/profile and shares the existing operator trust model. Data-volume deletion is intentionally not granted; preservation is the default. No live IAM grant is made here.

Before any apply, obtain approval, use an approved non-root operator, rerun all full plans against current State/code, check new resource cost/permissions, verify the GD-298 management-listener contract, and review exact SG sources. Then apply/install through GD-299. Runtime acceptance includes A/C/monitoring targets up, non-missing required metrics, CloudWatch role authentication, private endpoint isolation, SSM login, crash/reboot mount/secret persistence and an actual backup restore. All remain pending until approved execution. Roll back by restoring the last reviewed Compose/config and data backup or by removing only this root's new collection rules; preserve existing API/ALB connectivity and the retained data volume.
