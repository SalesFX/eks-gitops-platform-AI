resource "helm_release" "metrics_server" {
  name             = "metrics-server"
  repository       = "https://kubernetes-sigs.github.io/metrics-server/"
  chart            = "metrics-server"
  version          = var.metrics_server.chart_version
  namespace        = "kube-system"
  create_namespace = false

  atomic          = true
  cleanup_on_fail = true
  max_history     = 3

  set = [
    # EKS requer --kubelet-insecure-tls pois os nodes usam certificados
    # auto-assinados não reconhecidos pela CA do cluster por padrão.
    # Ver ADR-0007: validar e remover se o node group usar cert gerenciado.
    {
      name  = "args[0]"
      value = "--kubelet-insecure-tls"
    },
    # Réplica única — trade-off consciente para t3.micro x2 (ADR-0007 Fase 1).
    # Gap de até 60s sem métrica em caso de restart é aceito para o MVP.
    {
      name  = "replicas"
      value = "1"
    },
    # PriorityClass system-cluster-critical previne eviction em pressão de memória.
    {
      name  = "priorityClassName"
      value = "system-cluster-critical"
    },
    # Resources calibrados para t3.micro (ADR-0007): requests mínimos,
    # limits suficientes para ~50–100 pods no cluster MVP.
    {
      name  = "resources.requests.cpu"
      value = "10m"
    },
    {
      name  = "resources.requests.memory"
      value = "20Mi"
    },
    {
      name  = "resources.limits.cpu"
      value = "100m"
    },
    {
      name  = "resources.limits.memory"
      value = "100Mi"
    },
  ]
}
