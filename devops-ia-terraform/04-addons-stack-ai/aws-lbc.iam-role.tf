resource "aws_iam_openid_connect_provider" "eks" {
  url             = data.aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["9e99a48a9960b14926bb7f3b02e22da2b0ab7280"]

  tags = {
    Component = "eks-oidc-provider"
  }
}

data "aws_iam_policy_document" "aws_lbc_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.eks.arn]
    }

    actions = ["sts:AssumeRoleWithWebIdentity"]

    condition {
      test     = "StringEquals"
      variable = "${replace(data.aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")}:sub"
      values   = ["system:serviceaccount:kube-system:aws-load-balancer-controller"]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(data.aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "aws_lbc" {
  name               = "${var.project.name}-${var.project.environment}-aws-lbc"
  assume_role_policy = data.aws_iam_policy_document.aws_lbc_assume_role.json

  tags = {
    Component = "aws-load-balancer-controller"
  }
}

resource "aws_iam_policy" "aws_lbc" {
  name        = "${var.project.name}-${var.project.environment}-aws-lbc"
  description = "IAM policy for the AWS Load Balancer Controller running on EKS cluster ${var.cluster.name}."
  policy      = file("${path.module}/policies/aws-load-balancer-controller.json")

  tags = {
    Component = "aws-load-balancer-controller"
  }
}

resource "aws_iam_role_policy_attachment" "aws_lbc" {
  role       = aws_iam_role.aws_lbc.name
  policy_arn = aws_iam_policy.aws_lbc.arn
}
