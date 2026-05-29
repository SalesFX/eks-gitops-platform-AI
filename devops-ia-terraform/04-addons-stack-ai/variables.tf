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

variable "project" {
  description = "Configurações do projeto para composição de nomes de recursos."
  type = object({
    name        = string
    environment = string
  })
  nullable = false
}

variable "aws_lbc" {
  description = "Configurações do Helm release do AWS Load Balancer Controller."
  type = object({
    chart_version = string
  })
  nullable = false
}
