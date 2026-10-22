# GETDDO infrastructure

Infrastructure source for the existing production service. No separate Dev server is planned. Staging/NCP/load-test and Prometheus/Grafana implementation are deferred; the load-test tool is undecided.

## Execution roots

| Root | Existing S3 State key | Scope |
| --- | --- | --- |
| `terraform/bootstrap` | `bootstrap/terraform.tfstate` | State S3 protection and existing operator IAM |
| `terraform/prod` | `dev/terraform.tfstate` | Existing production network, API A/C, ALB/CloudFront, RDS, Redis, S3, runtime/CD IAM and CloudWatch |

`terraform/prod` was renamed locally from the former dev directory. Keep the existing State key, Terraform resource addresses, AWS `getddo-dev-*` resource names, Parameter Store paths, metric labels and log groups. A directory rename does not require AWS resource replacement or another import. Existing IAM and CloudWatch objects have already been registered in State; do not repeat imports.

## Inputs and secrets

`terraform/prod/prod.tfvars.example` is a public placeholder example. Real `*.tfvars*`, State/backup/plan files, provider caches, env files and credentials are excluded from Git. Provider `.terraform.lock.hcl` files are committed. DB/Redis secrets use ephemeral/write-only inputs; do not put real passwords in examples, command output or PR attachments.

Existing Git-ignored `dev.auto.tfvars.json`, local `dev-secrets.json`, SSH key and AWS CLI profile `getddo-dev` retain their names. They are not included here. The AWS profile name does not guarantee the calling identity: check the actual account and principal before live commands.

Terraform owns infrastructure/IAM. The backend repository owns Java, Docker/Compose, application configuration and app CD. Its legacy `infra/scripts/dev.py` now resolves sibling `getddo-infra/terraform/prod` or `$GETDDO_INFRA_DIR/terraform/prod`; its legacy JAR deployment action is not the rolling CD entry point. Existing GitHub OIDC trust remains on the backend repository.

## Checks and changes

Run `python3 scripts/validate.py` for fmt, validate, mock Terraform tests and script/config syntax. The two CI jobs call `python3 scripts/validate.py terraform` and `python3 scripts/validate.py scripts`. These checks use temporary roots and mock AWS; they do not deploy. Optional `python3 scripts/validate.py documents` validates documentation and six regression tests.

For real changes, authenticate with the intended operator role, initialize the existing backend, supply approved private inputs, and review a full plan with State locking. Never disable deletion protection or apply a replacement merely to fix a naming mismatch. Do not publish State, plan JSON or private inputs.

The 2026-10-22 directory move passed 22 Terraform mock tests, script checks and document checks. Live plans before and after returned exit code 0 with no resource/output changes; the State identity and managed addresses were preserved. No apply, commit or push was performed for that move.

## Existing security limits

This source preserves the existing deployed setup; publishing it does not harden or redeploy AWS. CloudFront is an external API entry point, while the ALB, RDS and Redis are private. The application's demonstration user-selection headers are not login authentication. A private GitHub repository does not restrict access to that public API. Reassess authentication/access controls before treating it as a public production service.

Operator IAM is privileged infrastructure-management access, not an application/user role. Runtime and backend deployment roles remain separate. Metadata names/ARNs in source are operational identifiers, not credentials.

## Publication scope

This initial source set contains only the active production/bootstrap configuration, its required templates and tests, validation scripts and CI. Historical documents, architecture images, archived IAM mutation tools, unapplied permission drafts and load-test design are deliberately deferred. Personal HTML and result files stay local. Do not use `git add .` against the full local working tree; publish only the reviewed file list.

The source contains actual AWS account IDs, resource names, ARNs and deployment topology. These are not passwords or access tokens, but reveal operational metadata. A private repository is recommended for the initial publication; review and sanitize separately before making it public.
