# Runbook: Rollback de Infraestrutura Terraform

**ADR de referência:** ADR-0010 (Camada 2)
**RTO alvo:** 10–30 minutos (depende do stack e dos recursos afetados)
**RPO:** zero (Git + S3 versioning)
**Última revisão:** 2026-05-27

---

## Quando usar este runbook

- Mudança em stack Terraform quebrou conectividade (VPC, Security Group, Route Table).
- Mudança em IAM removeu permissões que aplicações ou CI/CD necessitam.
- Mudança em EKS (upgrade de versão, node group) causou instabilidade.
- `terraform apply` falhou a meio e o estado ficou inconsistente.

---

## Pré-condições

- Acesso ao repositório Git com permissão de push em `main`
- AWS CLI configurado com permissões de leitura no S3 de estado
- `terraform` instalado (>= 1.9)
- Bucket de estado: `devops-ia-production-terraform-state-<YOUR_ACCOUNT_ID>` (ADR-0002)

---

## Procedimento padrão — git revert + pipeline (RTO 10–30 min)

### Passo 1: Identificar o commit que introduziu a mudança ruim

```bash
git log --oneline devops-ia-terraform/<stack-afetada>/ | head -10
```

Stacks disponíveis:
- `01-networking-stack-ai` — VPC, subnets, NAT, route tables
- `02-eks-stack-ai` — EKS cluster, node group, ECR, IAM
- `03-ci-cd-stack-ai` — OIDC provider, IAM roles CI/CD
- `04-addons-stack-ai` — Helm releases (metrics-server)

### Passo 2: Criar revert do commit

```bash
git revert <commit-sha> --no-edit
git push origin main
```

A pipeline re-executa automaticamente `terraform plan` + `terraform apply` via CI/CD.
**Revisão humana do plan é obrigatória antes do apply em stacks de rede e EKS.**

### Passo 3: Acompanhar o apply via pipeline

Monitorar a execução no GitHub Actions. Se a pipeline não existir para o stack, executar manualmente:

```bash
cd devops-ia-terraform/<stack-afetada>
terraform init
terraform plan -var-file="envs/production.tfvars"
# Revisar o plan antes de aplicar
terraform apply -var-file="envs/production.tfvars"
```

---

## Verificar estado anterior via S3 versioning

O bucket S3 tem versioning habilitado (ADR-0002). Para inspecionar versões anteriores do state:

```bash
# Listar versões do state file do stack afetado
aws s3api list-object-versions \
  --bucket devops-ia-production-terraform-state-<YOUR_ACCOUNT_ID> \
  --prefix <key-do-stack>/terraform.tfstate \
  --query 'Versions[*].{VersionId:VersionId,LastModified:LastModified,IsLatest:IsLatest}' \
  --output table
```

Keys por stack:
- `networking/terraform.tfstate`
- `eks/terraform.tfstate`
- `ci-cd/terraform.tfstate`
- `addons/terraform.tfstate`

Para baixar uma versão específica do state:

```bash
aws s3api get-object \
  --bucket devops-ia-production-terraform-state-<YOUR_ACCOUNT_ID> \
  --key <key-do-stack>/terraform.tfstate \
  --version-id <version-id> \
  state-backup-$(date +%Y%m%d-%H%M%S).json
```

---

## Operação direta em `terraform state` (apenas emergência)

**Risco extremo.** Requer aprovação síncrona de pelo menos 1 revisor antes de executar.
Use apenas quando o `git revert + apply` não for suficiente (ex.: recurso fantasma no state,
apply parcial com estado inconsistente).

### Sempre fazer backup do state antes de qualquer operação

```bash
terraform state pull > state-backup-$(date +%Y%m%d-%H%M%S).json
# Guardar o arquivo em local seguro (não commitar em repositório público)
```

### Remover recurso fantasma (após apply parcial)

```bash
terraform state rm <resource.identifier>
# Se necessário, re-importar o recurso
terraform import <resource.identifier> <aws-resource-id>
```

### Mover/renomear recurso entre módulos

```bash
terraform state mv <source> <destination>
```

### Verificar estado atual

```bash
terraform state list
terraform state show <resource.identifier>
```

---

## Proteções do state (já configuradas — ADR-0002)

- **S3 versioning:** habilitado — permite recuperar qualquer versão anterior do state file.
- **S3 lock (use_lockfile=true):** previne apply concorrente que corromperia o state.
- **Server-side encryption:** SSE habilitado no bucket.
- **Public access block:** ativo — bucket não é acessível publicamente.

---

## Ordem de rollback quando múltiplos stacks são afetados

Respeitar a ordem reversa de dependências:

```
4. addons (04)    ← rollback primeiro
3. ci-cd (03)
2. eks (02)
1. networking (01) ← rollback por último
```

**Nunca fazer rollback do stack de networking enquanto o EKS stack não foi revertido** —
pode quebrar conectividade do cluster antes do controle plano ser restaurado.

---

## Checklist pós-rollback

- [ ] `terraform state list` retorna os recursos esperados
- [ ] `terraform plan` retorna "No changes" após o revert
- [ ] Conectividade validada: `kubectl get nodes` retorna nodes `Ready`
- [ ] Pipeline CI/CD funcionando: teste de push → build → sync ArgoCD
- [ ] Incidente registrado (data, stack, recurso, causa, tempo de recovery)
- [ ] Backup do state descartado ou movido para `docs/incident-state-snapshots/` se relevante

---

## Referências

- ADR-0010: Estratégia de Rollback e Recovery
- ADR-0002: Remote Backend S3
- Terraform state commands: https://developer.hashicorp.com/terraform/cli/commands/state
