# Runbook: Rollback de Aplicação via ArgoCD

**ADR de referência:** ADR-0010 (Camada 1)
**RTO alvo:** < 5 minutos
**RPO:** zero (estado declarativo em Git)
**Última revisão:** 2026-05-27

---

## Quando usar este runbook

- Deploy produziu imagem que crasha em runtime (CrashLoopBackOff, OOMKilled).
- Deploy regrediu comportamento crítico detectado via `kubectl logs` ou health checks.
- Necessidade de reverter para versão anterior estável com urgência.

---

## Pré-condições

- CLI `argocd` instalado e autenticado: `argocd login <argocd-server> --username admin`
- `kubectl` configurado para o cluster `devops-ia-production`
- Acesso ao GitHub para fazer `git revert` se necessário

---

## Procedimento padrão (ArgoCD — RTO < 5 min)

### Passo 1: Pausar auto-sync antes do rollback

O auto-sync do ArgoCD re-aplicaria o estado do Git imediatamente após o rollback,
desfazendo a operação. **Pause primeiro.**

```bash
argocd app set <app-name> --sync-policy none
```

Substituir `<app-name>` por `backend` ou `frontend` conforme o app afetado.

### Passo 2: Identificar a revisão estável anterior

```bash
argocd app history <app-name>
```

Exemplo de saída:
```
ID  DATE                           REVISION
0   2026-05-27 14:00:00 +0000 UTC  main (abc1234)
1   2026-05-27 13:45:00 +0000 UTC  main (def5678)   <-- versão estável
```

Anote o `ID` da revisão estável (coluna `ID`, não o hash do Git).

### Passo 3: Executar rollback

```bash
argocd app rollback <app-name> <revision-id>
```

Exemplo:
```bash
argocd app rollback backend 1
```

### Passo 4: Acompanhar o rollout

```bash
kubectl rollout status deployment/<app-name> -n <namespace>
```

Namespaces padrão: `backend` → `backend`, `frontend` → `frontend`.

### Passo 5: Validar que a versão anterior está saudável

```bash
# Verificar pods em running
kubectl get pods -n <namespace>

# Verificar logs da versão revertida
kubectl logs -l app.kubernetes.io/name=<app-name> -n <namespace> --tail=50

# Verificar health check do serviço
kubectl exec -n <namespace> deploy/<app-name> -- curl -sf http://localhost:<port>/health
```

### Passo 6: Corrigir o código e fazer novo deploy

Após rollback bem-sucedido, abra um PR com o fix e **não re-ative auto-sync** até a
nova versão estar validada.

### Passo 7: Reativar auto-sync

```bash
argocd app set <app-name> --sync-policy automated \
  --self-heal \
  --auto-prune
```

---

## Procedimento alternativo quando ArgoCD está inacessível

Use `git revert` para que o ArgoCD detecte automaticamente via auto-sync (~3 min):

```bash
# Identificar o commit que introduziu a versão ruim
git log --oneline devops-ia-kubernetes/kustomization.yaml | head -5

# Reverter o commit
git revert <commit-sha> --no-edit

# Empurrar para main
git push origin main
```

ArgoCD detectará o novo commit em ~3 min e reaplicará o estado anterior.

---

## Fallback via kubectl (apenas emergência)

Use **apenas** quando ArgoCD estiver completamente indisponível e cada segundo importa.
**Obrigatório:** executar `git revert` na sequência — caso contrário ArgoCD re-sincroniza
a versão ruim em ~3 min.

```bash
kubectl rollout undo deployment/<app-name> -n <namespace>

# Validar imediatamente
kubectl rollout status deployment/<app-name> -n <namespace>
kubectl get pods -n <namespace>
```

Registrar desvio do GitOps pattern no incident log.

---

## Checklist pós-rollback

- [ ] Pods em estado `Running` sem restarts recentes
- [ ] Health check HTTP respondendo 200
- [ ] Logs sem erros críticos nos últimos 5 minutos
- [ ] Auto-sync reativado no ArgoCD
- [ ] Incidente registrado (data, causa, revisão antes/depois, tempo de rollback)
- [ ] PR de fix criado (não repetir o deploy ruim)
- [ ] Se foi usado `kubectl rollout undo`: `git revert` aplicado e ArgoCD sincronizado

---

## Referências

- ADR-0010: Estratégia de Rollback e Recovery
- ADR-0006: ArgoCD GitOps Deployment
- ArgoCD CLI: https://argo-cd.readthedocs.io/en/stable/user-guide/commands/argocd_app_rollback/
