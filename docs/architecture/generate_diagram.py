"""
Gera docs/architecture/architecture.png usando diagrams (diagrams.mingrammer.com).
Uso: python3 docs/architecture/generate_diagram.py
"""

from pathlib import Path
from diagrams import Diagram, Cluster, Edge
from diagrams.aws.compute import EKS, EC2ContainerRegistry
from diagrams.aws.network import ALB
from diagrams.aws.security import IAMRole
from diagrams.onprem.gitops import Argocd
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.vcs import Github
from diagrams.onprem.iac import Terraform
from diagrams.onprem.monitoring import Grafana, Prometheus
from diagrams.onprem.client import Users
from diagrams.k8s.compute import Deployment
from diagrams.k8s.network import Ingress

OUTPUT = str(Path(__file__).parent / "architecture")

graph_attr = {
    "bgcolor":  "white",
    "pad":      "0.8",
    "splines":  "ortho",
    "nodesep":  "0.8",
    "ranksep":  "1.0",
    "fontsize": "13",
    "fontname": "Helvetica",
    "newrank":  "true",
}

node_attr = {
    "fontsize": "10",
    "fontname": "Helvetica",
}

with Diagram(
    "devops-ia-production",
    filename=OUTPUT,
    outformat="png",
    graph_attr=graph_attr,
    node_attr=node_attr,
    show=False,
    direction="TB",
):
    # ── Terraform IaC (provisionamento) ───────────────────────────────────────
    # Declarados em ordem reversa: graphviz inverte no layout TB.
    with Cluster("Terraform IaC (provisionamento)"):
        tf4 = Terraform("04\naddons + LBC")
        tf3 = Terraform("03\nci-cd + OIDC")
        tf2 = Terraform("02\neks + ecr")
        tf1 = Terraform("01\nnetworking")
        tf0 = Terraform("00\nremote-backend")

    # ── Source of truth + CI/CD ───────────────────────────────────────────────
    repo = Github("GitHub\ncode + manifests")

    with Cluster("GitHub Actions"):
        cicd = GithubActions("ci-cd.yml\nbuild + push images")
        sec  = GithubActions("security-scans.yml\nGitleaks · Checkov · Trivy")

    # ── AWS ───────────────────────────────────────────────────────────────────
    with Cluster("AWS us-east-1"):
        iam = IAMRole("IAM Role\nOIDC / GitHub")
        ecr = EC2ContainerRegistry("Amazon ECR\nbackend + frontend")

        with Cluster("VPC Multi-AZ"):
            users = Users("Internet")
            alb   = ALB("ALB\ninternet-facing")

            with Cluster("EKS Cluster 1.32  |  t3.small x4  |  AL2023"):

                with Cluster("kube-system"):
                    lbc = EKS("AWS Load Balancer\nController")

                with Cluster("argocd"):
                    argo = Argocd("ArgoCD")

                with Cluster("default"):
                    ing = Ingress("Ingress")
                    fe  = Deployment("Frontend\n2 replicas")
                    be  = Deployment("Backend .NET\n2 replicas")

                with Cluster("monitoring"):
                    vmagent  = Prometheus("vmagent")
                    vmsingle = Prometheus("vmsingle")
                    grafana  = Grafana("Grafana")

    # ── Push -> build -> ECR ──────────────────────────────────────────────────
    repo >> Edge(color="#444444") >> cicd
    cicd >> Edge(label="OIDC", color="#d6b656") >> iam
    iam  >> Edge(color="#d6b656", constraint="false") >> ecr

    # ── GitOps: ArgoCD polls GitHub e aplica manifests ────────────────────────
    repo >> Edge(
        style="dashed", color="#9673a6",
        label="GitOps manifests", constraint="false",
    ) >> argo
    argo >> Edge(color="#9673a6") >> fe
    argo >> Edge(color="#9673a6") >> be

    # ── Image pull ────────────────────────────────────────────────────────────
    ecr >> Edge(style="dashed", color="#aaaaaa", constraint="false") >> fe
    ecr >> Edge(style="dashed", color="#aaaaaa", constraint="false") >> be

    # ── LBC cria o ALB a partir do recurso Ingress ────────────────────────────
    lbc >> Edge(
        label="creates", style="dashed", color="#d6b656", constraint="false",
    ) >> alb

    # ── Trafego externo: Internet -> ALB -> Ingress -> apps ───────────────────
    users >> Edge(color="#4488cc") >> alb
    alb   >> Edge(color="#82b366") >> ing
    ing   >> Edge(color="#82b366") >> fe
    ing   >> Edge(color="#82b366") >> be

    # ── Monitoring ────────────────────────────────────────────────────────────
    vmagent  >> Edge(color="#ae4132", constraint="false") >> vmsingle
    vmsingle >> Edge(color="#ae4132", constraint="false") >> grafana
