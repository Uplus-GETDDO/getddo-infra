# Independent attachment: EC2 replacement must not delete the monitoring database.
resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.existing.availability_zone
  type              = "gp3"
  size              = 30
  encrypted         = true
  tags              = { Name = "getddo-prod-monitoring-data" }
  lifecycle {
    prevent_destroy = true
  }
}
resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.monitoring.id
  # Never force-detach a mounted database volume.
  force_detach = false
}
