mock_provider "aws" {
  alias = "edge"

  mock_resource "aws_wafv2_web_acl" {
    defaults = { arn = "arn:aws:wafv2:us-east-1:942466928325:global/webacl/getddo-dev-api-waf/12345678-1234-1234-1234-123456789abc" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:942466928325:log-group:aws-waf-logs-getddo-dev-api" }
  }
}

# AWS에 자원을 만들지 않고 외부 노출 방지·비밀번호 처리 계약을 확인한다.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "942466928325" }
  }

  # Dashboard rendering needs the actual two-node shape, not an empty mock set.
  mock_resource "aws_elasticache_replication_group" {
    defaults = { member_clusters = ["getddo-dev-redis-001", "getddo-dev-redis-002"] }
  }

  # 새 ALB·CloudFront 참조 값도 형식이 유효한 테스트 전용 값으로 제공한다.
  mock_resource "aws_lb" {
    defaults = {
      arn      = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:loadbalancer/app/getddo-dev-api-alb/0123456789abcdef"
      dns_name = "internal-test.ap-northeast-2.elb.amazonaws.com"
    }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:942466928325:targetgroup/getddo-dev-api-tg/0123456789abcdef" }
  }
}

variables {
  admin_ipv4_cidr    = "203.0.113.10/32"
  ami_id             = "ami-0123456789abcdef0"
  ssh_public_key     = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB9vBqHizcQYJVtjb3VcYO3qs95kKhQuwpDVHRptYI0G test-only"
  db_master_password = "TestOnlyPasswordNotUsedInAWS123456"
  redis_auth_token   = "TestOnlyRedisPasswordNotUsed123456"
}

run "private_ha_redis_with_tls_and_auth" {
  command = apply

  assert {
    condition = (
      aws_elasticache_replication_group.redis.engine == "redis" &&
      aws_elasticache_replication_group.redis.engine_version == "7.1" &&
      aws_elasticache_replication_group.redis.cluster_mode == "disabled" &&
      aws_elasticache_replication_group.redis.num_cache_clusters == 2 &&
      aws_elasticache_replication_group.redis.automatic_failover_enabled &&
      aws_elasticache_replication_group.redis.multi_az_enabled &&
      aws_elasticache_replication_group.redis.at_rest_encryption_enabled &&
      aws_elasticache_replication_group.redis.transit_encryption_enabled &&
      aws_elasticache_replication_group.redis.transit_encryption_mode == "required" &&
      aws_elasticache_replication_group.redis.auth_token_wo_version == 1
    )
    error_message = "Redis OSS 원본·복제본 두 대와 저장 암호화·필수 TLS·write-only 인증 버전을 유지해야 합니다."
  }

  assert {
    condition = (
      toset(aws_elasticache_subnet_group.redis.subnet_ids) == toset([for subnet in aws_subnet.data : subnet.id]) &&
      alltrue([for subnet in aws_subnet.data : !subnet.map_public_ip_on_launch]) &&
      aws_elasticache_replication_group.redis.subnet_group_name == aws_elasticache_subnet_group.redis.name &&
      toset(aws_elasticache_replication_group.redis.security_group_ids) == toset([aws_security_group.redis.id]) &&
      aws_security_group.redis.vpc_id == aws_vpc.main.id &&
      aws_vpc_security_group_ingress_rule.redis_from_api.security_group_id == aws_security_group.redis.id &&
      aws_vpc_security_group_ingress_rule.redis_from_api.referenced_security_group_id == aws_security_group.api.id &&
      aws_vpc_security_group_ingress_rule.redis_from_api.cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.redis_from_api.cidr_ipv6 == null &&
      aws_vpc_security_group_ingress_rule.redis_from_api.from_port == 6379 &&
      aws_vpc_security_group_ingress_rule.redis_from_api.to_port == 6379 &&
      output.redis_primary_endpoint == aws_elasticache_replication_group.redis.primary_endpoint_address &&
      output.redis_port == 6379
    )
    error_message = "Redis는 기존 사설 data 서브넷에서 API 보안 그룹의 6379 연결만 허용해야 합니다."
  }
}

run "reject_invalid_redis_password" {
  command = plan
  variables {
    redis_auth_token = "short"
  }
  expect_failures = [var.redis_auth_token]
}

run "reject_invalid_redis_password_version" {
  command = plan
  variables {
    redis_auth_token_version = 0
  }
  expect_failures = [var.redis_auth_token_version]
}

