resource "helm_release" "aws_load_balancer_controller" {
  name             = "aws-load-balancer-controller"
  repository       = "https://aws.github.io/eks-charts"
  chart            = "aws-load-balancer-controller"
  version          = var.aws_lbc.chart_version
  namespace        = "kube-system"
  create_namespace = false

  atomic          = true
  cleanup_on_fail = true
  max_history     = 3

  set = [
    {
      name  = "clusterName"
      value = var.cluster.name
    },
    {
      name  = "serviceAccount.create"
      value = "true"
    },
    {
      name  = "serviceAccount.name"
      value = "aws-load-balancer-controller"
    },
    {
      name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
      value = aws_iam_role.aws_lbc.arn
    },
    {
      name  = "replicaCount"
      value = "1"
    },
    {
      name  = "resources.requests.cpu"
      value = "50m"
    },
    {
      name  = "resources.requests.memory"
      value = "64Mi"
    },
    {
      name  = "resources.limits.cpu"
      value = "200m"
    },
    {
      name  = "resources.limits.memory"
      value = "256Mi"
    },
    # IMDSv2 está habilitado nos nodes (IMDSv1 desabilitado). O controller não
    # consegue descobrir o VPC ID via metadata. Passamos explicitamente para
    # evitar o erro: "failed to fetch VPC ID from instance metadata: 401".
    {
      name  = "vpcId"
      value = "vpc-0ca452cff561bdf41"
    },
    {
      name  = "region"
      value = "us-east-1"
    },
  ]

  timeout = 480

  depends_on = [aws_iam_role_policy_attachment.aws_lbc]
}
