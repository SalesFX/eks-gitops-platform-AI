# ADR-0010: Estratégia de Rollback e Recovery (Aplicação, Infra, Helm) — Free Tier sem Velero

**Status:** Approved — implementação imediata (subconjunto mínimo); Velero deferido para Fase 2
**Data:** 2026-05-27
**Autores:** [Architect Agent]
**Supersedes / Relacionado:** [[ADR-0002]] (Remote Backend S3+DynamoDB), [[ADR-0003]] (EKS), [[ADR-0005]] (Pipeline), [[ADR-0006]] (ArgoCD GitOps), [[ADR-0007 free-tier]] (metrics-server), [[ADR-0008]] (CloudWatch Logs)

## Viabilidade Free Tier

> **Veredicto:** Parcialmente viável — rollback de app (ArgoCD), infra (Terraform revert) e Helm releases são **gratuitos e viáveis hoje**. Velero é **inviável agora** no cluster `t3.micro x2` (consumo ~100–200 MiB RAM + EBS snapshots pagos). Adotamos plano mínimo de backup (export `kubectl get all -A -o yaml` + Terraform state versionado em S3) e adiamos Velero para Fase 2.
>
> Justificativa: validado via `aws-mcp` em [Velero on EKS blog](https://aws.amazon.com/blogs/containers/back-up-and-restore-your-amazon-eks-cluster-resources-using-velero/) — Velero requer EKS 1.35+ com Auto Mode na versão atual do blog (2026-05-12), implica custos de S3 + EBS snapshots, e tipicamente consome ~100–200 MiB RAM no controller pod. A boa notícia é que **o stack atual já oferece rollback robusto sem Velero**: ArgoCD versiona aplicações via Git (rollback é `git revert`), Terraform state é versionado em S3 com DynamoDB lock ([[ADR-0002]]), e Helm mantém histórico de releases nativamente.

## Contexto

Hoje o projeto não tem estratégia de rollback documentada e nenhum mecanismo de recovery formalizado. Em caso de incidente (deploy ruim, drift de infra, corrupção de state), as opções são improvisadas. Cenários que precisam ser cobertos por procedimentos claros:

1. **Deploy ruim da aplicação** (backend/frontend): pipeline produziu imagem que crasha em runtime ou regride comportamento crítico.
2. **Configuração ruim de IaC** (Terraform): mudança em VPC, EKS ou IAM quebrou conectividade ou acesso.
3. **Upgrade de Helm release** (ArgoCD, NGINX ingress, metrics-server) introduziu regressão.
4. **Perda de recurso K8s crítico** por erro humano (`kubectl delete deployment` acidental em namespace errado).
5. **Cluster completamente perdido** (apagado, região indisponível): cenário extremo de DR.

Como o cluster `devops-ia-production` não tem stateful workloads (frontend Next.js e backend .NET são stateless, sem PVCs), o cenário 5 é amplamente cobertível por re-provisionamento via Terraform + ArgoCD (GitOps = state declarativo em Git).

### Validações via MCP

- **aws-mcp** — Velero on EKS: o blog recomenda EKS 1.35+ Auto Mode (não nosso caso — estamos em 1.31, [[ADR-0003]]). Velero usa S3 para metadata + EBS snapshots para volumes (custos: S3 storage standard ~US$ 0,023/GB-mês; EBS snapshot ~US$ 0,05/GB-mês). Para um cluster sem PVs e ~50 MiB de manifests serializados, custo seria sub-US$ 1/mês — **mas o footprint de RAM do controller (~100–200 MiB) não cabe** em `t3.micro x2`.
- **aws-mcp** — [AWS Backup for Amazon EKS](https://docs.aws.amazon.com/aws-backup/latest/devguide/eks-backups.html): alternativa managed, mas custo recorrente por recovery point e por GB. Excessivo para um cluster MVP stateless.
- **aws-mcp** — `kubectl get all -A -o yaml` salvo em S3 versionado é um plano-B oficialmente aceitável para clusters stateless onde Git é a source of truth.
- **terraform-mcp** — provider `hashicorp/aws ~> 6.0` mantém `aws_s3_bucket_versioning` + `aws_dynamodb_table` como recursos nativos para state lock (já implementados em [[ADR-0002]]).

## Decisão

Estratégia de rollback em **quatro camadas independentes**, cada uma com gatilho, procedimento, RTO/RPO e plano de fallback:

---

### Camada 1 — Rollback de Aplicação (via ArgoCD)

**Source of truth:** Git (`devops-ia-kubernetes/kustomization.yaml`) — ver [[ADR-0006]].

**Procedimento padrão (RTO < 5 min):**

```bash
# 1) Identificar a revisão estável anterior
argocd app history <app-name>
# 2) Rollback declarativo via ArgoCD (NÃO via kubectl)
argocd app rollback <app-name> <revision-id>
# 3) Validar via kubectl + CloudWatch Logs (ADR-0008)
kubectl rollout status deploy/<app> -n <ns>
```

**Procedimento alternativo (quando ArgoCD UI inacessível):**

```bash
# Reverter o commit que mudou a tag da imagem em kustomization.yaml
git revert <commit-sha>
git push origin main
# ArgoCD detecta o novo commit em ~3 min (auto-sync) e reaplica
```

**Quando usar `kubectl rollout undo` em vez de ArgoCD:**
- **Apenas** em incidente crítico de produção onde ArgoCD está down e cada minuto importa.
- **Sempre** seguido de `git revert` na sequência — caso contrário ArgoCD vai re-sincronizar a versão ruim do Git em ~3 min (auto-sync ativo).
- Registrar no incident log o desvio do GitOps pattern.

**RTO:** < 5 min (via ArgoCD) | **RPO:** zero (estado declarativo em Git).

---

### Camada 2 — Rollback de Infraestrutura (Terraform)

**Source of truth:** Git (`devops-ia-terraform/**`) + state em S3 (`devops-ia-production-terraform-state`, ver [[ADR-0002]]).

**Procedimento padrão (RTO 10–30 min, depende do stack):**

```bash
# 1) Reverter o commit que introduziu a mudança ruim
git revert <commit-sha>
git push origin main
# 2) Pipeline re-executa terraform plan; revisão humana antes de apply
# 3) terraform apply -var-file="envs/production.tfvars" no stack afetado
```

**Proteção do state (já configurada em [[ADR-0002]]):**
- **S3 versioning** habilitado no bucket `devops-ia-production-terraform-state` — permite rollback do state file via `aws s3api restore-object` ou cópia da versão anterior.
- **DynamoDB lock table** `devops-ia-production-terraform-locks` — previne corrupção por apply concorrente.
- **Server-side encryption** (SSE-S3 ou SSE-KMS) habilitado.
- **Public access block** ativo.

**Quando usar `terraform state` diretamente (exceções):**
- Recurso fantasma no state após `apply` parcial. Comando: `terraform state rm <resource>` seguido de `terraform import` se necessário.
- Renomear/mover recurso entre módulos: `terraform state mv`.
- **Sempre fazer backup do state antes**: `terraform state pull > state-backup-$(date +%Y%m%d-%H%M%S).json` (commitado a `docs/incident-state-snapshots/` se for incidente, ou descartado se for refactor).
- Operação em estado é **operação de risco extremo** — exige aprovação síncrona de pelo menos 1 revisor.

**RTO:** 10–30 min | **RPO:** zero (Git + state versioning).

---

### Camada 3 — Rollback de Helm Releases (Addons)

Releases ativas no cluster (referência a ADRs anteriores):
- **ArgoCD** ([[ADR-0006]]) — release `argocd` no namespace `argocd`.
- **NGINX Ingress Controller** — release `ingress-nginx` no namespace `ingress-nginx`.
- **metrics-server** ([[ADR-0007 free-tier]]) — release `metrics-server` no namespace `kube-system`.
- **AWS for Fluent Bit** ([[ADR-0008]]) — release `aws-for-fluent-bit` no namespace `amazon-cloudwatch` (a ser criado).

**Procedimento padrão (RTO < 3 min):**

```bash
# 1) Inspecionar histórico do release
helm history <release> -n <namespace>
# 2) Rollback para revisão específica
helm rollback <release> <revision> -n <namespace>
# 3) Validar pods e probes
kubectl get pods -n <namespace>
kubectl rollout status deploy/<release> -n <namespace>
```

**Política de retenção de histórico:**
- Manter **mínimo 3 revisões** por release via flag `--history-max 3` no `helm upgrade` ou via configuração do `helm_release` Terraform (`history_count = 3`).
- 3 revisões = espaço suficiente para rollback de N-1 e N-2 sem inflar etcd.

**Riscos conhecidos:**
- Rollback de release com CRDs alterados (ex.: ArgoCD entre major versions) **NÃO restaura CRDs** automaticamente. Procedimento: re-aplicar CRDs do release anterior antes do rollback. Documentar em runbook.
- Rollback do `metrics-server` é seguro (sem state, sem PVC). Rollback do NGINX requer atenção a Services do tipo NodePort: se hostnames/ports mudaram, o ALB/NLB upstream precisa atualizar.

**RTO:** < 3 min | **RPO:** zero (releases versionadas no cluster).

---

### Camada 4 — Backup e Recovery do Cluster (Fase 1 mínima sem Velero)

Como Velero é inviável no `t3.micro x2`, adotar **plano-B minimalista**:

1. **Cron job semanal no GitHub Actions** (`.github/workflows/cluster-backup.yml`):
   ```bash
   kubectl get all,cm,secret,ingress,pdb,sa,role,rolebinding,clusterrole,clusterrolebinding,networkpolicy \
     -A -o yaml \
     --kubeconfig=$KUBECONFIG \
     > cluster-snapshot-$(date +%Y%m%d).yaml
   # Upload para S3 com versioning
   aws s3 cp cluster-snapshot-*.yaml s3://devops-ia-production-cluster-backups/snapshots/ \
     --sse aws:kms
   ```
   - Bucket dedicado `devops-ia-production-cluster-backups` (NOVO recurso a ser provisionado por Terraform).
   - Lifecycle policy: transitar para S3 Standard-IA após 30 dias; deletar após 180 dias.
   - Custo estimado: < US$ 0,10/mês (manifest serializado ~5–20 MiB × 26 semanas × US$ 0,023/GB).
2. **Secrets**: snapshot incluí `Secret` objects (criptografados at-rest em etcd via KMS conforme [[ADR-0003]]). Para audit, separar em export `secrets-snapshot-redacted.yaml` com data fields zerados antes do upload (regra: nunca commitar Secrets em S3 cleartext — usar SSE-KMS).
3. **Como restaurar** (RTO ~30–60 min para cluster stateless):
   ```bash
   # 1) Re-provisionar cluster via Terraform (revert + apply nos stacks 01,02,03)
   # 2) Re-instalar addons (ArgoCD, NGINX, metrics-server, fluent-bit) via Helm
   # 3) Bootstrap GitOps: ArgoCD aponta para devops-ia-kubernetes/kustomization.yaml
   # 4) ArgoCD sincroniza todos os apps automaticamente
   # 5) Validar via CloudWatch Logs (ADR-0008) + kubectl top (ADR-0007)
   ```
4. **Não inclui PVs/PVCs** — o cluster atual é stateless. Quando PVs forem introduzidos (futuro), Velero passa a ser necessário (Fase 2).

**RTO (cluster completo):** 30–60 min | **RPO:** ≤ 7 dias (snapshot semanal) — aceito para ambiente MVP/portfolio.

---

### Targets RTO/RPO consolidados

| Cenário | RTO | RPO | Mecanismo |
|---|---|---|---|
| App ruim (backend/frontend) | < 5 min | 0 | ArgoCD rollback / git revert |
| Infra ruim (Terraform stack) | 10–30 min | 0 | git revert + terraform apply |
| Helm release ruim (addon) | < 3 min | 0 | `helm rollback` |
| State corrompido | 15–60 min | 0 | S3 versioning |
| Pod K8s deletado por engano | < 3 min | 0 | ArgoCD self-heal (auto-sync) |
| Cluster perdido (stateless) | 30–60 min | ≤ 7 dias | Re-provision via Terraform + GitOps |
| Região AWS perdida | N/A na Fase 1 | N/A | Não coberto — Fase 3+ |

### Justificativa contra os 6 pilares do AWS Well-Architected

1. **Operational Excellence**: runbooks explícitos por cenário; comandos copy-paste; rollback testável em pre-prod antes de produção.
2. **Security**: snapshots criptografados (SSE-KMS); Secrets redacted antes do export; rollback de IaC passa pelo mesmo gate de CI ([[ADR-0009]]).
3. **Reliability**: 4 camadas independentes — falha em uma não bloqueia as outras. Estado declarativo em Git elimina drift.
4. **Performance Efficiency**: ArgoCD/Helm/Terraform já existentes — sem overhead novo. Snapshots semanais em job CI não tocam o cluster.
5. **Cost Optimization**: zero custo direto na Fase 1 (Velero adiado). Bucket de snapshots < US$ 0,10/mês.
6. **Sustainability**: sem componentes idle no cluster; snapshots compactos em S3; lifecycle policies removem dados velhos automaticamente.

## Configuração Mínima Adotada

```text
ArgoCD:
  history-max:            3 revisões por Application (config global do ArgoCD)
  sync-policy:            automated + selfHeal + prune

Helm releases (todos via Terraform helm_release):
  history_count:          3
  cleanup_on_fail:        true
  atomic:                 true

Terraform:
  S3 versioning:          ON (já em ADR-0002)
  DynamoDB lock:          ON (já em ADR-0002)
  KMS encryption:         ON

Cluster snapshot (NOVO):
  Bucket:                 devops-ia-production-cluster-backups
  Encryption:             SSE-KMS (KMS key dedicada)
  Versioning:             ON
  Lifecycle:              Standard-IA @ 30d, expire @ 180d
  Cron:                   0 03 * * 0 (domingos 03:00 UTC) via GitHub Actions
  IAM:                    IRSA não necessário (job é runner externo); IAM Role
                          GitHub OIDC com put-object scoped ao bucket
```

## Consequências

### Positivas

- Quatro caminhos de rollback documentados e testáveis sem precisar de tooling extra no cluster.
- Recovery do cluster inteiro em ~30–60 min é viável por causa do design stateless + GitOps.
- Custo direto: ~US$ 0 / mês.
- Sem footprint adicional no cluster `t3.micro` (Velero adiado).
- Runbooks evoluem com a equipe — cada incident report alimenta um runbook.

### Negativas / Trade-offs

- **Sem backup de PVs**: aceito porque o cluster é stateless hoje. **Bloqueador para introduzir qualquer stateful workload** (RDS-substituto, Loki self-hosted, etc.) — neste caso, ativar Velero primeiro (Fase 2).
- **RPO de 7 dias** no plano de cluster: longo para produção real, aceitável para portfolio/MVP.
- **Rollback de Terraform pode quebrar dependências**: ex.: revert do stack `01-networking` enquanto `03-eks` depende dele. Mitigação: ordem de apply documentada e `terraform plan` revisado por humano antes de apply em rollback.
- **Helm rollback com CRDs alterados** exige passo manual extra — documentado em runbook.
- **Sem cross-region DR**: cenário "região us-east-1 indisponível" não é coberto. Aceito como gap explícito.

## Alternativas Consideradas

| Alternativa | Motivo da rejeição |
|---|---|
| **Velero agora** | Footprint ~100–200 MiB RAM não cabe em `t3.micro x2`. Validado via aws-mcp. Adiar para Fase 2 (upgrade de nodes). |
| **AWS Backup for EKS** | Custo recorrente por recovery point + per-GB. Exigiria modo `API` ou `API_AND_CONFIG_MAP` no EKS access. Excessivo para cluster stateless MVP. |
| **etcd snapshot manual** | EKS é managed — etcd não é acessível ao cliente. Não aplicável. |
| **Backup de cada CRD individualmente** | Operação manual error-prone. Plano-B `kubectl get all -A` cobre uniformemente. |
| **Não fazer backup** | GitOps cobre 95% dos casos, mas não cobre: Secrets criados fora do Git (gerados por operator), ConfigMaps mutados via `kubectl edit`, recursos órfãos. Risco residual inaceitável. |
| **Backup contínuo (não semanal)** | Sobre-engenharia para um cluster sem dados mutáveis. Semanal + GitOps = suficiente. |

## Roadmap de Evolução

| Fase | Gatilho | O que adicionar |
|---|---|---|
| **Fase 1 (atual — t3.micro x2)** | — | 4 camadas: ArgoCD rollback / Terraform revert / Helm rollback / snapshot semanal em S3. |
| **Fase 2 (t3.medium x 2–3 nodes, primeira PVC do cluster)** | Upgrade dos nodes OU introdução de stateful workload (Loki, Postgres operator, etc.) | Instalar **Velero** via Helm com S3 backend + EBS snapshots. Schedule: backup diário 03:00 UTC, retenção 30 dias. Testar restore mensal em namespace de staging. |
| **Fase 3 (produção real)** | Compliance ou requisito de DR formal | RTO < 15 min documentado e testado quarterly. Velero schedules diferenciados por namespace (prod: 6h, staging: 24h). Snapshots cross-region (replicação S3 + EBS snapshot copy para `us-west-2`). Runbook DR validado em game-day. |
| **Fase 4 (multi-cluster ou multi-region)** | Cluster dedicado para DR ou requisito BCP | Cluster passivo em `us-west-2` com Velero restore automático em failure detectado. Considerar **AWS Backup centralizado** em conta dedicada. RTO ≤ 1h, RPO ≤ 15 min. |

## Critérios de Aceitação

- [ ] Runbook `docs/runbooks/rollback-app-argocd.md` publicado (Camada 1).
- [ ] Runbook `docs/runbooks/rollback-infra-terraform.md` publicado (Camada 2), incluindo seção "operação em `terraform state`".
- [ ] Runbook `docs/runbooks/rollback-helm-release.md` publicado (Camada 3) com lista de releases ativas e ressalva de CRDs.
- [ ] Runbook `docs/runbooks/cluster-recovery.md` publicado (Camada 4) com passo-a-passo end-to-end.
- [ ] Bucket `devops-ia-production-cluster-backups` provisionado via Terraform com SSE-KMS, versioning, lifecycle.
- [ ] Workflow `.github/workflows/cluster-backup.yml` rodando semanalmente em domingo 03:00 UTC.
- [ ] Helm releases (`argocd`, `ingress-nginx`, `metrics-server`, `aws-for-fluent-bit`) configuradas com `history_count = 3`, `cleanup_on_fail = true`, `atomic = true`.
- [ ] ArgoCD config global com `application.controller.history-max = 3`.
- [ ] **Teste de rollback em ambiente real**: degradar deliberadamente o backend (image tag inválida via PR), confirmar bloqueio via probes, executar `argocd app rollback`, documentar tempo gasto.
- [ ] **Teste de recovery do snapshot**: aplicar snapshot da semana passada em namespace `recovery-drill`, validar que recursos sobem.
- [ ] Documentado o gatilho explícito para promover Velero (Fase 2): upgrade de nodes OU introdução de PVC.

## Referências

- AWS Velero on EKS (validado via aws-mcp): https://aws.amazon.com/blogs/containers/back-up-and-restore-your-amazon-eks-cluster-resources-using-velero/
- AWS Backup for Amazon EKS (validado via aws-mcp): https://docs.aws.amazon.com/aws-backup/latest/devguide/eks-backups.html
- ArgoCD CLI rollback: https://argo-cd.readthedocs.io/en/stable/user-guide/commands/argocd_app_rollback/
- Helm history & rollback: https://helm.sh/docs/helm/helm_rollback/
- Terraform state management: https://developer.hashicorp.com/terraform/cli/commands/state
- Relacionados: [[ADR-0002]] (Remote Backend), [[ADR-0005]] (Pipeline), [[ADR-0006]] (ArgoCD), [[ADR-0008]] (Logs)
