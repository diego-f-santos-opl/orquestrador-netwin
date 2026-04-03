# Scripts de Deploy — Netwin

## Fluxo completo de uma nova release

```bash
# 1. Exportar yamls do cluster
for deploy in $(kubectl get deployments -n netwin -o jsonpath='{.items[*].metadata.name}'); do
  kubectl get deployment $deploy -n netwin -o yaml > ${deploy}.yaml
done

# 2. Limpar campos gerados pelo Kubernetes
python3 clean-manifests.py *.yaml

# 3. Renomear removendo sufixo -clean
for f in *-clean.yaml; do mv "$f" "${f/-clean/}"; done

# 4. Atualizar versão e migrar registry
python3 update-version.py --version 1.0.6-r2

# 5. Subir na ordem correta
python3 startup.py
```

---

## clean-manifests.py

**O que faz:** Limpa os campos gerados automaticamente pelo Kubernetes dos arquivos yaml.

**Quando usar:** Sempre que exportar yamls do cluster com `kubectl get ... -o yaml` antes de versionar ou reaplicar.

**O que remove:**
- `resourceVersion`, `uid`, `creationTimestamp`, `generation`
- `kubectl.kubernetes.io/last-applied-configuration`
- `deployment.kubernetes.io/revision`
- `terminationMessagePath`, `terminationMessagePolicy`
- `schedulerName`, `dnsPolicy`, `restartPolicy`
- `serviceAccount` (duplicado do `serviceAccountName`)
- `status`

**Gera:** arquivos com sufixo `-clean.yaml` — o original não é alterado.

```bash
# Limpar um arquivo
python3 clean-manifests.py netwin-backend.yaml

# Limpar todos de uma vez
python3 clean-manifests.py *.yaml
```

---

## update-version.py

**O que faz:** Atualiza a versão da release nos manifests yaml e migra o registry das imagens do ACR para o GHCR.

**Quando usar:** A cada nova release antes de fazer o deploy.

**O que atualiza:**
- Label `app.kubernetes.io/version`
- Tag das imagens principais (`inventory-vtal`, `inventory-vtal-lb`)
- Registry: `acrdevopsfbdev2demo.azurecr.io/netwin` → `ghcr.io/alticelabsprojects`

**O que NÃO altera:**
- Imagens do docmanager, mongo e zookeeper (versão independente)
- Label `helm.sh/chart`

```bash
# Atualizar versão nos arquivos
python3 update-version.py --version 1.0.6-r2

# Simular sem alterar
python3 update-version.py --version 1.0.6-r2 --dry-run

# Atualizar e já aplicar no cluster
python3 update-version.py --version 1.0.6-r2 --apply

# Diretório específico
python3 update-version.py --version 1.0.6-r2 --dir ./manifests
```

---

## deploy.py

**O que faz:** Aplica manifests yaml no cluster Kubernetes.

**Quando usar:** Para aplicar um ou todos os manifests diretamente, sem ordem específica.

```bash
# Aplicar todos os manifests do diretório atual
python3 deploy.py

# Aplicar um manifesto específico
python3 deploy.py --file netwin-backend.yaml

# Simular sem aplicar
python3 deploy.py --dry-run

# Diretório e namespace específicos
python3 deploy.py --dir ./manifests --namespace netwin
```

---

## rollout.py

**O que faz:** Monitora, reinicia ou reverte Deployments e StatefulSets que já estão rodando. Não inicia deployments parados.

**Quando usar:** Após um deploy para verificar o estado, reiniciar pods ou reverter em caso de problema.

**Diferença para os outros scripts:**

| Script | Responsabilidade |
|---|---|
| `startup.py` | **Iniciar** deployments parados na ordem correta |
| `deploy.py` | **Aplicar** manifests no cluster |
| `rollout.py` | **Monitorar / reiniciar / reverter** deployments já rodando |

**`--status`** — Mostra o estado atual sem bloquear:
```bash
python3 rollout.py --status
python3 rollout.py --status --name deployment/netwin-backend
# netwin-backend    desired=1  ready=1  updated=1  available=1  [OK]
# netwin-wildfly    desired=1  ready=0  updated=1  available=0  [PENDENTE]
# netwin-frontend   desired=0  ready=0  updated=0  available=0  [PARADO]
```

**`--restart`** — Rolling restart nos deployments com replicas > 0. Deployments parados são ignorados:
```bash
python3 rollout.py --restart
python3 rollout.py --restart --name deployment/netwin-backend
```

**`--undo`** — Reverte para a versão anterior em caso de problema:
```bash
python3 rollout.py --undo
python3 rollout.py --undo --name deployment/netwin-backend
```

---

## startup.py

**O que faz:** Orquestra a subida de todos os componentes na ordem correta, verificando se cada um está pronto antes de iniciar o próximo. Pergunta quantos pods iniciar para cada componente.

**Quando usar:** Para subir o ambiente completo de forma controlada.

**Ordem de subida:**
```
MongoDB → Zookeeper → LoadBalancer Backend → LoadBalancer Frontend
→ Wildfly → Wildfly Feas → Wildfly Prov → Backend
→ Geoserver → Docmanager (*opcional) → Tomcat → Tomcat Prov → Frontend
```

**Comportamento especial:**
| Componente | Comportamento |
|---|---|
| MongoDB / Zookeeper | Só valida se está no ar — não reinicia |
| Geoserver | Inicia após o Backend — sem dependências |
| Demais componentes | Pergunta quantos pods e aplica o manifesto |
| Docmanager BE / FE | Pergunta se deseja iniciar antes de aplicar |

```bash
# Subida completa na ordem correta
python3 startup.py

# Simular sem aplicar nada
python3 startup.py --dry-run

# Sem aguardar cada componente ficar pronto
python3 startup.py --no-wait

# Diretório e namespace específicos
python3 startup.py --dir ./manifests --namespace netwin

# Timeout personalizado por componente (default: 300s)
python3 startup.py --timeout 600
```
