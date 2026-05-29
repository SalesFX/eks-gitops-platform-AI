"""
Gera docs/architecture/architecture.png usando a biblioteca diagrams.
Requer: pip install diagrams --break-system-packages && apt install graphviz

Uso: python3 docs/architecture/generate_diagram.py
"""

from pathlib import Path
from diagrams import Diagram, Cluster, Edge
from diagrams.aws.compute import EKS, EC2, EC2ContainerRegistry
from diagrams.aws.network import VPC
from diagrams.aws.security import IAMRole
from diagrams.aws.storage import S3
from diagrams.onprem.gitops import Argocd
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.monitoring import Grafana, Prometheus
from diagrams.onprem.vcs import Github
from diagrams.onprem.iac import Terraform

OUTPUT = str(Path(__file__).parent / "architecture")

graph_attr = {
    "fontsize": "12",
    "bgcolor": "white",
    "pad": "1.0",
    "splines": "ortho",
    "nodesep": "0.8",
    "ranksep": "1.2",
}

with Diagram(
    "devops-ia-production",
    filename=OUTPUT,
    outformat="png",
    graph_attr=graph_attr,
    show=False,
    direction="LR",
):
    repo = Github("GitHub\ncode + manifests")

    with Cluster("Terraform IaC"):
        tf = [
            Terraform("01 networking\nVPC + subnets + NAT"),
            Terraform("02 eks\nEKS + ECR + addons"),
            Terraform("03 ci-cd\nOIDC + IAM Role"),
        ]

    with Cluster("GitHub Actions"):
        ci  = GithubActions("ci-cd.yml\nbuild + push images")
        sec = GithubActions("security-scans\nGitleaks + Checkov")

    with Cluster("AWS us-east-1"):
        ecr = EC2ContainerRegistry("ECR\nbackend + frontend")
        iam = IAMRole("IAM Role\nOIDC / GitHub")

        with Cluster("VPC Multi-AZ  |  EKS 1.32  |  t3.small x4"):
            argocd = Argocd("ArgoCD\n(namespace: argocd)")
            fe = EC2("Frontend  2x\n(namespace: default)")
            be = EC2("Backend .NET  2x\n(namespace: default)")
            vm = Prometheus("VictoriaMetrics\n(namespace: monitoring)")
            gf = Grafana("Grafana\n(namespace: monitoring)")

    # Terraform provisiona
    tf[0] >> Edge(color="#999999", style="dashed", label="provisions") >> ecr
    tf[1] >> Edge(color="#999999", style="dashed") >> iam

    # Dev flow
    repo >> Edge(label="push / merge") >> ci
    ci   >> Edge(label="OIDC auth")    >> iam
    iam  >> Edge(label="push images")  >> ecr
    ci   >> Edge(label="commit tags", style="dashed") >> repo

    # GitOps
    argocd >> Edge(label="poll manifests") >> repo
    argocd >> Edge(label="apply")          >> fe
    argocd >> Edge(label="apply")          >> be

    # Imagens
    fe >> Edge(label="pull") >> ecr
    be >> Edge(label="pull") >> ecr

    # Monitoring
    vm >> Edge(label="query") >> gf
