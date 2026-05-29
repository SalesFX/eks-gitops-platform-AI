---
name: PlantonistaOps
description: |
  Plantão operacional (on-call runbook) para a plataforma devops-ia EKS + Terraform + ArgoCD.
  Diagnostica e resolve incidentes conhecidos desta infraestrutura: Terraform provider sem permissão
  de execução, S3 state lock travado, state vazio após apply interrompido, node group AL2 deprecated
  (AMI inexistente), ASG preso no destroy, backend não inicializado, kubectl sem credenciais após
  recreate do cluster, access entry EKS sumida, ArgoCD pod travado em ContainerCreating por falha
  do CNI, dex-server crash com "server.secretkey is missing", e pressão de memória em t3.micro.
  Use esta skill IMEDIATAMENTE sempre que o usuário descrever qualquer problema operacional com
  EKS, Terraform, ArgoCD ou Kubernetes neste projeto — não tente debugar do zero, consulte este
  runbook primeiro. Palavras-chave: travado, stuck, erro, falhou, node group, apply, destroy,
  lock, state, CNI, ArgoCD, dex, kubectl, access denied, permission denied, creating, NodeCreationFailure.
---

# PlantonistaOps — Runbook de Plantão

## Contexto do Ambiente

| Recurso | Valor |
|---|---|
| Cluster EKS | `devops-ia-production` |
| Região | `us-east-1` |
| Conta AWS | `074994084847` |
| Bucket Terraform State | `devops-ia-production-terraform-state-074994084847` |
| IAM Admin User | `arn:aws:iam::074994084847:user/adm-user` |
| Raiz das Stacks | `/home/lustrabits/DevOps-Nuvem/eks-terraform-cicd-monitoring-001/devops-ia-terraform/` |
| Tipo de Node | `t3.small` (2 GiB RAM), AL2023 |
| AMI Type | `AL2023_x86_64_STANDARD` |

## Como Usar Este Runbook

1. Identifique o sintoma relatado pelo usuário
2. Encontre o incidente correspondente abaixo
3. Execute o **Diagnóstico** para confirmar
4. Aplique o **Fix**
5. Execute a **Verificação** para confirmar resolução

Se o sintoma não bater com nenhum incidente, leia `references/diagnostico-geral.md`.

---

## Incidente 1 — Provider Terraform sem Permissão de Execução

**Sintoma:** `fork/exec .terraform/providers/.../terraform-provider-*: permission denied`

**Causa:** O binário do provider foi baixado sem bit de execução, comum após clone de repositório ou
cópia de diretório no Linux/WSL.

**Diagnóstico:**
```bash
ls -la .terraform/providers/*/*/
```

**Fix:**
```bash
find .terraform/providers -name "terraform-provider-*" -exec chmod +x {} \;
```

**Verificação:**
```bash
terraform validate
```
Deve retornar `Success!`.

---

## Incidente 2 — S3 State Lock Travado

**Sintoma:** `Error acquiring the state lock` / `Lock Info: ... Path: .../terraform.tfstate.tflock`

**Causa:** Um `terraform apply` ou `destroy` foi interrompido (Ctrl+C, timeout, kill) e o arquivo
de lock no S3 não foi removido automaticamente.

**Diagnóstico:**
```bash
aws s3 ls s3://devops-ia-production-terraform-state-074994084847/ --recursive | grep tflock
```

**Fix:**
```bash
# Substitua <stack-prefix> pelo prefixo correto (ex: eks, networking, ci-cd, addons)
aws s3 rm s3://devops-ia-production-terraform-state-074994084847/<stack-prefix>/terraform.tfstate.tflock
```

Prefixos por stack:
- `01-networking-stack-ai` → `networking/terraform.tfstate.tflock`
- `02-eks-stack-ai` → `eks/terraform.tfstate.tflock`
- `03-ci-cd-stack-ai` → `ci-cd/terraform.tfstate.tflock`
- `04-addons-stack-ai` → `addons/terraform.tfstate.tflock`

**Verificação:**
```bash
terraform plan -var-file="envs/production.tfvars"
```
Não deve mostrar mais o erro de lock.

---

## Incidente 3 — Backend Não Inicializado / Reconfigure Necessário

**Sintoma:** `Backend initialization required, please run "terraform init"` mesmo após já ter rodado
`init` antes.

