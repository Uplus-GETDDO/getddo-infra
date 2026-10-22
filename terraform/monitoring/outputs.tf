output "monitoring_instance_id" {
  value = aws_instance.monitoring.id
}
output "data_volume_id" {
  value = aws_ebs_volume.data.id
}
output "api_instance_ids" {
  value = { for server, host in data.aws_instance.api : server => host.id }
}
output "grafana_password_path" {
  value = local.grafana_password_path
}
