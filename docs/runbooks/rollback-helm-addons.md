# Runbook: Rollback de Helm Releases (Addons)

**ADR de referência:** ADR-0010 (Camada 3)
**RTO alvo:** < 3 minutos
**RPO:** zero (histórico de revisões mantido no cluster)
**Última revisão:** 2026-05-27

---

## Quando usar este runbook

- Upgrade de addon (metrics-server, ingress-nginx, argocd) causou regressão.
- Pods do addon em CrashLoopBackOff após upgrade.
- Funcionalidade do addon degradada após mudança de versão ou values.

---

## Pré-condições

- `helm` CLI instalado (>= 3.x)
- `kubectl` configurado para o cluster `devops-ia-production`
- Acesso de leitura/escrita ao namespace do release

---

## Releases ativas no cluster

| Release | Namespace | Gerenciado por |
|---------|-----------|---------------|
| `metrics-server` | `kube-system` | Terraform (`04-addons-stack-ai`) |
| `argocd` | `argocd` | Terraform (`03-ci-cd-stack-ai`) |
| `ingress-nginx` | `ingress-nginx` | Terraform (`03-ci-cd-stack-ai`) |

---

## Procedimento padrão

### Passo 1: Inspecionar o histórico do release

```bash
helm history <release-name> -n <namespace>
```

Exemplos:
```bash
helm history metrics-server -n kube-system
helm history argocd -n argocd
helm history ingress-nginx -n ingress-nginx
```

Saída esperada:
```
REVISION  UPDATED                   STATUS     CHART                  APP VERSION  DESCRIPTION
1         Mon May 27 12:00:00 2026  superseded metrics-server-3.12.1  0.7.1        Install complete
2         Mon May 27 14:00:00 2026  deployed   metrics-server-3.12.2  0.7.2        Upgrade complete
```

### Passo 2: Executar rollback para a revisão estável

```bash
helm rollback <release-name> <revision> -n <namespace>
```

Exemplos:
```bash
# Rollback do metrics-server para revisão 1
helm rollback metrics-server 1 -n kube-system

# Rollback do ingress-nginx para revisão anterior
helm rollback ingress-nginx 1 -n ingress-nginx
```

### Passo 3: Verificar status após rollback

```bash
# Status do release
helm status <release-name> -n <namespace>

# Status dos pods
kubectl get pods -n <namespace>

# Rollout status do deployment (se aplicável)
kubectl rollout status deployment/<release-name> -n <namespace>
```

---

## Validações específicas por release

### metrics-server

```bash
# Verificar que Metrics API está respondendo
kubectl top nodes
kubectl top pods -A

# Se retornar erro "metrics not available yet", aguardar 60s e tentar novamente
```

### argocd

```bash
# Verificar que ArgoCD está sincronizando apps
argocd app list
kubectl get pods -n argocd

# Verificar CRDs após rollback (ver seção abaixo sobre CRDs)
kubectl get crd | grep argoproj
```

### ingress-nginx

```bash
# Verificar que o controller está rodando
kubectl get pods -n ingress-nginx

# Verificar que Ingress rules ainda estão ativas
kubectl get ingress -A

# ATENÇÃO: se o Service NodePort mudou de porta entre revisões,
# verificar o ALB/NLB upstream e atualizar target group se necessário.
kubectl get svc -n ingress-nginx
```

---

## Ressalva: rollback com CRDs alterados

Rollback de releases que alteraram CRDs (principalmente ArgoCD entre versões major) **NÃO
restaura CRDs automaticamente**. O `helm rollback` reverte apenas os recursos do chart,
não os CRDs instalados na revisão anterior.

**Procedimento quando CRDs foram alterados:**

1. Verificar se há CRDs incompatíveis:
   ```bash
   kubectl get crd -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.annotations.helm\.sh/resource-policy}{"\n"}{end}' | grep argoproj
   ```

2. Se necessário, re-aplicar os CRDs da versão anterior manualmente:
   ```bash
   # Obter os CRDs da versão anterior do chart
   helm show crds <repo>/<chart> --version <versao-anterior> | kubectl apply -f -
   ```

3. Após restaurar CRDs, executar o rollback normal.

---

## Política de histórico de revisões

Todos os releases são configurados com `max_history = 3` (via Terraform `helm_release`).
Isso significa que no máximo as últimas 3 revisões ficam disponíveis para rollback.
Para releases com mais de 3 upgrades seguidos, o rollback além de N-3 requer re-install do chart.

---

## Checklist pós-rollback

- [ ] `helm status <release>` retorna `STATUS: deployed`
- [ ] Pods do addon em `Running` sem restarts recentes
- [ ] Funcionalidade validada (ver seção de validações específicas acima)
- [ ] Se ingress-nginx: confirmar Ingress rules ativas e tráfego fluindo
- [ ] Se metrics-server: `kubectl top nodes` retorna dados
- [ ] Se argocd: apps sincronizando normalmente
- [ ] Atualizar `production.tfvars` da stack correspondente com a versão estável (evitar re-upgrade acidental)
- [ ] Incidente registrado (data, release, revisão antes/depois, causa, tempo de recovery)

---

## Referências

- ADR-0010: Estratégia de Rollback e Recovery
- ADR-0007: metrics-server (observabilidade free-tier)
- ADR-0006: ArgoCD GitOps
- Helm rollback: https://helm.sh/docs/helm/helm_rollback/
- Helm history: https://helm.sh/docs/helm/helm_history/