**Causa:** A configuração do backend S3 mudou (bucket, key, região) ou o diretório `.terraform/`
está desatualizado.

**Diagnóstico:**
```bash
cat .terraform/terraform.tfstate 2>/dev/null | python3 -m json.tool | grep '"type"'
```

**Fix:**
```bash
terraform init -reconfigure
```

**Verificação:**
```bash
terraform validate -var-file="envs/production.tfvars"
```

---

## Incidente 4 — State Vazio / Recursos Existem na AWS mas Não no State

**Sintoma:** `terraform plan` mostra tudo para criar, mas os recursos já existem na AWS. Ou o apply
foi interrompido e o state ficou parcial/vazio.

**Causa:** Apply interrompido antes do Terraform gravar o state, ou state deletado acidentalmente.

**Diagnóstico:**
```bash
terraform state list 2>&1 | head -20
# Se vazio ou muito menor que o esperado, confirma o incidente
aws eks describe-cluster --name devops-ia-production --region us-east-1 --query 'cluster.status'
```

**Fix — Importar recursos da stack 02-eks-stack-ai:**
```bash
cd /home/lustrabits/DevOps-Nuvem/eks-terraform-cicd-monitoring-001/devops-ia-terraform/02-eks-stack-ai
VF="-var-file=envs/production.tfvars"

terraform import $VF aws_iam_role.cluster devops-ia-production-cluster-role
terraform import $VF aws_iam_role.node devops-ia-production-node-role
terraform import $VF 'aws_iam_role_policy_attachment.cluster_AmazonEKSClusterPolicy' \
  devops-ia-production-cluster-role/arn:aws:iam::aws:policy/AmazonEKSClusterPolicy
terraform import $VF 'aws_iam_role_policy_attachment.node_AmazonEKSWorkerNodePolicy' \
  devops-ia-production-node-role/arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy
terraform import $VF 'aws_iam_role_policy_attachment.node_AmazonEKS_CNI_Policy' \
  devops-ia-production-node-role/arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy
terraform import $VF 'aws_iam_role_policy_attachment.node_AmazonEC2ContainerRegistryReadOnly' \
  devops-ia-production-node-role/arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
terraform import $VF aws_eks_cluster.this devops-ia-production

# ECR repos
terraform import $VF 'aws_ecr_repository.this[0]' devops-ia/production/backend
terraform import $VF 'aws_ecr_repository.this[1]' devops-ia/production/frontend

# Obter Launch Template ID e Node Group antes de importar
LT_ID=$(aws ec2 describe-launch-templates --region us-east-1 \
  --filters "Name=launch-template-name,Values=devops-ia-production-node-*" \
  --query 'LaunchTemplates[0].LaunchTemplateId' --output text)
terraform import $VF aws_launch_template.node $LT_ID

SG_ID=$(aws ec2 describe-security-groups --region us-east-1 \
  --filters "Name=tag:Name,Values=devops-ia-production-cluster-sg" \
  --query 'SecurityGroups[0].GroupId' --output text)
terraform import $VF aws_security_group.cluster $SG_ID

terraform import $VF aws_eks_node_group.this devops-ia-production:devops-ia-production

# Addons
terraform import $VF aws_eks_addon.coredns devops-ia-production:coredns
terraform import $VF aws_eks_addon.kube_proxy devops-ia-production:kube-proxy
terraform import $VF aws_eks_addon.vpc_cni devops-ia-production:vpc-cni

# Access entry
terraform import $VF \
  'aws_eks_access_entry.admin["arn:aws:iam::074994084847:user/adm-user"]' \
  'devops-ia-production:arn:aws:iam::074994084847:user/adm-user'
terraform import $VF \
  'aws_eks_access_policy_association.admin["arn:aws:iam::074994084847:user/adm-user"]' \
  'devops-ia-production#arn:aws:iam::074994084847:user/adm-user#arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy'
```

**Verificação:**
```bash
terraform plan -var-file="envs/production.tfvars" | grep "Plan:"
# Deve mostrar: Plan: 0 to add, 0 to change, 0 to destroy.
```

---

## Incidente 5 — Node Group AL2 Deprecated (NodeCreationFailure)

