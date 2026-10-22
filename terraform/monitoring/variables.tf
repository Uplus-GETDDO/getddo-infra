variable "ami_id" {
  description = "Pinned Amazon Linux 2023 x86_64 image, verified against the existing API hosts"
  type        = string
  default     = "ami-0870825cefcaafcc8"
  validation {
    condition     = can(regex("^ami-[0-9a-f]{17}$", var.ami_id))
    error_message = "Supply a verified AMI ID."
  }
}
locals {
  grafana_password_path = "/getddo/monitoring/grafana/admin-password"
}

variable "cloudfront_distribution_id" {
  description = "Existing production distribution ID; no new distribution or certificate is created."
  type        = string
  validation {
    condition     = can(regex("^E[A-Z0-9]+$", var.cloudfront_distribution_id))
    error_message = "Supply the existing production CloudFront distribution ID."
  }
}
