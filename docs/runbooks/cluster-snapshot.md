# Runbook: Snapshot Manual do Cluster

**ADR de referência:** ADR-0010 (Camada 4)
**RTO (cluster completo):** 30–60 minutos
**RPO:** <= 7 dias (snapshot semanal automatizado)
**Última revisão:** 2026-05-27

---

## Quando usar este runbook

- Antes de qualquer upgrade significativo (EKS version upgrade, upgrade de addon major).
- Antes de mudanças estruturais em Terraform (nova VPC, refactor de IAM).
- Após incidente, para documentar o estado atual do cluster.
- Para execução manual ad-hoc além do cron semanal automatizado.

---

## Pré-condições

- `kubectl` configurado para o cluster `devops-ia-production`
- AWS CLI configurado com permissões de write no bucket de backups:
  - Bucket: `devops-ia-production-cluster-backups` (a ser provisionado via Terraform)
  - Permissão necessária: `s3:PutObject` no path `s3://devops-ia-production-cluster-backups/snapshots/`
- Espaço em disco local: ~50 MiB para o arquivo YAML serializado

---

## Script de backup (execução manual)

```bash
#!/bin/bash
set -euo pipefail

BUCKET="devops-ia-production-cluster-backups"
DATE=$(date +%Y%m%d-%H%M%S)
SNAPSHOT_FILE="cluster-snapshot-${DATE}.yaml"
SECRETS_REDACTED_FILE="cluster-snapshot-${DATE}-secrets-redacted.yaml"

echo "[INFO] Iniciando snapshot do cluster em ${DATE}"

# Exportar todos os recursos relevantes (excluindo Secrets — exportados separadamente)
kubectl get \
  all,configmap,ingress,pdb,serviceaccount,role,rolebinding,clusterrole,clusterrolebinding,networkpolicy \
  --all-namespaces \
  -o yaml \
  > "${SNAPSHOT_FILE}"

echo "[INFO] Snapshot criado: ${SNAPSHOT_FILE} ($(du -sh ${SNAPSHOT_FILE} | cut -f1))"

# Exportar Secrets com data fields zerados (sem conteúdo sensível)
kubectl get secret --all-namespaces -o yaml | \
  python3 -c "
import sys, yaml
docs = list(yaml.safe_load_all(sys.stdin))
for doc in docs:
    if doc and doc.get('data'):
        doc['data'] = {k: '<REDACTED>' for k in doc['data']}
    if doc and doc.get('stringData'):
        doc['stringData'] = {k: '<REDACTED>' for k in doc['stringData']}
print(yaml.dump_all(docs, default_flow_style=False))
" > "${SECRETS_REDACTED_FILE}"

echo "[INFO] Secrets redacted criados: ${SECRETS_REDACTED_FILE}"

# Upload para S3 com SSE-KMS
echo "[INFO] Fazendo upload para s3://${BUCKET}/snapshots/"

aws s3 cp "${SNAPSHOT_FILE}" \
  "s3://${BUCKET}/snapshots/${SNAPSHOT_FILE}" \
  --sse aws:kms

aws s3 cp "${SECRETS_REDACTED_FILE}" \
  "s3://${BUCKET}/snapshots/${SECRETS_REDACTED_FILE}" \
  --sse aws:kms

echo "[INFO] Upload concluido."
echo "[INFO] Arquivos locais: ${SNAPSHOT_FILE}, ${SECRETS_REDACTED_FILE}"
echo "[WARN] Deletar arquivos locais apos validar o upload:"
echo "  rm ${SNAPSHOT_FILE} ${SECRETS_REDACTED_FILE}"
```

---

## Frequência recomendada

| Evento | Quando executar |
|--------|-----------------|
| Automático (cron) | Domingos 03:00 UTC via `.github/workflows/cluster-backup.yml` |
| Antes de upgrades | Imediatamente antes de qualquer upgrade de EKS, addon major |
| Antes de mudanças de infra | Antes de `terraform apply` em stacks `01-networking` ou `02-eks` |
| Após incidente | Para documentar o estado pós-incidente |