**Sintoma:** Node group fica em `CREATING` por 15+ minutos sem nodes aparecerem no `kubectl get nodes`,
e eventualmente vira `NodeCreationFailure` ou `CREATE_FAILED`. Pode aparecer erro de AMI não encontrada.

**Causa:** A AWS parou de publicar AMIs EKS-otimizadas para Amazon Linux 2 em 26/11/2025. O `ami_type`
`AL2_x86_64` não encontra mais AMI válida.

**Diagnóstico:**
```bash
aws eks describe-nodegroup \
  --cluster-name devops-ia-production \
  --nodegroup-name devops-ia-production \
  --region us-east-1 \
  --query 'nodegroup.{status: status, health: health}'
```

**Fix — Parte 1: Atualizar ami_type no tfvars:**
```
# em envs/production.tfvars, dentro do bloco eks.node_group:
ami_type = "AL2023_x86_64_STANDARD"   # era AL2_x86_64
```

**Fix — Parte 2: Atualizar user_data no launch template para formato nodeadm (TOML):**

O arquivo `eks.launch-template.tf` deve ter este user_data (não o formato AL2 com bootstrap.sh):
```hcl
user_data = base64encode(<<-EOT
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="==BOUNDARY=="

--==BOUNDARY==
Content-Type: application/node.eks.aws

---
apiVersion: node.eks.aws/v1alpha1
kind: NodeConfig
spec:
  kubelet:
    config:
      maxPods: 110

--==BOUNDARY==--
EOT
)
```

**Fix — Parte 3: Aplicar:**
```bash
cd /home/lustrabits/DevOps-Nuvem/eks-terraform-cicd-monitoring-001/devops-ia-terraform/02-eks-stack-ai
terraform apply -auto-approve -var-file="envs/production.tfvars"
```

**Verificação:**
```bash
kubectl get nodes
# Deve mostrar 2 nodes com STATUS=Ready em menos de 5 minutos
```

---

## Incidente 6 — ASG Travado no Destroy (Instâncias Não Terminam)

**Sintoma:** `terraform destroy` fica preso no node group por 20+ minutos. As instâncias EC2 não
encerram. Na console AWS, o ASG mostra instâncias em `InService` sem escalar para zero.

**Causa:** O ASG tem `min_size > 0` e o Terraform está esperando o ASG esvaziar antes de deletar.

**Diagnóstico:**
```bash
ASG_NAME=$(aws autoscaling describe-auto-scaling-groups --region us-east-1 \
  --filters "Name=tag:eks:nodegroup-name,Values=devops-ia-production" \
  --query 'AutoScalingGroups[0].AutoScalingGroupName' --output text)
echo "ASG: $ASG_NAME"
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$ASG_NAME" --region us-east-1 \
  --query 'AutoScalingGroups[0].{Min: MinSize, Max: MaxSize, Desired: DesiredCapacity}'
```

**Fix:**
```bash
aws autoscaling update-auto-scaling-group \
  --auto-scaling-group-name "$ASG_NAME" \
  --min-size 0 \
  --desired-capacity 0 \
  --region us-east-1
```

Aguarde ~2 minutos para as instâncias terminarem, depois o `terraform destroy` deve prosseguir.

**Verificação:**
```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$ASG_NAME" --region us-east-1 \
  --query 'AutoScalingGroups[0].Instances'
# Deve retornar []
```

---

## Incidente 7 — kubectl Sem Credenciais / Erro de Autenticação Após Recreate

**Sintoma:** `error: You must be logged in to the server (Unauthorized)` ou
`Unable to connect to the server: getting credentials: exec: executable aws failed`
após destruir e recriar o cluster.

**Causa:** O kubeconfig local ainda aponta para o endpoint/certificado do cluster antigo.

**Diagnóstico:**
```bash
kubectl get nodes 2>&1 | head -3
```

**Fix:**
```bash
aws eks update-kubeconfig \
  --name devops-ia-production \
  --region us-east-1
```

**Verificação:**
```bash
kubectl get nodes
# Deve listar os nodes sem erro de autenticação
```

---

## Incidente 8 — Access Entry EKS Ausente Após Recreate

**Sintoma:** `kubectl get nodes` retorna `Error from server (Forbidden)` ou usuário não consegue
executar comandos kubectl mesmo com kubeconfig correto. Ocorre após destroy + recreate do cluster.

