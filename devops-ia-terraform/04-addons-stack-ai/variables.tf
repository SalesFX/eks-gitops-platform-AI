variable "cluster" {
  description = "Configurações do cluster EKS alvo para instalação dos addons."
  type = object({
    name   = string
    region = string
  })
  nullable = false
}

variable "metrics_server" {
  description = "Configurações do Helm release do metrics-server."
  type = object({
    chart_version = string
  })
  nullable = false
}
