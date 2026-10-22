output "deployment" {
  description = "Non-secret lifecycle integration contract. UI routing is managed separately."
  value = {
    account_id              = data.aws_caller_identity.current.account_id
    region                  = "ap-northeast-2"
    vpc_id                  = data.aws_vpc.existing.id
    api_instances           = { for key, instance in aws_instance.api : key => { id = instance.id, private_ip = instance.private_ip } }
    locust_instance         = { id = aws_instance.locust.id, private_ip = aws_instance.locust.private_ip }
    target_group_arn        = aws_lb_target_group.stage.arn
    api_url                 = "http://${aws_lb.stage.dns_name}"
    redis_endpoint          = aws_elasticache_replication_group.stage.primary_endpoint_address
    db_endpoint             = aws_db_instance.stage.address
    db_identifier           = aws_db_instance.stage.identifier
    redis_cluster_id        = one(aws_elasticache_replication_group.stage.member_clusters)
    load_balancer_arn       = aws_lb.stage.arn
    locust_target_group_arn = aws_lb_target_group.locust.arn
  }
}