**Causa:** O `aws_eks_access_entry` e `aws_eks_access_policy_association` foram destruídos junto
com o cluster e precisam ser recriados. Se não estão no state, o Terraform não os recria.

**Diagnóstico:**
```bash
aws eks list-access-entries \
  --cluster-name devops-ia-production \
  --region us-east-1
# adm-user não deve aparecer na lista
```

**Fix — Via Terraform (preferido, já codificado em eks.access-entry.tf):**
```bash
cd /home/lustrabits/DevOps-Nuvem/eks-terraform-cicd-monitoring-001/devops-ia-terraform/02-eks-stack-ai
terraform apply -auto-approve -var-file="envs/production.tfvars"
```

**Fix — Via CLI (emergência, se Terraform não estiver disponível):**
```bash
aws eks create-access-entry \
  --cluster-name devops-ia-production \
  --principal-arn arn:aws:iam::074994084847:user/adm-user \
  --region us-east-1

aws eks associate-access-policy \
  --cluster-name devops-ia-production \
  --principal-arn arn:aws:iam::074994084847:user/adm-user \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
  --access-scope type=cluster \
  --region us-east-1
```

Após fix via CLI, importar para o state para evitar drift:
```bash
terraform import -var-file="envs/production.tfvars" \
  'aws_eks_access_entry.admin["arn:aws:iam::074994084847:user/adm-user"]' \
  'devops-ia-production:arn:aws:iam::074994084847:user/adm-user'

terraform import -var-file="envs/production.tfvars" \
  'aws_eks_access_policy_association.admin["arn:aws:iam::074994084847:user/adm-user"]' \
  'devops-ia-production#arn:aws:iam::074994084847:user/adm-user#arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy'
```

**Verificação:**
```bash
kubectl get nodes
kubectl get pods -A | head -10
```

---

## Incidente 9 — ArgoCD Pod Travado em ContainerCreating (CNI sem IP)

**Sintoma:** Pod do ArgoCD (ou qualquer pod) fica em `ContainerCreating` por 5+ minutos.
`kubectl describe pod` mostra eventos:
`Failed to create pod sandbox: ... aws-cni failed (add): failed to assign an IP address to container`

**Causa:** O VPC CNI (aws-node) ainda está aquecendo o pool de IPs após nodes recém-criados,
ou o pool de prefixos do ENI está temporariamente esgotado.

**Diagnóstico:**
```bash
kubectl get pod <nome-do-pod> -n <namespace> -o json | jq '{podIP: .status.podIP}'
# Se null, o IP não foi atribuído ainda
```

**Fix:**
```bash
# Deletar o pod para forçar um novo agendamento com pool de IPs mais fresco
kubectl delete pod <nome-do-pod> -n <namespace>
```

O ReplicaSet/Deployment vai criar um pod substituto. O novo pod geralmente obtém IP com sucesso
porque o pool já foi reabastecido na segunda tentativa.

**Verificação:**
```bash
kubectl get pod -n <namespace> -l app.kubernetes.io/name=<app> -o wide
# O novo pod deve ter um IP (ex: 10.0.x.y) dentro de 1-2 minutos
```

---

## Incidente 10 — ArgoCD dex-server Crash: "server.secretkey is missing"

**Sintoma:** `argocd-dex-server` fica em `CrashLoopBackOff`. Logs mostram:
`level=fatal msg="server.secretkey is missing"`

**Causa:** O dex-server iniciou antes do `argocd-server` terminar de bootstrapar o `argocd-secret`
com o campo `server.secretkey`. Ocorre tipicamente quando os nodes são recreados e todos os pods
sobem ao mesmo tempo — o dex inicia, tenta ler o secret que ainda não tem o campo, e crasha.

**Diagnóstico:**
```bash
# Verificar se o argocd-server está Running
kubectl get pods -n argocd -l app.kubernetes.io/name=argocd-server

# Verificar se o secret já tem o campo
kubectl get secret -n argocd argocd-secret -o jsonpath='{.data.server\.secretkey}' | base64 -d
# Deve retornar uma string não-vazia
```

**Fix:**
```bash
# Aguardar o argocd-server estar Running, depois:
kubectl rollout restart deployment/argocd-dex-server -n argocd
```

