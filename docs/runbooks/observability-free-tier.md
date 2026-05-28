# Runbook: Observabilidade Free Tier

> Runbook operacional referente ao [ADR-0007 — Observabilidade Free Tier (metrics-server)](../ADR-0007-observabilidade-free-tier-metrics-server.md).
> Aplica-se ao cluster `devops-ia-production` (us-east-1, conta `654654554686`) enquanto estiver na **Fase 1** do roadmap (2 nodes `t3.micro`, sem Prometheus/Grafana).

## Visão Geral

Este runbook descreve como observar o cluster EKS em modo **free-tier-first**, usando exclusivamente:

- **`metrics-server`** (snapshot de CPU/memória de nodes e pods)
- **`kubectl top`** e **`kubectl get events`**
- **CloudWatch free tier** (até 10 alarmes EC2 nativos)

Não há dashboards visuais nem armazenamento histórico de métricas nesta fase. As verificações são **point-in-time**: o operador roda os comandos sob demanda (diariamente como rotina, ou ad-hoc durante incidentes) e compara contra thresholds documentados aqui.

Quando essa abordagem deixar de ser suficiente, promova o cluster para **Fase 2** do roadmap (ADR-0007, seção "Roadmap de Evolução").

## Pré-requisitos

Antes de usar este runbook, garanta:

- [ ] `metrics-server` instalado no namespace `kube-system` (chart oficial `kubernetes-sigs/metrics-server`).
- [ ] `kubectl` configurado para o cluster `devops-ia-production` (`aws eks update-kubeconfig --region us-east-1 --name devops-ia-production`).
- [ ] Permissão RBAC para ler métricas e eventos (verbo `get`/`list` em `metrics.k8s.io`, `events`, `pods`, `nodes`).
- [ ] Todos os Deployments do projeto com `resources.requests` e `resources.limits` definidos (regra `.claude/rules/kubernetes-manifests.md`).
- [ ] Acesso ao Console AWS (CloudWatch) para alarmes EC2 do node group.

Validação rápida:

```bash
kubectl get deployment metrics-server -n kube-system
# Esperado:
# NAME             READY   UP-TO-DATE   AVAILABLE   AGE
# metrics-server   1/1     1            1           ...

kubectl top nodes
# Se retornar erro "Metrics API not available", o metrics-server não está pronto — aguarde 30–60s após o install.
```

## Comandos de Diagnóstico

### Verificar consumo dos nodes

```bash
kubectl top nodes
```

Exemplo de output esperado em cluster `t3.micro x2`:

```
NAME                         CPU(cores)   CPU%   MEMORY(bytes)   MEMORY%
ip-10-0-1-23.ec2.internal    310m         31%    780Mi           80%
ip-10-0-2-47.ec2.internal    245m         24%    690Mi           71%
```

**Interpretação:**
- `t3.micro` tem **1 vCPU** (= 1000m) e **~950 MiB alocáveis** (1024 MiB − reservas do kubelet).
- `CPU% 31%` significa ~310m de 1000m usados.
- `MEMORY% 80%` em `t3.micro` é **ALERTA** — pressão de memória iminente, OOMKill provável.

### Verificar consumo por namespace

```bash
kubectl top pods -A --sort-by=memory
```

Exemplo:

```
NAMESPACE     NAME                                CPU(cores)   MEMORY(bytes)
argocd        argocd-server-7d8f-xxx              15m          180Mi
argocd        argocd-repo-server-abc              25m          150Mi
default       backend-deploy-7c9-yyy              40m          120Mi
default       backend-deploy-7c9-zzz              38m          118Mi
default       frontend-deploy-5d4-aaa             20m          80Mi
default       frontend-deploy-5d4-bbb             22m          82Mi
kube-system   coredns-xxx                         3m           25Mi
kube-system   metrics-server-xxx                  4m           45Mi
```

