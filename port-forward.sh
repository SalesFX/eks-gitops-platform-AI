#!/bin/bash
# Inicia port-forwards para todos os serviços do cluster devops-ia-production.
# Reinicia automaticamente se a conexão cair.
# Uso: bash port-forward.sh

pf() {
  while true; do
    kubectl port-forward "$@" 2>/dev/null
    sleep 2
  done
}

echo "Iniciando port-forwards..."
echo "  Frontend  -> http://localhost:3000"
echo "  Backend   -> http://localhost:8080/backend/swagger"
echo "  Grafana   -> http://localhost:3001  (admin / devops-ia-2026)"
echo "  ArgoCD    -> https://localhost:8443 (usuario: admin)"
echo "             Senha: kubectl get secret -n argocd argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
echo ""
echo "Pressione Ctrl+C para encerrar todos."

pf svc/frontend 3000:3000 -n default &
pf svc/backend 8080:8080 -n default &
pf svc/victoria-metrics-grafana 3001:80 -n monitoring &
pf svc/argocd-server 8443:443 -n argocd &

wait
