# SSH와 첫 API 테스트를 허용할 현재 작업자의 공인 IPv4 한 개다.
variable "admin_ipv4_cidr" {
  type        = string
  description = "Single public IPv4 address allowed to access SSH and port 8080"

  validation {
    condition     = can(cidrnetmask(var.admin_ipv4_cidr)) && endswith(var.admin_ipv4_cidr, "/32")
    error_message = "관리자 접근 범위는 IPv4 주소 한 개(/32)로 지정해야 합니다."
  }
}

# dev.py prepare가 AWS 공식 SSM 경로에서 조회한 AMI를 로컬 변수 파일에 고정한다.
variable "ami_id" {
  type        = string
  description = "Pinned Amazon Linux 2023 x86_64 AMI in ap-northeast-2"

  validation {
    condition     = can(regex("^ami-[0-9a-f]{17}$", var.ami_id))
    error_message = "서울 리전에서 확인한 AMI ID를 입력해야 합니다."
  }
}

variable "ssh_public_key" {
  type        = string
  description = "Public SSH key; the private key stays outside the repository"

  validation {
    condition     = startswith(var.ssh_public_key, "ssh-ed25519 ")
    error_message = "ED25519 공개 키를 입력해야 합니다."
  }
}

# ephemeral + password_wo로 비밀번호를 plan·State에 저장하지 않는다.
variable "db_master_password" {
  type        = string
  sensitive   = true
  ephemeral   = true
  description = "RDS bootstrap administrator password, supplied only during execution"

  validation {
    condition     = can(regex("^[A-Za-z0-9]{32,64}$", var.db_master_password))
    error_message = "로컬 준비 스크립트가 생성한 32~64자 영문·숫자 비밀번호를 사용합니다."
  }
}

variable "db_apply_immediately" {
  type        = bool
  default     = false
  description = "Apply RDS changes immediately only during an approved observed operation"
}

variable "db_master_password_version" {
  type        = number
  default     = 1
  description = "Increment only when intentionally rotating the write-only password"
}

# Redis 생성·회전 시 실행 환경으로만 전달한다. tfvars·Git·채팅에 비밀번호를 기록하지 않는다.
# 기존 DB와 동일하게 ephemeral 입력을 write-only 속성에 연결해 plan·State 저장을 막는다.
variable "redis_auth_token" {
  type        = string
  sensitive   = true
  ephemeral   = true
  description = "Redis AUTH password supplied only during execution"

  # AWS AUTH 범위 안에서 복사·env 전달이 간단한 32~64자 영문·숫자로 제한한다.
  validation {
    condition     = can(regex("^[A-Za-z0-9]{32,64}$", var.redis_auth_token))
    error_message = "Redis 인증 비밀번호는 32~64자 영문·숫자로 입력해야 합니다."
  }
}

# write-only 비밀번호의 변경 감지용 숫자다. 내용만 바꾸면 회전되지 않으므로 함께 올린다.
variable "redis_auth_token_version" {
  type        = number
  default     = 1
  description = "Increment only when intentionally rotating the write-only Redis password"

  validation {
    condition     = var.redis_auth_token_version >= 1 && floor(var.redis_auth_token_version) == var.redis_auth_token_version
    error_message = "Redis 비밀번호 버전은 1 이상의 정수여야 합니다."
  }
}

# 같은 AWS 계정에 GitHub OIDC 공급자가 이미 있으면 ARN을 지정해 재사용한다.
variable "github_oidc_provider_arn" {
  type        = string
  default     = null
  description = "Existing GitHub OIDC provider ARN; null creates a provider"

  validation {
    condition = var.github_oidc_provider_arn == null ? true : can(regex(
      "^arn:aws:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$",
      var.github_oidc_provider_arn
    ))
    error_message = "GitHub OIDC 공급자의 ARN만 지정할 수 있습니다."
  }
}

# C keeps its previously verified pinned AMI independently of future A AMI changes.
variable "api_c_ami_id" {
  type        = string
  default     = "ami-0870825cefcaafcc8"
  description = "Pinned Amazon Linux 2023 x86_64 AMI for C preparation"
  validation {
    condition     = can(regex("^ami-[0-9a-f]{17}$", var.api_c_ami_id))
    error_message = "C AMI must be a verified image ID."
  }
}

# Enable only after first deployment, direct health checks and worker-safety approval.
variable "api_c_register_in_target_group" {
  type        = bool
  default     = false
  description = "Register C in the shared API target group after readiness gates pass"
}

variable "waf_mode" {
  description = "API rate-limit action: block or count"
  type        = string
  default     = "block"

  validation {
    condition     = contains(["block", "count"], var.waf_mode)
    error_message = "WAF mode must be block or count."
  }
}

variable "waf_log_mode" {
  description = "Store blocked requests, or temporarily store all evaluated requests"
  type        = string
  default     = "blocked"

  validation {
    condition     = contains(["blocked", "all"], var.waf_log_mode)
    error_message = "WAF log mode must be blocked or all."
  }

  validation {
    condition     = var.waf_mode != "count" || var.waf_log_mode == "all"
    error_message = "Count mode requires all-request logging to observe nonterminating matches."
  }
}

variable "waf_probe" {
  description = "Temporary fixed-response probe identified by UUID and an unused listener priority"
  type = object({
    run_id   = string
    priority = number
  })
  default = null

  validation {
    condition = var.waf_probe == null ? true : (
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.waf_probe.run_id)) &&
      var.waf_probe.priority >= 10 && var.waf_probe.priority <= 19 &&
      floor(var.waf_probe.priority) == var.waf_probe.priority
    )
    error_message = "The probe requires a UUID run ID and an integer priority from 10 through 19."
  }
}
