resource "aws_launch_template" "node" {
  name_prefix = "${var.eks.cluster_name}-node-"

  # AL2023 uses nodeadm (TOML) instead of the deprecated AL2 bootstrap.sh.
  # Prefix delegation: set maxPods=110 so VPC CNI can use the higher ENI prefix limit.
  user_data = base64encode(<<-EOT
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="==BOUNDARY=="

--==BOUNDARY==
Content-Type: application/node.eks.aws

---
apiVersion: node.eks.aws/v1alpha1
kind: NodeConfig
spec:
  kubelet:
    config:
      maxPods: 110

--==BOUNDARY==--
EOT
  )

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.eks.node_group.disk_size
      volume_type           = "gp3"
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags          = local.common_tags
  }
}