**Interpretação:**
- Soma da coluna `MEMORY(bytes)` deve ficar abaixo de **~1.4 GiB** (somando os 2 nodes).
- Pods de uma mesma app (ex.: `backend-deploy-7c9-yyy` e `backend-deploy-7c9-zzz`) devem ter consumo parecido — divergência > 50% sugere bug de memória ou carga desbalanceada.

Para agrupar por namespace, faça com `awk`:

```bash
kubectl top pods -A --no-headers | awk '{cpu[$1]+=$3; mem[$1]+=$4} END {for (ns in cpu) printf "%-15s CPU: %dm  MEM: %dMi\n", ns, cpu[ns], mem[ns]}'
```

### Identificar pods com maior consumo

Top 5 por CPU:

```bash
kubectl top pods -A --sort-by=cpu --no-headers | tail -n +1 | head -n 5
```

Top 5 por memória:

```bash
kubectl top pods -A --sort-by=memory --no-headers | tail -n +1 | head -n 5
```

Pods próximos do `limits` configurado:

```bash
kubectl top pods -A --no-headers | while read ns pod cpu mem rest; do
  limits=$(kubectl get pod -n "$ns" "$pod" -o jsonpath='{.spec.containers[0].resources.limits.memory}' 2>/dev/null)
  echo "$ns/$pod usando $mem (limit: ${limits:-<não definido>})"
done
```

### Verificar eventos de OOMKill

OOMKill é o sinal mais grave em cluster com nodes pequenos. Cheque com:

```bash
kubectl get events -A --field-selector reason=OOMKilling --sort-by=.lastTimestamp
```

Para pegar OOMKills históricos via status dos containers:

```bash
kubectl get pods -A -o json | jq -r '
  .items[]
  | select(.status.containerStatuses[]?.lastState.terminated.reason == "OOMKilled")
  | "\(.metadata.namespace)/\(.metadata.name) — OOMKilled em \(.status.containerStatuses[0].lastState.terminated.finishedAt)"
'
```

Exemplo de output (incidente):

```
default/backend-deploy-7c9-yyy — OOMKilled em 2026-05-27T11:42:13Z
```

**Ação imediata** ao detectar OOMKill: aumentar `resources.limits.memory` do Deployment OU promover cluster à Fase 2 do roadmap.

### Verificar eventos gerais do cluster

Eventos recentes em ordem cronológica (mais informativos para diagnóstico):

```bash
kubectl get events -A --sort-by=.lastTimestamp | tail -n 30
```

Eventos do tipo `Warning` apenas:

```bash
kubectl get events -A --field-selector type=Warning --sort-by=.lastTimestamp
```

Eventos comuns que indicam pressão de recurso:

| Reason | Significado | Severidade |
|---|---|---|
| `OOMKilling` | Container foi killed por consumo de memória acima do limit | **Crítico** |
| `Evicted` | Pod foi removido por pressão de memória/disco no node | **Crítico** |
| `FailedScheduling` | Sem capacidade para schedular o pod | **Alto** |
| `BackOff` | Container em CrashLoopBackOff | **Alto** |
| `Unhealthy` | Probe (readiness/liveness) falhou | **Médio** |
| `NodeNotReady` | Node ficou indisponível | **Crítico** |

### Verificar HPA (se configurado)

Se houver `HorizontalPodAutoscaler` no cluster:

```bash
kubectl get hpa -A
```

Exemplo:

```
NAMESPACE   NAME       REFERENCE              TARGETS         MINPODS   MAXPODS   REPLICAS
default     backend    Deployment/backend     45%/70%, 60%/80% 2         5         2
```

Se `TARGETS` mostrar `<unknown>/X%`, o HPA não está conseguindo ler métricas do `metrics-server` — investigue:

```bash
kubectl describe hpa <nome> -n <namespace>
kubectl logs -n kube-system deploy/metrics-server --tail=50
```

### Verificar descrição detalhada do node

