---
name: depoveiro
description: Diagnostica e corrige problemas de saúde do cluster EKS devops-ia-production. Use esta skill sempre que o usuário perguntar se a app está no ar, se os pods subiram, se tem algo errado no cluster, ou quando houver relato de pod travado, ImagePullBackOff, CNI error, ou ArgoCD fora de sync. Também use quando o usuário chamar /depoveiro diretamente.
---

## O que esta skill faz

Verifica o estado do cluster EKS e identifica os problemas conhecidos deste projeto, dando o diagnóstico e o passo a passo de correção.

**Cluster:** `devops-ia-production` | **Namespace principal:** `default`
**Repo:** `/home/lustrabits/DevOps-Nuvem/eks-terraform-cicd-monitoring-001`

---

## Passo 1 — Listar pods em default

Use a ferramenta `mcp__awslabs_eks-mcp-server__list_k8s_resources` com `kind=Pod`, `api_version=v1`, `namespace=default`.

Conte quantas gerações de ReplicaSet existem por app. Mais de 2 gerações por app indica rolling update travado.

---

## Passo 2 — Pegar eventos dos pods problemáticos

Para cada pod que não está Running (ou que existe em excesso), use `mcp__awslabs_eks-mcp-server__get_k8s_events` e identifique o padrão de falha.

---

## Diagnóstico — padrões conhecidos

### Padrão A: `InvalidImageName`
**Sintoma:** Evento `InvalidImageName` ou `InspectFailed` com mensagem contendo `ACCOUNT_ID.dkr.ecr`.

**Causa:** O `deployment.yaml` foi sanitizado com o placeholder literal `ACCOUNT_ID` no campo `image:`. O kustomize não consegue fazer match entre a base e o bloco `images:` do `kustomization.yaml`, então usa o placeholder como está — que não é um nome de imagem válido.

**Correção:**
1. Editar `devops-ia-kubernetes/backend/deployment.yaml` e `devops-ia-kubernetes/frontend/deployment.yaml` — substituir `ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com` por `074994084847.dkr.ecr.us-east-1.amazonaws.com`
2. Verificar no `devops-ia-kubernetes/kustomization.yaml` se há entradas duplicadas com `ACCOUNT_ID` — removê-las, manter apenas as com `074994084847`
3. Commitar e fazer push → ArgoCD sincroniza automaticamente

---

### Padrão B: `ImagePullBackOff` — tag não existe no ECR
**Sintoma:** Evento `Failed to pull image ... not found` com mensagem `rpc error: code = NotFound`.

**Causa:** O CI só atualiza a tag da imagem que foi de fato construída no commit. Se apenas o frontend mudou, o backend fica com a tag anterior. Usar a mesma tag para as duas imagens garante que uma delas vai falhar.

**Como identificar a tag correta por imagem:**
```bash
git log --oneline origin/main | grep "ci: update image tags"
```
Isso mostra quais commits geraram auto-commits do CI. Cada commit `ci: update image tags to sha-XXXXXXX` foi disparado por uma build. Procurar nos commits regulares anteriores qual mudou `devops-ia-apps/backend/` vs `devops-ia-apps/frontend/` para saber qual imagem foi construída.

Também verificar o `kustomization.yaml` no histórico — a última linha de cada imagem antes de qualquer edição manual indica a última tag realmente construída.

**Correção:**
Editar `devops-ia-kubernetes/kustomization.yaml` ajustando o `newTag` de cada imagem para a última tag realmente construída. Commitar e fazer push.

---

### Padrão C: `FailedCreatePodSandBox` — CNI sem IPs
**Sintoma:** Evento `failed to assign an IP address to container` do plugin `aws-cni`.

**Causa:** O node onde o pod foi agendado esgotou os IPs secundários disponíveis nas ENIs. Ocorre quando um node concentra muitos pods (ArgoCD + kube-system + apps). O scheduler não sabe sobre esgotamento de IPs VPC-CNI, então pode continuar mandando pods para um node que já está cheio.

**Identificar o node afetado:**
- Ver em qual node o pod está (campo `Node:` no `kubectl describe pod <nome>`)
- Ou usar `mcp__awslabs_eks-mcp-server__list_k8s_resources` com `field_selector=spec.nodeName=<node>` para contar quantos pods estão nesse node