**Verificação:**
```bash
kubectl get pods -n argocd -l app.kubernetes.io/name=argocd-dex-server
# STATUS deve ser Running
kubectl logs -n argocd -l app.kubernetes.io/name=argocd-dex-server --tail=5
# Não deve aparecer "server.secretkey is missing"
```

---

## Incidente 11 — Nodes com Memória Insuficiente (t3.micro → t3.small)

**Sintoma:** Pods entram em `OOMKilled` ou `Pending` com `Insufficient memory`. `kubectl top nodes`
mostra memória acima de 85%. Pods do ArgoCD não conseguem ser agendados.

**Causa:** t3.micro tem 1 GiB RAM. ArgoCD + kube-system sozinhos consomem ~700 MiB, sem espaço
para aplicações.

**Diagnóstico:**
```bash
kubectl top nodes
kubectl get pods -A --field-selector=status.phase=Pending
```

**Fix:**
```bash
# Atualizar production.tfvars: instance_types = ["t3.small"]
sed -i 's/instance_types = \["t3.micro"\]/instance_types = ["t3.small"]/' \
  /home/lustrabits/DevOps-Nuvem/eks-terraform-cicd-monitoring-001/devops-ia-terraform/02-eks-stack-ai/envs/production.tfvars

cd /home/lustrabits/DevOps-Nuvem/eks-terraform-cicd-monitoring-001/devops-ia-terraform/02-eks-stack-ai
terraform apply -auto-approve -var-file="envs/production.tfvars"
```

Isso recria o node group (~8 min no total: 6 min destroy + 2 min create).
Aviso: t3.small não é free tier (~$0.021/hora por node).

**Verificação:**
```bash
kubectl top nodes
# Memória deve estar abaixo de 70% com os pods de sistema rodando
kubectl get nodes -o jsonpath='{.items[*].status.capacity.memory}'
# Deve mostrar ~1959220Ki por node (t3.small = 2 GiB)
```

---

## Incidente 12 — Node Group Demorando 20+ Minutos (Comportamento Normal)

**Sintoma:** `aws_eks_node_group` fica em `Still creating/destroying` por mais de 20 minutos.
`kubectl get nodes` já mostra nodes `Ready` mas o Terraform ainda aguarda.

**Causa:** Atraso de consistência eventual da API AWS neste ambiente. Os nodes ficam prontos em
~5 minutos mas a API do node group pode demorar 20+ minutos para reportar status `ACTIVE`.

**Ação:** Não cancele o processo. Este é o comportamento esperado neste ambiente. Confirme que
os nodes estão saudáveis enquanto aguarda:

```bash
kubectl get nodes
# Se Ready → está tudo bem, só aguardar o Terraform
aws eks describe-nodegroup \
  --cluster-name devops-ia-production \
  --nodegroup-name devops-ia-production \
  --region us-east-1 \
  --query 'nodegroup.{status: status, health: health}'
```

Se o processo for cancelado antes do Terraform concluir, o node group pode existir na AWS mas não
no state → aplicar o **Incidente 4** (import) antes de tentar novamente.

---

## Checklist Pós-Recreate Completo do Cluster

Sempre que o cluster for destruído e recriado do zero, execute nesta ordem:

```bash
# 1. Atualizar kubeconfig
aws eks update-kubeconfig --name devops-ia-production --region us-east-1

# 2. Verificar access entry (já gerenciado pelo Terraform, mas confirmar)
aws eks list-access-entries --cluster-name devops-ia-production --region us-east-1

# 3. Verificar nodes
kubectl get nodes

# 4. Instalar ArgoCD (se necessário)
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v2.14.11/manifests/install.yaml

# 5. Aguardar pods do ArgoCD (pode demorar 3-5 min)
kubectl wait pod --all -n argocd --for=condition=Ready --timeout=300s

# 6. Se dex-server crashar, aguardar argocd-server subir e então:
kubectl rollout restart deployment/argocd-dex-server -n argocd

# 7. Aplicar ArgoCD Application
kubectl apply -f /home/lustrabits/DevOps-Nuvem/eks-terraform-cicd-monitoring-001/devops-ia-kubernetes/argocd-application.yaml

# 8. Verificar estado final
kubectl get pods -n argocd
kubectl get application -n argocd
kubectl top nodes
```