Para inspecionar reservas, alocações e condições do node:

```bash
kubectl describe node <node-name> | sed -n '/Allocatable/,/Allocated resources/p'
```

Exemplo do output relevante:

```
Allocatable:
  cpu:                940m
  ephemeral-storage:  18242267924
  memory:             859456Ki        # ~839 MiB alocáveis em t3.micro
  pods:               4

Allocated resources:
  Resource           Requests      Limits
  cpu                650m (69%)    1400m (148%)
  memory             720Mi (85%)   1200Mi (143%)
```

**Interpretação:**
- `Allocated requests memory 85%` em `t3.micro` significa que o **scheduler está praticamente saturado** — novos pods provavelmente entrarão em `Pending` com `FailedScheduling`.
- `Limits > 100%` é **overcommit normal** (Kubernetes permite), mas em nodes pequenos eleva risco de OOMKill quando vários pods spike ao mesmo tempo.
- `pods: 4` em `t3.micro` é o limite imposto pelo VPC CNI (devido a ENIs/IPs disponíveis) — atenção: pode forçar `FailedScheduling` antes mesmo do CPU/memória estourarem.

## Interpretando os Resultados

### Sinais verdes (cluster saudável)

- `kubectl top nodes`: `CPU% < 60%`, `MEMORY% < 70%` em ambos os nodes.
- `kubectl top pods -A --sort-by=memory`: nenhum pod individual > 200 MiB (em `t3.micro`).
- `kubectl get events -A --field-selector type=Warning` retorna lista vazia (ou somente eventos antigos > 1h).
- Soma de `Allocated requests memory` por node < 80%.

### Sinais amarelos (atenção, monitorar)

- `MEMORY% entre 70% e 85%` em qualquer node.
- 1 evento `Unhealthy` ou `BackOff` recente, mas o pod recuperou.
- Pod consumindo > 80% do seu `limits.memory` consistentemente.
- HPA com `TARGETS` próximo de `MAXPODS`.

### Sinais vermelhos (incidente, ação imediata)

- `MEMORY% >= 85%` em qualquer node, especialmente em `t3.micro`.
- Qualquer ocorrência de `OOMKilling` ou `Evicted` nas últimas 24h.
- `Allocated requests memory > 90%` (próximo de impedir scheduling).
- `FailedScheduling` para algum pod novo.
- Múltiplos `BackOff` ou `CrashLoopBackOff` para o mesmo pod.

## Alertas Manuais — Thresholds Recomendados

Como nesta fase **não há Alertmanager nem PagerDuty automatizado**, o operador deve rodar um *checklist manual* diariamente (ou em horários definidos) e aplicar os thresholds abaixo (calibrados para `t3.micro x2`, validado via aws-mcp sobre footprint padrão dos componentes EKS):

| Métrica | Threshold warning | Threshold crítico | Comando |
|---|---|---|---|
| Memória do node | 70% | 85% | `kubectl top nodes` |
| CPU do node | 70% | 90% | `kubectl top nodes` |
| Memória individual de pod vs. limit | 75% | 90% | `kubectl top pods -A` + verificação manual |
| Eventos `Warning` na última 1h | > 5 | > 15 | `kubectl get events -A --field-selector type=Warning` |
| OOMKill nas últimas 24h | qualquer ocorrência | qualquer ocorrência | `kubectl get events -A --field-selector reason=OOMKilling` |
| Allocated memory requests por node | 75% | 90% | `kubectl describe node` |
| Pods em estado != `Running`/`Completed` | > 0 por > 5min | > 0 por > 15min | `kubectl get pods -A --field-selector status.phase!=Running,status.phase!=Succeeded` |

### CloudWatch Alarms gratuitos (até 10 incluídos no free tier)

Configurar via Console AWS, sem custo adicional:

