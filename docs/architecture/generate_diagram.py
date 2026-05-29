"""
Gera docs/architecture/architecture.png com matplotlib (posicionamento manual).
Uso: python3 docs/architecture/generate_diagram.py
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
from pathlib import Path

OUT = Path(__file__).parent / "architecture.png"

FIG_W, FIG_H = 22, 13
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=120)
ax.set_xlim(0, FIG_W)
ax.set_ylim(0, FIG_H)
ax.axis("off")
fig.patch.set_facecolor("white")


# ─── helpers ──────────────────────────────────────────────────────────────────

def band(x, y, w, h, label, bg, border, fontsize=8.5):
    r = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.12",
                        facecolor=bg, edgecolor=border, linewidth=1.8, zorder=1)
    ax.add_patch(r)
    ax.text(x + 0.22, y + h - 0.22, label, ha="left", va="top",
            fontsize=fontsize, fontweight="bold", color=border, zorder=2)


def box(x, y, w, h, label, bg, border, fontsize=8):
    r = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08",
                        facecolor=bg, edgecolor=border, linewidth=1.4, zorder=3)
    ax.add_patch(r)
    ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
            fontsize=fontsize, color="#222222", multialignment="center", zorder=4)


def arrow(x1, y1, x2, y2, label="", color="#666666", dashed=False):
    ls = "--" if dashed else "-"
    ax.annotate(
        "", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(arrowstyle="-|>", color=color, lw=1.3,
                        linestyle=ls, mutation_scale=12),
        zorder=5,
    )
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx, my + 0.12, label, ha="center", va="bottom",
                fontsize=7.5, color=color, zorder=6,
                bbox=dict(facecolor="white", edgecolor="none", pad=1))


# ─── title ────────────────────────────────────────────────────────────────────

ax.text(FIG_W / 2, FIG_H - 0.35, "devops-ia-production: Arquitetura Completa",
        ha="center", va="top", fontsize=13, fontweight="bold", color="#222222")

# ─── BAND A: Terraform IaC ────────────────────────────────────────────────────
# x=0.4-8.6  y=9.8-12.4

band(0.4, 9.8, 8.2, 2.4, "Terraform IaC  (provisiona a infraestrutura)",
     "#f9f2ff", "#9673a6")

tf_boxes = [
    ("00-remote-backend\nS3 state + DynamoDB", 0.6),
    ("01-networking\nVPC + subnets + NAT", 2.6),
    ("02-eks\nEKS + ECR + addons",  4.6),
    ("03-ci-cd\nOIDC + IAM Role",   6.0),
    ("04-addons\nMetrics + LBC",    7.4),
]
for label, x in tf_boxes:
    box(x, 10.15, 1.25, 1.8, label, "#ecdff5", "#9673a6")

# arrows between tf boxes (horizontal chain)
for i in range(len(tf_boxes) - 1):
    arrow(tf_boxes[i][1] + 1.25, 11.05, tf_boxes[i+1][1], 11.05, color="#9673a6")


# ─── BAND B: CI/CD ────────────────────────────────────────────────────────────
# x=0.4-13.6  y=7.2-9.5

band(0.4, 7.2, 13.2, 2.3, "CI/CD", "#e8f0fe", "#6c8ebf")

# GitHub
box(0.7, 7.6, 2.0, 1.5, "GitHub\ncode + manifests", "#f5f5f5", "#555555")

# GitHub Actions sub-band
band(3.1, 7.4, 6.0, 2.0, "GitHub Actions", "#d0e4fc", "#6c8ebf", fontsize=7.5)
box(3.3, 7.65, 2.6, 1.55, "ci-cd.yml\nbuild + push images\ncommit tags kustomize", "#d0e4fc", "#6c8ebf")
box(6.1, 7.65, 2.6, 1.55, "security-scans.yml\nGitleaks · Checkov\nSemgrep · Trivy", "#d0e4fc", "#6c8ebf")

# IAM + ECR
box(9.5, 8.15, 1.8, 1.1, "IAM Role\nOIDC / GitHub", "#ffe6cc", "#d6b656")
box(11.5, 8.15, 1.7, 1.1, "Amazon ECR\nbackend/frontend", "#ffe6cc", "#d6b656")

# CI/CD arrows
arrow(2.7,  8.35, 3.3,  8.35, color="#555555")               # github → ci-cd
arrow(4.6,  8.35, 4.6,  7.2,  color="#555555", dashed=True)  # ci-cd → commit tags (goes down, off-band)
arrow(5.9,  8.35, 9.5,  8.70, "OIDC", color="#6c8ebf")       # ci-cd → IAM
arrow(11.3, 8.70, 11.5, 8.70, color="#d6b656")               # IAM → ECR


# ─── BAND C: AWS / VPC / EKS ──────────────────────────────────────────────────
# x=0.4-21.5  y=0.4-6.9

band(0.4, 0.4, 21.1, 6.5, "AWS us-east-1  |  VPC Multi-AZ", "#fff8e6", "#d6b656")

# EKS cluster sub-band
band(0.7, 0.65, 20.5, 5.7, "EKS Cluster 1.32  |  t3.small x4  |  AL2023", "#e8eeff", "#6c8ebf")

# Internet + ALB (public subnet, left of EKS)
box(0.9, 3.8, 2.0, 1.0, "Internet\nUsuarios", "#daeeff", "#4488cc")
box(0.9, 2.4, 2.0, 1.1, "ALB\ninternet-facing\n/ → fe  /backend → be", "#fff2cc", "#d6b656")

# kube-system namespace
band(3.3, 4.5, 3.2, 1.6, "ns: kube-system", "#fff8e8", "#d6b656", fontsize=7.5)
box(3.5, 4.75, 2.7, 1.1, "AWS Load Balancer\nController (IRSA)", "#ffe6cc", "#d6b656")

# argocd namespace
band(3.3, 2.5, 3.2, 1.8, "ns: argocd", "#f5eeff", "#9673a6", fontsize=7.5)
box(3.5, 2.75, 2.7, 1.2, "ArgoCD\nGitOps controller", "#e8d5f5", "#9673a6")

# default namespace
band(6.9, 2.5, 4.2, 3.6, "ns: default", "#efffef", "#82b366", fontsize=7.5)
box(7.1, 4.5, 3.6, 1.3, "Frontend\nNext.js  (2 replicas)", "#d5e8d4", "#82b366")
box(7.1, 2.75, 3.6, 1.3, "Backend .NET\nAPI  (2 replicas)", "#d5e8d4", "#82b366")

# monitoring namespace
band(11.5, 2.5, 9.3, 3.6, "ns: monitoring  (VictoriaMetrics k8s stack)", "#fff0f0", "#ae4132", fontsize=7.5)
box(11.7, 4.5, 2.5, 1.3, "vmagent\nscraper",          "#ffe6e6", "#ae4132")
box(14.5, 4.5, 2.8, 1.3, "vmsingle\nVictoriaMetrics", "#ffe6e6", "#ae4132")
box(17.5, 4.5, 2.8, 1.3, "Grafana\ndashboards",       "#ffe6e6", "#ae4132")
box(11.7, 2.75, 2.5, 1.3, "kube-state\nmetrics",      "#ffe6e6", "#ae4132")
box(14.5, 2.75, 2.8, 1.3, "node-exporter\n(DaemonSet)","#ffe6e6", "#ae4132")


# ─── arrows BAND C ────────────────────────────────────────────────────────────

# Internet → ALB
arrow(1.9, 3.8, 1.9, 3.5, color="#4488cc")
# ALB → frontend
arrow(2.9, 2.95, 7.1, 5.15, color="#82b366")
# ALB → backend
arrow(2.9, 2.75, 7.1, 3.40, color="#82b366")
# LBC manages ALB
arrow(3.5, 4.75, 2.9, 3.5, "manages", color="#d6b656", dashed=True)

# ArgoCD polls GitHub (goes up into Band B)
arrow(4.85, 4.3, 4.85, 7.2, "poll", color="#9673a6", dashed=True)
# ArgoCD → frontend
arrow(6.5, 3.35, 7.1, 5.15, color="#9673a6")
# ArgoCD → backend
arrow(6.5, 3.15, 7.1, 3.40, color="#9673a6")

# ECR → frontend (image pull)
arrow(12.35, 8.15, 8.9, 5.8, "pull", color="#aaaaaa", dashed=True)
# ECR → backend
arrow(12.35, 8.15, 8.9, 3.4, "pull", color="#aaaaaa", dashed=True)

# Monitoring
arrow(14.2, 5.15, 14.2, 5.8,  color="#ae4132")  # vmagent → vmsingle
arrow(14.2, 3.4,  14.2, 4.5,  color="#ae4132")  # ksm → vmagent
arrow(16.0, 3.4,  16.0, 4.5,  color="#ae4132")  # node-exp → vmsingle
arrow(17.3, 5.15, 17.5, 5.15, color="#ae4132")  # vmsingle → grafana

# Terraform provisions (dashed, purple, into Band C)
arrow(7.22,  9.8, 11.5, 8.70, color="#9673a6", dashed=True)  # tf2 → ECR
arrow(7.85,  9.8, 4.85, 6.35, color="#9673a6", dashed=True)  # tf4 → LBC


# ─── legend ───────────────────────────────────────────────────────────────────

legend_items = [
    mpatches.Patch(facecolor="#ecdff5", edgecolor="#9673a6", label="Terraform IaC"),
    mpatches.Patch(facecolor="#d0e4fc", edgecolor="#6c8ebf", label="GitHub Actions / CI"),
    mpatches.Patch(facecolor="#ffe6cc", edgecolor="#d6b656", label="AWS (IAM, ECR, ALB, LBC)"),
    mpatches.Patch(facecolor="#d5e8d4", edgecolor="#82b366", label="Aplicacao (ns: default)"),
    mpatches.Patch(facecolor="#e8d5f5", edgecolor="#9673a6", label="ArgoCD (ns: argocd)"),
    mpatches.Patch(facecolor="#ffe6e6", edgecolor="#ae4132", label="Monitoring (ns: monitoring)"),
]
ax.legend(handles=legend_items, loc="lower right", fontsize=7.5,
          framealpha=0.9, edgecolor="#cccccc", ncol=2)

plt.tight_layout(pad=0.3)
plt.savefig(OUT, dpi=120, bbox_inches="tight", facecolor="white")
print(f"Salvo: {OUT}")