run "private_database_and_limited_operator_access" {
  command = apply

  variables {
    # 기본값 1 고정이 아니라 실제 입력 버전을 전달하는 계약을 검사한다.
    db_master_password_version = 7
  }

  assert {
    condition = (
      aws_db_instance.main.multi_az &&
      !aws_db_instance.main.apply_immediately &&
      !aws_db_instance.main.publicly_accessible &&
      aws_db_instance.main.storage_encrypted &&
      aws_db_instance.main.deletion_protection &&
      aws_db_instance.main.backup_retention_period == 1 &&
      !aws_db_instance.main.skip_final_snapshot &&
      aws_db_instance.main.final_snapshot_identifier == "getddo-dev-mysql-final" &&
      aws_db_instance.main.copy_tags_to_snapshot &&
      aws_db_instance.main.password_wo_version == var.db_master_password_version
    )
    error_message = "운영 DB는 비공개 Multi-AZ·기본 유지보수 창 적용과 암호화·삭제 보호·백업·입력 비밀번호 버전을 유지해야 합니다."
  }

  assert {
    condition = (
      length(aws_subnet.data) == 2 &&
      length(toset([for subnet in aws_subnet.data : subnet.availability_zone])) == 2 &&
      alltrue([for subnet in aws_subnet.data : !subnet.map_public_ip_on_launch]) &&
      toset(aws_db_subnet_group.main.subnet_ids) == toset([for subnet in aws_subnet.data : subnet.id]) &&
      aws_db_instance.main.db_subnet_group_name == aws_db_subnet_group.main.name &&
      length(aws_route_table.data.route) == 0
    )
    error_message = "RDS는 서로 다른 두 AZ의 사설 data 서브넷과 외부 기본 경로 없는 라우팅을 사용해야 합니다."
  }

  assert {
    condition     = alltrue([for rule in aws_vpc_security_group_ingress_rule.api_operator : rule.cidr_ipv4 == "203.0.113.10/32"])
    error_message = "SSH와 API 접근은 관리자 IPv4 한 개로 제한해야 합니다."
  }

  assert {
    condition = (
      toset(aws_db_instance.main.vpc_security_group_ids) == toset([aws_security_group.db.id]) &&
      aws_security_group.db.vpc_id == aws_vpc.main.id &&
      aws_vpc_security_group_ingress_rule.db_from_api.security_group_id == aws_security_group.db.id &&
      aws_vpc_security_group_ingress_rule.db_from_api.referenced_security_group_id == aws_security_group.api.id &&
      aws_vpc_security_group_ingress_rule.db_from_api.cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.db_from_api.cidr_ipv6 == null &&
      aws_vpc_security_group_ingress_rule.db_from_api.ip_protocol == "tcp" &&
      aws_vpc_security_group_ingress_rule.db_from_api.from_port == 3306 &&
      aws_vpc_security_group_ingress_rule.db_from_api.to_port == 3306
    )
    error_message = "DB는 기존 DB 보안 그룹에서 API 보안 그룹의 TCP 3306 연결만 허용해야 합니다."
  }

  assert {
    condition     = aws_instance.api.metadata_options[0].http_tokens == "required" && aws_instance.api.root_block_device[0].encrypted
    error_message = "EC2는 IMDSv2와 암호화 디스크를 사용해야 합니다."
  }
}

run "approved_database_immediate_apply" {
  command = plan
  variables {
    db_apply_immediately = true
  }
  assert {
    condition     = aws_db_instance.main.apply_immediately && aws_db_instance.main.multi_az
    error_message = "승인된 전환 plan에서만 RDS 즉시 적용을 명시적으로 선택할 수 있어야 합니다."
  }
}

run "reject_open_operator_access" {
  command = plan
  variables {
    admin_ipv4_cidr = "0.0.0.0/0"
  }
  expect_failures = [var.admin_ipv4_cidr]
}