1. **EC2 CPUUtilization > 85% por 15min** — para cada instância do node group (alarme nativo EC2, sem agente).
2. **EC2 StatusCheckFailed_Instance > 0** — failover de hardware EC2.
3. **EC2 StatusCheckFailed_System > 0** — incidente AWS.

Notificação: SNS Topic → email da equipe (sem custo dentro do free tier de SNS: 1.000 emails/mês grátis).

**NÃO configurar nesta fase**: alarmes que dependem de Container Insights ou CloudWatch agent (custo recorrente fora do free tier — validado via aws-mcp).

## Limitações desta Abordagem

Aceitar esses limites é decisão consciente da Fase 1:

1. **Sem histórico**: `kubectl top` é point-in-time (~últimos 60s). Não responde "qual foi o pico de memória ontem às 3h?".
2. **Sem alertas automatizados em tempo real**: incidente detectado só na próxima checagem manual. Janela de detecção: minutos a horas.
3. **Sem dashboards visuais**: equipes que não dominam `kubectl` ficam cegas.
4. **Sem métricas de aplicação**: endpoints `/metrics` (Prometheus exposition format) do backend e frontend **não são raspados**. Métricas de negócio (request rate, error rate, latência P95) inexistem.
5. **Sem agregação de logs**: `kubectl logs` por pod, sem search global, sem retenção configurável.
6. **Sem tracing distribuído**: sem rastreamento de requests end-to-end.
7. **HPA limitado a CPU/memória**: sem custom metrics, sem KEDA, sem scale-to-zero por evento.

## Quando Fazer Upgrade para kube-prometheus-stack

Promova o cluster para **Fase 2 do roadmap** (kube-prometheus-stack leve, ver ADR-0007 seção "Roadmap de Evolução") quando **qualquer** um dos gatilhos abaixo for atingido:

- [ ] Node group foi upgraded para `t3.medium` ou superior (≥ 2 vCPU, 4 GiB RAM por node), liberando capacidade para o stack.
- [ ] **3 ou mais incidentes operacionais por semana** detectados tardiamente (ex.: OOMKill que ficou sem ação por mais de 1h por falta de alerta).
- [ ] Primeiro **post-mortem** em que faltou histórico de métricas para diagnóstico de causa raiz.
- [ ] Stakeholder não-técnico solicitou **dashboard visual** que `kubectl top` não consegue prover.
- [ ] Necessidade de **SLI/SLO formal** (P95, error budget) — não é possível sem time-series histórico.
- [ ] Volume de pods > 15 por node ou > 30 total — a complexidade já justifica o investimento.

Quando promover, seguir o checklist da Fase 2 no ADR-0007 e abrir novo ADR (`ADR-XXXX-observabilidade-fase-2-kube-prometheus-leve.md`).

## Próximos Passos (Evolução)

Mesmo na Fase 1, há melhorias incrementais possíveis sem sair do free-tier:

1. **Garantir resources em todos os Deployments**: pré-requisito para `kubectl top` ser interpretável. Auditar com:
   ```bash
   kubectl get deploy -A -o json | jq -r '.items[] | select(.spec.template.spec.containers[]?.resources.limits == null) | "\(.metadata.namespace)/\(.metadata.name) — sem limits"'
   ```
2. **Adicionar checagem diária ao calendário da equipe** (ex.: 9h da manhã): rodar os comandos da seção "Comandos de Diagnóstico" e registrar em log de operação.
3. **Documentar runbook de resposta a OOMKill**: passo-a-passo (aumentar limits → investigar leak → considerar upgrade de node).
4. **Configurar os 3 alarmes CloudWatch EC2 gratuitos** mencionados acima.
5. **Instrumentar backend e frontend com `/metrics` desde já** (sem custo de implementação tardia): expor o endpoint mesmo sem scraper, deixando pronto para Fase 2.
6. **Versionar Grafana dashboards JSON em Git** já durante a Fase 1 (mesmo sem Grafana rodando): habilita Fase 2 com sidecar de auto-discovery.
