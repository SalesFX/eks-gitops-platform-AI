output "metrics_server_status" {
  description = "Status do Helm release do metrics-server após o deploy."
  value       = helm_release.metrics_server.status
}

output "aws_lbc_role_arn" {
  description = "ARN da IAM role IRSA criada para o AWS Load Balancer Controller."
  value       = aws_iam_role.aws_lbc.arn
}
