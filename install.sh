#!/usr/bin/env bash
# ==============================================================================
#
#   install.sh — Instalador do Orquestrador Netwin
#
#   Uso     : sudo bash install.sh
#   Destino : /opt/orquestrador/
#   Comando : netwin  (qualquer usuário)
#
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------
INSTALL_DIR="/opt/orquestrador"
BIN_LINK="/usr/local/bin/netwin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[ OK ]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
err()     { echo -e "${RED}[ERRO]${RESET}  $*" >&2; }
die()     { err "$*"; exit 1; }
section() { echo -e "\n${BOLD}── $* ${RESET}"; }

# ------------------------------------------------------------------------------
# Root check
# ------------------------------------------------------------------------------
[[ $EUID -ne 0 ]] && die "Execute com sudo: sudo bash install.sh"

# ------------------------------------------------------------------------------
# Banner
# ------------------------------------------------------------------------------
clear
echo -e "${CYAN}"
echo "  ███╗   ██╗███████╗████████╗██╗    ██╗██╗███╗   ██╗"
echo "  ████╗  ██║██╔════╝╚══██╔══╝██║    ██║██║████╗  ██║"
echo "  ██╔██╗ ██║█████╗     ██║   ██║ █╗ ██║██║██╔██╗ ██║"
echo "  ██║╚██╗██║██╔══╝     ██║   ██║███╗██║██║██║╚██╗██║"
echo "  ██║ ╚████║███████╗   ██║   ╚███╔███╔╝██║██║ ╚████║"
echo "  ╚═╝  ╚═══╝╚══════╝   ╚═╝    ╚══╝╚══╝ ╚═╝╚═╝  ╚═══╝"
echo -e "${RESET}"
echo -e "  ${BOLD}Orquestrador de Deploy${RESET}  —  Instalador"
echo -e "  Destino: ${CYAN}${INSTALL_DIR}${RESET}"
echo -e "  Comando: ${GREEN}netwin${RESET}\n"

# ------------------------------------------------------------------------------
# Verificar Python3
# ------------------------------------------------------------------------------
section "Verificando dependências"

PYTHON=$(command -v python3 || true)
[[ -z "$PYTHON" ]] && die "python3 não encontrado. Instale: apt install python3"
PY_VERSION=$($PYTHON --version 2>&1)
ok "Python: $PY_VERSION"

PIP=$(command -v pip3 || true)
[[ -z "$PIP" ]] && die "pip3 não encontrado. Instale: apt install python3-pip"
ok "pip3 encontrado"

# Verificar kubectl
KUBECTL=$(command -v kubectl || true)
if [[ -z "$KUBECTL" ]]; then
    warn "kubectl não encontrado — necessário para operar o cluster"
else
    ok "kubectl: $(kubectl version --client --short 2>/dev/null | head -1 || echo 'ok')"
fi

# Verificar PyYAML e rich
section "Instalando dependências Python"

pip3 install --quiet pyyaml rich 2>/dev/null && ok "pyyaml + rich instalados" \
    || warn "Falha ao instalar dependências — tente manualmente: pip3 install pyyaml rich"

# ------------------------------------------------------------------------------
# Verificar arquivos fonte
# ------------------------------------------------------------------------------
section "Verificando arquivos fonte"

REQUIRED_FILES=(
    "netwin.py"
)

REQUIRED_SCRIPTS=(
    "scripts/startup.py"
    "scripts/shutdown.py"
    "scripts/deploy.py"
    "scripts/update-version.py"
    "scripts/migrate.py"
    "scripts/rollout.py"
    "scripts/validate.py"
    "scripts/clean-manifests.py"
)

for f in "${REQUIRED_FILES[@]}"; do
    [[ -f "$SCRIPT_DIR/$f" ]] || die "Arquivo não encontrado: $f\n  Certifique-se de executar install.sh de dentro do repositório."
    ok "$f"
done

MISSING_SCRIPTS=()
for f in "${REQUIRED_SCRIPTS[@]}"; do
    if [[ ! -f "$SCRIPT_DIR/$f" ]]; then
        MISSING_SCRIPTS+=("$f")
        warn "Script não encontrado: $f (será necessário adicionar depois)"
    else
        ok "$f"
    fi
done

# ------------------------------------------------------------------------------
# Instalar em /opt/orquestrador
# ------------------------------------------------------------------------------
section "Instalando em ${INSTALL_DIR}"

# Backup se já existir
if [[ -d "$INSTALL_DIR" ]]; then
    BACKUP="${INSTALL_DIR}.bak.$(date +%Y%m%d_%H%M%S)"
    info "Backup do diretório anterior em: $BACKUP"
    mv "$INSTALL_DIR" "$BACKUP"
