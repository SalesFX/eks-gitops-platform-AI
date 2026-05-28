output "metrics_server_status" {
  description = "Status do Helm release do metrics-server após o deploy."
  value       = helm_release.metrics_server.status
}