run "private_alb_and_https_api_without_shared_cache" {
  # mock apply는 로컬 검증이다. AWS 생성 API와 원격 State는 사용하지 않는다.
  command = apply

  assert {
    condition = (
      aws_lb.api.internal &&
      length(aws_subnet.alb) == 2 &&
      length(toset([for subnet in aws_subnet.alb : subnet.availability_zone])) == 2 &&
      alltrue([for subnet in aws_subnet.alb : !subnet.map_public_ip_on_launch]) &&
      aws_lb_target_group_attachment.api.target_id == aws_instance.api.id
    )
    error_message = "ALB는 두 AZ의 사설 서브넷을 사용하고 기존 EC2 한 대에 연결해야 합니다."
  }

  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.alb_from_cloudfront.cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.alb_from_cloudfront.referenced_security_group_id == data.aws_security_group.cloudfront_vpc_origin.id &&
      aws_vpc_security_group_ingress_rule.alb_from_cloudfront.from_port == 80 &&
      aws_vpc_security_group_egress_rule.alb_to_api.referenced_security_group_id == aws_security_group.api.id &&
      aws_vpc_security_group_egress_rule.alb_to_api.from_port == 8080 &&
      aws_vpc_security_group_ingress_rule.api_from_alb.referenced_security_group_id == aws_security_group.alb.id
    )
    error_message = "CloudFront 서비스 그룹 → ALB:80 → API:8080 경로만 추가해야 합니다."
  }

  assert {
    condition = (
      aws_cloudfront_distribution.api.viewer_certificate[0].cloudfront_default_certificate &&
      aws_cloudfront_distribution.api.default_cache_behavior[0].viewer_protocol_policy == "https-only" &&
      aws_cloudfront_distribution.api.default_cache_behavior[0].cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" &&
      aws_cloudfront_distribution.api.default_cache_behavior[0].origin_request_policy_id == "b689b0a8-53d0-40ab-baf2-68738e2966ac" &&
      contains(aws_cloudfront_distribution.api.default_cache_behavior[0].allowed_methods, "POST") &&
      contains(aws_cloudfront_distribution.api.default_cache_behavior[0].allowed_methods, "OPTIONS") &&
      one([for origin in aws_cloudfront_distribution.api.origin : origin if origin.origin_id == "getddo-dev-api-alb"]).vpc_origin_config[0].vpc_origin_id == aws_cloudfront_vpc_origin.api.id &&
      aws_cloudfront_vpc_origin.api.vpc_origin_endpoint_config[0].origin_protocol_policy == "http-only"
    )
    error_message = "기본 HTTPS 주소·캐시 비활성화·요청 전달·VPC origin 연결을 유지해야 합니다."
  }
}

run "ssh_endpoint_reaches_only_api_ssh" {
  command = apply

  assert {
    condition = (
      !aws_ec2_instance_connect_endpoint.ssh.preserve_client_ip &&
      aws_vpc_security_group_egress_rule.ssh_endpoint_to_api.referenced_security_group_id == aws_security_group.api.id &&
      aws_vpc_security_group_egress_rule.ssh_endpoint_to_api.from_port == 22 &&
      aws_vpc_security_group_egress_rule.ssh_endpoint_to_api.to_port == 22 &&
      aws_vpc_security_group_egress_rule.ssh_endpoint_to_api.cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.api_from_ssh_endpoint.referenced_security_group_id == aws_security_group.ssh_endpoint.id &&
      aws_vpc_security_group_ingress_rule.db_from_api.referenced_security_group_id == aws_security_group.api.id
    )
    error_message = "관리 터널은 API의 SSH만 허용하며 DB 접근 주체는 API 보안 그룹으로 유지해야 합니다."
  }
}