fi

mkdir -p "$INSTALL_DIR"

# Copiar tudo do diretório atual
info "Copiando arquivos..."
cp -r "$SCRIPT_DIR"/. "$INSTALL_DIR"/

# Garantir estrutura de diretórios
for dir in scripts environments manifests db-migrate backups logs; do
    mkdir -p "$INSTALL_DIR/$dir"
done

ok "Arquivos copiados para $INSTALL_DIR"

# Permissões
chmod 755 "$INSTALL_DIR/netwin.py"
find "$INSTALL_DIR/scripts" -name "*.py" -exec chmod 755 {} \; 2>/dev/null || true
# Proteger environments (pode conter senhas de banco)
chmod 700 "$INSTALL_DIR/environments"

ok "Permissões aplicadas"

# ------------------------------------------------------------------------------
# Criar wrapper /usr/local/bin/netwin
# ------------------------------------------------------------------------------
section "Criando comando 'netwin'"

cat > "$BIN_LINK" << EOF
#!/usr/bin/env bash
# Wrapper gerado pelo install.sh
exec python3 ${INSTALL_DIR}/netwin.py "\$@"
EOF

chmod +x "$BIN_LINK"
ok "Comando criado: $BIN_LINK"

# ------------------------------------------------------------------------------
# Exemplo de ambiente (se environments/ estiver vazio)
# ------------------------------------------------------------------------------
section "Configuração de ambientes"

ENV_EXAMPLE="$INSTALL_DIR/environments/exemplo.yaml"
if [[ ! -f "$ENV_EXAMPLE" ]] && [[ -z "$(ls -A "$INSTALL_DIR/environments/" 2>/dev/null)" ]]; then
    cat > "$ENV_EXAMPLE" << 'ENVEOF'
# Exemplo de ambiente — renomeie e ajuste
# Arquivo: environments/dev-interno.yaml

name: dev-interno
label: "DEV Interno"
namespace: netwin
manifests_dir: manifests

cluster:
  type: rke2                          # rke2 | openshift | eks | gke
  context: ""                         # nome do contexto kubectl (vazio = padrão)
  kubeconfig: "~/.kube/config"

database:
  host: ""                            # host Oracle (deixe vazio se não usa migrate)
  port: 1521
  service: ""
  user: ""
  password: ""

startup:
  defaults:
    replicas: 1
  components:
    netwin-wildfly: 1
    netwin-wildfly-feas: 1
    netwin-wildfly-prov: 1
    netwin-backend: 1
    netwin-frontend: 1
    netwin-tomcat: 1
    netwin-tomcat-prov: 1
    netwin-geoserver: 1
    netwin-lb-backend: 1
    netwin-lb-frontend: 1
ENVEOF
    ok "Arquivo de exemplo criado: environments/exemplo.yaml"
    warn "Edite o arquivo acima ou crie novos em $INSTALL_DIR/environments/"
else
    info "Diretório environments/ já possui arquivos — mantidos."
fi

# ------------------------------------------------------------------------------
# Resumo
# ------------------------------------------------------------------------------
echo ""
echo -e "${CYAN}┌──────────────────────────────────────────────────┐${RESET}"
echo -e "${CYAN}│${RESET}  ${BOLD}Instalação concluída!${RESET}                            ${CYAN}│${RESET}"
echo -e "${CYAN}├──────────────────────────────────────────────────┤${RESET}"
echo -e "${CYAN}│${RESET}  Diretório : ${CYAN}${INSTALL_DIR}${RESET}"
echo -e "${CYAN}│${RESET}  Comando   : ${GREEN}netwin${RESET}"
echo -e "${CYAN}│${RESET}  Ambientes : ${INSTALL_DIR}/environments/"
echo -e "${CYAN}│${RESET}  Manifests : ${INSTALL_DIR}/manifests/"
echo -e "${CYAN}│${RESET}  Logs      : ${INSTALL_DIR}/logs/"
echo -e "${CYAN}└──────────────────────────────────────────────────┘${RESET}"

if [[ ${#MISSING_SCRIPTS[@]} -gt 0 ]]; then
    echo ""
    warn "Scripts ainda faltando em scripts/:"
    for s in "${MISSING_SCRIPTS[@]}"; do
        echo "    - $s"
    done
    warn "Adicione os scripts antes de usar o orquestrador."
fi

echo ""
echo -e "  ${BOLD}Próximos passos:${RESET}"
echo -e "  1. Edite   ${CYAN}${INSTALL_DIR}/environments/exemplo.yaml${RESET}"
echo -e "  2. Execute ${GREEN}netwin${RESET}"
echo ""