**Correção imediata (sem Terraform):**
```bash
kubectl cordon <node-saturado>
kubectl delete pod <pod-travado>
# aguardar o novo pod subir no outro node
kubectl uncordon <node-saturado>
```

O cordon impede novos agendamentos no node saturado. O delete força o Deployment a recriar o pod, que desta vez vai para o node com IPs disponíveis.

**Correção permanente:** Escalar o node group para 3+ nodes via Terraform na stack `02-eks-stack-ai`. Se o problema ocorrer em massa após scaling event, ver Padrão E.

---

### Padrão E: CNI exhaustion em massa após rolling update ou scaling event
**Sintoma:** Depois de um `terraform apply` que troca o launch template, ou depois de `aws eks update-nodegroup-config`, múltiplos pods de namespaces diferentes ficam Pending com `FailedCreatePodSandBox` — todos no mesmo node.

**Causa:** Durante o scaling/rolling update, novos nodes entram no cluster mas ainda estão inicializando o VPC CNI (ainda não pré-alocaram IPs secundários). O scheduler agenda dezenas de pods neles ao mesmo tempo — mais pods do que IPs disponíveis. Resultado: metade dos pods fica travada no mesmo node. É o Padrão C multiplicado.

**Como identificar:**
```bash
kubectl get pods -A --field-selector=status.phase=Pending
# Se múltiplos pods de namespaces diferentes estão Pending, é este padrão
```

Verificar se estão todos no mesmo node:
- Usar `mcp__awslabs_eks-mcp-server__get_k8s_events` em alguns dos pods — todos vão mostrar o mesmo node no evento `Scheduled`.

**Correção:**
1. Cordon o node saturado: `kubectl cordon <node>`
2. Deletar TODOS os pods Pending de uma vez (usar `manage_k8s_resource` com `operation=delete` para cada um em paralelo)
3. Aguardar ~20s para os pods subirem nos outros nodes
4. Uncordon: `kubectl uncordon <node>`

**Importante:** Ao deletar pods de Deployments com PDB (`minAvailable: 1`), deletar um de cada Deployment por vez se houver apenas 2 réplicas — aguardar o novo subir antes de deletar o segundo. Para pods de workloads sem réplica (operator, agent), pode deletar todos de uma vez.

**Correção permanente:** Ativar prefix delegation no VPC CNI para aumentar o número de IPs por ENI de 9 para 110 por node — elimina o problema de exhaustion em t3.small.

---

### Padrão D: Multiple ReplicaSets acumulados
**Sintoma:** Mais de 2 gerações de pods por app (ex: 3 pods de backend com hashes diferentes).

**Causa:** Rolling updates travados por falhas nos padrões A, B ou C. Cada sync do ArgoCD cria um novo ReplicaSet, os antigos ficam porque `maxUnavailable: 0` impede terminar pods sem ter novos Ready.

**Correção:** Resolver o padrão A, B ou C que está bloqueando. Quando o novo pod ficar Ready, o Deployment controller termina os velhos automaticamente.

---

## Verificar ArgoCD

Use `mcp__awslabs_eks-mcp-server__get_k8s_events` com `kind=Application`, `name=devops-ia`, `namespace=argocd` para ver o último sync e o health status.

- **Synced + Healthy:** tudo certo
- **Synced + Progressing:** sync feito, aguardando pods ficarem Ready
- **Synced + Degraded:** pods com problema (verificar padrões acima)
- **OutOfSync:** ArgoCD ainda não detectou o último commit (aguardar até 3 min ou verificar repo URL)

---

## Output esperado ao final do diagnóstico

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Diagnóstico: devops-ia-production / default
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
frontend   — ✓ 2 pods Running (sha-XXXXXXX)
backend    — ✗ 1/2 pods Running | 1 em FailedCreatePodSandBox
ArgoCD     — Synced / Progressing

Problema identificado: Padrão C — CNI IP exhaustion no node ip-10-0-12-56
Correção: cordon + delete pod + uncordon (ver instruções acima)
```