---

## Limitações importantes

- **Não inclui PersistentVolumes/PVCs:** o cluster atual é stateless. Se PVCs forem
  introduzidos no futuro, ativar Velero (gatilho definido em ADR-0010 Fase 2).
- **Não inclui estado da aplicação:** banco de dados, caches, filas são externos
  ao cluster; snapshot do K8s não captura dados de aplicação.
- **Secrets redacted:** o arquivo de secrets exportado tem os valores zerados por segurança.
  Para restaurar secrets, usar o repositório Git (Sealed Secrets ou External Secrets —
  quando implementados) ou inserir manualmente valores do Secrets Manager.
- **Snapshot é point-in-time:** recursos criados após o snapshot não são capturados.

---

## Como restaurar a partir de um snapshot

Usar este procedimento apenas em caso de perda parcial de recursos (ex.: `kubectl delete`
acidental). Para perda total do cluster, seguir o procedimento de cluster recovery abaixo.

```bash
# 1. Baixar o snapshot do S3
aws s3 cp \
  "s3://devops-ia-production-cluster-backups/snapshots/cluster-snapshot-<DATE>.yaml" \
  ./cluster-snapshot-restore.yaml

# 2. Inspecionar os recursos antes de aplicar
kubectl apply -f cluster-snapshot-restore.yaml --dry-run=client

# 3. Aplicar apenas os namespaces/recursos específicos necessários
# NÃO aplicar o snapshot inteiro em cluster em execução — pode causar conflitos.
# Filtrar o recurso específico e aplicar:
kubectl apply -f cluster-snapshot-restore.yaml
```

---

## Procedimento de recovery total do cluster (cluster stateless)

Quando o cluster está completamente perdido ou inacessível:

### Passo 1: Re-provisionar via Terraform (RTO ~20–30 min)

```bash
# Ordem obrigatória — respeitar dependências
cd devops-ia-terraform

# Stack 1: networking
cd 01-networking-stack-ai
terraform init && terraform apply -var-file="envs/production.tfvars"

# Stack 2: EKS
cd ../02-eks-stack-ai
terraform init && terraform apply -var-file="envs/production.tfvars"

# Stack 3: CI/CD (OIDC, IAM roles)
cd ../03-ci-cd-stack-ai
terraform init && terraform apply -var-file="envs/production.tfvars"
```

### Passo 2: Re-instalar addons via Helm (~5–10 min)

```bash
# Stack 4: addons (metrics-server, e demais releases gerenciadas)
cd ../04-addons-stack-ai
terraform init && terraform apply -var-file="envs/production.tfvars"

# ArgoCD e ingress-nginx são gerenciados pelo stack 03-ci-cd — já instalados no Passo 1
```

### Passo 3: Bootstrap ArgoCD GitOps (~5 min)

```bash
# Verificar que ArgoCD está rodando
kubectl get pods -n argocd

# ArgoCD auto-sync já deve detectar o repositório configurado em ADR-0006
# e sincronizar os apps. Verificar:
argocd app list

# Se necessário, forçar sync manualmente:
argocd app sync backend
argocd app sync frontend
```

### Passo 4: Validar cluster completo (~5 min)

```bash
# Nodes em Ready
kubectl get nodes

# Pods de aplicação em Running
kubectl get pods -A

# Métricas disponíveis
kubectl top nodes

# Endpoints de aplicação respondem
# Substituir pelo endpoint real do ALB/Ingress
curl -sf https://<app-endpoint>/health
```

---

## RTO/RPO targets (conforme ADR-0010)

| Cenário | RTO | RPO |
|---------|-----|-----|
| Recurso K8s deletado por engano | < 3 min | 0 (ArgoCD self-heal) |
| Cluster completo perdido (stateless) | 30–60 min | <= 7 dias |

---

## Referências

- ADR-0010: Estratégia de Rollback e Recovery
- ADR-0003: EKS Cluster
- ADR-0006: ArgoCD GitOps