run "private_images_and_read_only_cloudfront_origin" {
  command = apply

  assert {
    condition = (
      aws_s3_bucket.images.bucket == "getddo-images-942466928325-ap-northeast-2" &&
      aws_s3_bucket.images.bucket != aws_s3_bucket.releases.bucket &&
      !aws_s3_bucket.images.force_destroy &&
      aws_s3_bucket_public_access_block.images.block_public_acls &&
      aws_s3_bucket_public_access_block.images.block_public_policy &&
      aws_s3_bucket_public_access_block.images.ignore_public_acls &&
      aws_s3_bucket_public_access_block.images.restrict_public_buckets &&
      aws_s3_bucket_ownership_controls.images.rule[0].object_ownership == "BucketOwnerEnforced" &&
      one(aws_s3_bucket_server_side_encryption_configuration.images.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "AES256"
    )
    error_message = "이미지 버킷은 배포와 분리하고 비공개·ACL 비활성화·암호화 상태여야 합니다."
  }

  assert {
    condition = (
      jsondecode(aws_s3_bucket_policy.images.policy).Statement[0].Effect == "Deny" &&
      jsondecode(aws_s3_bucket_policy.images.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false" &&
      jsondecode(aws_s3_bucket_policy.images.policy).Statement[1].Principal.Service == "cloudfront.amazonaws.com" &&
      toset(jsondecode(aws_s3_bucket_policy.images.policy).Statement[1].Action) == toset(["s3:GetObject"]) &&
      jsondecode(aws_s3_bucket_policy.images.policy).Statement[1].Condition.StringEquals["AWS:SourceArn"] == aws_cloudfront_distribution.api.arn &&
      jsondecode(aws_s3_bucket_policy.images.policy).Statement[1].Resource == "${aws_s3_bucket.images.arn}/images/*" &&
      aws_cloudfront_origin_access_control.images.signing_behavior == "always" &&
      aws_cloudfront_origin_access_control.images.signing_protocol == "sigv4"
    )
    error_message = "HTTPS를 강제하고 이 CloudFront의 images/* 읽기만 허용해야 합니다."
  }

  assert {
    condition = (
      toset(jsondecode(aws_iam_role_policy.api_images.policy).Statement[0].Action) == toset(["s3:PutObject"]) &&
      jsondecode(aws_iam_role_policy.api_images.policy).Statement[0].Resource[0] == "${aws_s3_bucket.images.arn}/images/*" &&
      aws_instance.api.metadata_options[0].http_tokens == "required" &&
      aws_instance.api.metadata_options[0].http_put_response_hop_limit == 2
    )
    error_message = "백엔드 업로드 권한은 이미지 경로로 제한하고 Docker의 IMDSv2 역할 인증을 준비해야 합니다."
  }

  assert {
    condition = (
      one([for behavior in aws_cloudfront_distribution.api.ordered_cache_behavior : behavior if behavior.path_pattern == "/images/*"]).path_pattern == "/images/*" &&
      one([for behavior in aws_cloudfront_distribution.api.ordered_cache_behavior : behavior if behavior.path_pattern == "/images/*"]).target_origin_id == "getddo-dev-images" &&
      toset(one([for behavior in aws_cloudfront_distribution.api.ordered_cache_behavior : behavior if behavior.path_pattern == "/images/*"]).allowed_methods) == toset(["GET", "HEAD"]) &&
      one([for behavior in aws_cloudfront_distribution.api.ordered_cache_behavior : behavior if behavior.path_pattern == "/images/*"]).cache_policy_id == "658327ea-f89d-4fab-a63d-7e88639e58f6" &&
      aws_cloudfront_distribution.api.default_cache_behavior[0].cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" &&
      length([for origin in aws_cloudfront_distribution.api.origin : origin if origin.origin_id == "getddo-dev-images" && origin.origin_access_control_id == aws_cloudfront_origin_access_control.images.id]) == 1
    )
    error_message = "이미지 경로만 읽기 캐시를 사용하고 API는 기존 ALB·캐시 비활성화를 유지해야 합니다."
  }

  assert {
    condition = (
      length(one(aws_s3_bucket_lifecycle_configuration.images.rule).expiration) == 0 &&
      one(aws_s3_bucket_lifecycle_configuration.images.rule).abort_incomplete_multipart_upload[0].days_after_initiation == 1
    )
    error_message = "완료된 이미지는 만료하지 않고 미완료 업로드 조각만 정리해야 합니다."
  }
}

run "container_server_and_deployment_boundaries" {
  # mock_provider만 사용한다. 실제 AWS 자원 생성이나 원격 State 접근은 없다.
  command = apply

  assert {
    condition     = aws_iam_role.github_cd.max_session_duration == 7200
    error_message = "두 서버 순차 배포는 7200초 세션을 요청할 수 있어야 합니다."
  }

  assert {
    condition = (
      aws_instance.api.iam_instance_profile == aws_iam_instance_profile.api.name &&
      aws_iam_role_policy_attachment.api_ssm.policy_arn == "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" &&
      strcontains(aws_instance.api.user_data, "docker compose version") &&
      strcontains(aws_instance.api.user_data, "sha256sum --check") &&
      strcontains(aws_instance.api.user_data, "systemctl enable --now amazon-ssm-agent docker")
    )
    error_message = "EC2는 Docker·Compose·SSM과 서버 역할을 준비해야 합니다."
  }

  assert {
    condition = (
      aws_s3_bucket.releases.bucket == "getddo-deploy-942466928325-ap-northeast-2" &&
      !aws_s3_bucket.releases.force_destroy &&
      aws_s3_bucket_public_access_block.releases.block_public_acls &&
      aws_s3_bucket_public_access_block.releases.block_public_policy &&
      aws_s3_bucket_public_access_block.releases.ignore_public_acls &&
      aws_s3_bucket_public_access_block.releases.restrict_public_buckets &&
      aws_s3_bucket_ownership_controls.releases.rule[0].object_ownership == "BucketOwnerEnforced" &&
      one(aws_s3_bucket_server_side_encryption_configuration.releases.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "AES256" &&
      jsondecode(aws_s3_bucket_policy.releases.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
    )
    error_message = "배포 버킷은 State와 분리하고 비공개·암호화·TLS 전용으로 설정해야 합니다."
  }

  assert {
    condition = (
      toset(jsondecode(aws_iam_role_policy.api_releases.policy).Statement[0].Action) == toset(["s3:GetObject"]) &&
      jsondecode(aws_iam_role_policy.api_releases.policy).Statement[0].Resource[0] == "${aws_s3_bucket.releases.arn}/releases/*" &&
      toset(jsondecode(aws_iam_role_policy.github_cd.policy).Statement[0].Action) == toset(["s3:PutObject"]) &&
      jsondecode(aws_iam_role_policy.github_cd.policy).Statement[0].Resource[0] == "${aws_s3_bucket.releases.arn}/releases/*" &&
      toset(jsondecode(aws_iam_role_policy.github_cd.policy).Statement[1].Resource) == toset([
        aws_instance.api.arn, "arn:aws:ssm:ap-northeast-2::document/AWS-RunShellScript"
      ]) &&
      toset(jsondecode(aws_iam_role_policy.github_cd.policy).Statement[2].Action) == toset(["ssm:GetCommandInvocation"])
    )
    error_message = "파일 읽기·업로드와 SSM 실행 권한은 배포 버킷·대상 서버로 제한해야 합니다."
  }

  assert {
    condition = (
      jsondecode(aws_iam_role.github_cd.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com" &&
      jsondecode(aws_iam_role.github_cd.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:Uplus-GETDDO@341824447/getddo-be@1362269492:environment:production" &&
      output.github_production_variables.EC2_INSTANCE_ID == aws_instance.api.id &&
      output.github_production_variables.AWS_ROLE_ARN == aws_iam_role.github_cd.arn
    )
    error_message = "GitHub의 확인된 production 식별자만 허용하고 CD 연결값을 출력해야 합니다."
  }
}

run "reuse_existing_github_provider" {
  command = plan
  variables {
    github_oidc_provider_arn = "arn:aws:iam::942466928325:oidc-provider/token.actions.githubusercontent.com"
  }
  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 0
    error_message = "기존 GitHub OIDC 공급자를 지정하면 중복 생성하지 않아야 합니다."
  }
}

run "application_parameters_read_only_policy" {
  command = apply

  assert {
    condition = (
      aws_iam_role_policy.api_parameters.role == aws_iam_role.api.id &&
      toset(jsondecode(aws_iam_role_policy.api_parameters.policy).Statement[0].Action) == toset(["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]) &&
      toset(jsondecode(aws_iam_role_policy.api_parameters.policy).Statement[0].Resource) == toset([
        "arn:aws:ssm:ap-northeast-2:942466928325:parameter/getddo/dev/app",
        "arn:aws:ssm:ap-northeast-2:942466928325:parameter/getddo/dev/app/*"
      ]) &&
      toset(jsondecode(aws_iam_role_policy.api_parameters.policy).Statement[1].Action) == toset(["kms:Decrypt"]) &&
      jsondecode(aws_iam_role_policy.api_parameters.policy).Statement[1].Condition.StringEquals["kms:ViaService"] == "ssm.ap-northeast-2.amazonaws.com" &&
      jsondecode(aws_iam_role_policy.api_parameters.policy).Statement[1].Condition.StringLike["kms:EncryptionContext:PARAMETER_ARN"] == "arn:aws:ssm:ap-northeast-2:942466928325:parameter/getddo/dev/app/*"
    )
    error_message = "추가한 EC2 설정 정책은 앱 경로 읽기와 SSM을 통한 복호화만 허용해야 합니다."
  }
}

run "reject_other_region" {
  command = plan
  variables {
    aws_region = "us-east-1"
  }
  expect_failures = [var.aws_region]
}

run "reject_cross_account_provider" {
  command = plan
  variables {
    github_oidc_provider_arn = "arn:aws:iam::111122223333:oidc-provider/token.actions.githubusercontent.com"
  }
  expect_failures = [aws_iam_role.github_cd]
}

run "deployment_lock_has_only_exact_object_write_and_delete" {
  command = plan
  assert {
    condition = (
      toset(jsondecode(aws_iam_role_policy.github_cd_two_api.policy).Statement[3].Action) == toset(["s3:PutObject", "s3:DeleteObject"]) &&
      jsondecode(aws_iam_role_policy.github_cd_two_api.policy).Statement[3].Resource == "arn:aws:s3:::${aws_s3_bucket.releases.bucket}/locks/container-deploy.json" &&
      jsondecode(aws_iam_role_policy.github_cd_two_api.policy).Statement[3].Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2"
    )
    error_message = "CD lock permission must allow only put/delete of the exact deployment lock object."
  }
}

run "grafana_session_forwarding" {
  command = plan
  assert {
    condition     = length([for behavior in aws_cloudfront_distribution.api.ordered_cache_behavior : behavior if contains(["/grafana", "/grafana/*"], behavior.path_pattern) && behavior.target_origin_id == "getddo-dev-api-alb" && behavior.cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" && behavior.origin_request_policy_id == "216adef6-5c7f-47e4-b989-5492eafa07d3" && behavior.viewer_protocol_policy == "https-only" && contains(behavior.allowed_methods, "POST")]) == 2
    error_message = "Grafana requires both exact and child paths, no caching, HTTPS, and original viewer Host/cookies for login."
  }
}

run "locust_session_forwarding" {
  command = plan
  assert {
    condition     = length([for behavior in aws_cloudfront_distribution.api.ordered_cache_behavior : behavior if contains(["/locust", "/locust/*"], behavior.path_pattern) && behavior.target_origin_id == "getddo-dev-api-alb" && behavior.cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" && behavior.origin_request_policy_id == "216adef6-5c7f-47e4-b989-5492eafa07d3" && behavior.viewer_protocol_policy == "https-only" && contains(behavior.allowed_methods, "POST")]) == 2
    error_message = "Locust must preserve login cookies and viewer Host on existing private origin without caching."
  }
}

run "redis_changes_default_to_maintenance_window" {
  command = plan
  assert {
    condition     = !aws_elasticache_replication_group.redis.apply_immediately && aws_elasticache_replication_group.redis.snapshot_retention_limit == 0 && aws_elasticache_replication_group.redis.node_type == "cache.t4g.micro" && aws_elasticache_replication_group.redis.preferred_cache_cluster_azs == tolist(["ap-northeast-2a", "ap-northeast-2c"])
    error_message = "기본 즉시 적용은 끄고 사양·snapshot·두 AZ 선언을 유지해야 합니다. 실제 배치는 AWS에서 별도 확인합니다."
  }
}

run "redis_changes_can_be_applied_immediately" {
  command = plan
  variables { redis_apply_immediately = true }
  assert {
    condition     = aws_elasticache_replication_group.redis.apply_immediately
    error_message = "관찰 작업에서 명시한 Redis 즉시 적용 입력을 전달해야 합니다."
  }
}

run "database_connection_parameter_group" {
  command = plan
  assert {
    condition = (
      aws_db_parameter_group.main.family == "mysql8.4" &&
      aws_db_instance.main.parameter_group_name == aws_db_parameter_group.main.name &&
      one([for p in aws_db_parameter_group.main.parameter : p if p.name == "max_connections"]).value == "100" &&
      one([for p in aws_db_parameter_group.main.parameter : p if p.name == "max_connections"]).apply_method == "immediate" &&
      aws_db_instance.main.multi_az && aws_db_instance.main.deletion_protection && !aws_db_instance.main.publicly_accessible
    )
    error_message = "DB 연결 한도는 전용 MySQL 설정 그룹으로 관리하고 기존 보호 설정을 유지해야 합니다."
  }
}

run "medium_application_and_database_capacity" {
  command = plan
  assert {
    condition = (
      aws_instance.api.instance_type == "t3.medium" &&
      aws_instance.api_c.instance_type == "t3.medium" &&
      aws_db_instance.main.instance_class == "db.t4g.medium" &&
      aws_db_instance.main.multi_az &&
      !aws_db_instance.main.apply_immediately
    )
    error_message = "API 두 대와 Multi-AZ DB는 medium 사양과 기본 유지보수 창 적용을 유지해야 합니다."
  }
}
