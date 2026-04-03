#!/usr/bin/env python3
# ==============================================================================
#   Script    : update-version.py
#   Versao    : 2.0.0
#   Autor     : Diego Regis M. F. dos Santos
#   Email     : diego-f-santos@openlabs.com.br
#   Time      : OpenLabs - DevOps | Infra
#   Desc      : Atualiza a versao da release nos manifests yaml do Netwin.
#               Migra registry ACR -> GHCR, faz backup automatico da versao
#               atual, atualiza o job de migrate do ambiente e gera log
#               completo da operacao.
#
#   O que atualiza:
#     - Label app.kubernetes.io/version em todos os manifests
#     - Tag das imagens principais (inventory-vtal, inventory-vtal-lb)
#     - Registry: acrdevopsfbdev2demo.azurecr.io/netwin -> ghcr.io/alticelabsprojects
#     - Manifesto do job de migrate (db-migrate/<env>/netwin-db-migrate.yaml)
#
#   O que NAO altera:
#     - Imagens do docmanager, mongo e zookeeper (versao independente)
#     - Label helm.sh/chart
#
#   Backup automatico:
#     - Cria backups/<versao-atual>/ com todos os manifests antes de atualizar
#     - Inclui _backup-info.yaml com data, versao e lista de arquivos
#
#   Log:
#     - Gera logs/update_<env>_<versao>_<timestamp>.log com toda a operacao
#
#   Uso       : python3 scripts/update-version.py --version 1.0.7-r1
#
#   Params    :
#     --version    Nova versao ex: 1.0.7-r1 (obrigatorio)
#     --dir        Diretorio com os manifests yaml
#     --namespace  Namespace do Kubernetes
#     --apply      Aplicar os manifests no cluster apos atualizar
#     --dry-run    Mostrar o que seria feito sem alterar arquivos
#
#   Exemplos  :
#     python3 scripts/update-version.py --version 1.0.7-r1
#     python3 scripts/update-version.py --version 1.0.7-r1 --dry-run
#     python3 scripts/update-version.py --version 1.0.7-r1 --apply
#
#   Dependencias:
#     - pyyaml : pip install pyyaml
#     - rich   : pip install rich (opcional)
#
#   Historico :
#     1.0.0  2026-01-01  Criacao inicial
#     2.0.0  2026-04-01  Rich, log em arquivo, migrate por ambiente, KUBECONTEXT
# ==============================================================================

import sys
import os
import re
import yaml
import shutil
import datetime
import argparse
import subprocess

# =============================================================
# Rich — opcional
# =============================================================
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    console = None

# =============================================================
# Env vars — injetadas pelo netwin.py
# =============================================================
KUBE_CONTEXT = os.environ.get('KUBECONTEXT', '')
ENV_NAME     = os.environ.get('ENV_NAME',    'env')
LOG_DIR      = os.environ.get('LOG_DIR',     'logs')

# Caminho do db-migrate injetado pelo netwin.py
# Ex: /home/vtal/manager-netwin/db-migrate/dev-interno/netwin-db-migrate.yaml
MIGRATE_MANIFEST_PATH = os.environ.get('MIGRATE_MANIFEST', '')


def kubectl(*args):
    cmd = ['kubectl']
    if KUBE_CONTEXT:
        cmd += ['--context', KUBE_CONTEXT]
    cmd += list(args)
    return cmd


# =============================================================
# Logger — duplica stdout para arquivo
# =============================================================
class Tee:
    def __init__(self, log_path):
        self._log    = open(log_path, 'w', encoding='utf-8', buffering=1)
        self._stdout = sys.stdout

    def write(self, data):
        self._stdout.write(data)
        self._log.write(re.sub(r'\033\[[0-9;]*m', '', data))

    def flush(self):
        self._stdout.flush()
        self._log.flush()

    def close(self):
        self._log.close()

    def fileno(self):
        return self._stdout.fileno()


# =============================================================
# Cores / helpers output
# =============================================================
GREEN  = '\033[92m'
YELLOW = '\033[93m'
RED    = '\033[91m'
CYAN   = '\033[96m'
DIM    = '\033[2m'
BOLD   = '\033[1m'
RESET  = '\033[0m'

def short_path(path, max_len=44):
    """Exibe path a partir do projeto — remove prefixo do home."""
    home = os.path.expanduser('~')
    rel  = path.replace(home, '~') if path.startswith(home) else path
    if len(rel) <= max_len:
        return rel
    # Manter as ultimas partes do path
    parts = rel.replace('\\', '/').split('/')
    result = rel
    while len(result) > max_len and len(parts) > 2:
        parts = ['...'] + parts[2:]
        result = '/'.join(parts)
    return result


def ok(msg):   print(f"  {GREEN}✔  {msg}{RESET}")
def warn(msg): print(f"  {YELLOW}⚠  {msg}{RESET}")
def err(msg):  print(f"  {RED}✘  {msg}{RESET}")
def info(msg): print(f"  {CYAN}→  {msg}{RESET}")

def rprint(msg):
    if RICH:
        console.print(msg)
    else:
        print(re.sub(r'\[/?[a-z_ ]*\]', '', msg))


def print_banner(args, current_version, migrate_manifest, log_path):
    if RICH:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim",   justify="right",  min_width=14)
        grid.add_column(style="white", justify="left",   min_width=44)
        grid.add_row("diretorio",  f"[dim]{short_path(args.dir)}[/dim]")
        grid.add_row("versao atual", f"[yellow]{current_version}[/yellow]")
        grid.add_row("nova versao", f"[green bold]{args.version}[/green bold]")
        grid.add_row("namespace",  f"[dim]{args.namespace}[/dim]")
        grid.add_row("dry-run",    "[yellow]Sim[/yellow]" if args.dry_run else "[dim]Nao[/dim]")
        grid.add_row("aplicar",    "[green]Sim[/green]"   if args.apply   else "[dim]Nao[/dim]")
        grid.add_row("migrate",    f"[dim]{os.path.basename(migrate_manifest)}[/dim]")
        grid.add_row("log",        f"[dim]{short_path(log_path, max_len=52) if log_path else 'dry-run'}[/dim]")
        console.print(Panel(
            grid,
            title="[bold white]Netwin Update Version[/bold white]",
            border_style="blue",
            width=72, padding=(0, 2),
        ))
        console.print()
    else:
        print(f"\n{'='*60}")
        print(f"  Netwin Update Version")
        print(f"{'='*60}")
        print(f"  Diretorio    : {args.dir}")
        print(f"  Versao atual : {current_version}")
        print(f"  Nova versao  : {args.version}")
        print(f"  Namespace    : {args.namespace}")
        print(f"  Dry-run      : {'Sim' if args.dry_run else 'Nao'}")
        print(f"  Aplicar      : {'Sim' if args.apply else 'Nao'}")
        print(f"  Migrate      : {migrate_manifest}")
        print(f"  Log          : {log_path or 'dry-run'}")
        print(f"{'='*60}\n")


def print_summary(updated, skipped_files, migrate_ok, backup_dir,
                  current_version, new_version, elapsed, log_path, apply_ok=None):
    if RICH:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim",   justify="right",  min_width=14)
        grid.add_column(style="white", justify="left",   min_width=30)
        grid.add_row("versao",    f"[yellow]{current_version}[/yellow]  →  [green bold]{new_version}[/green bold]")
        grid.add_row("manifests", f"[green]{updated} atualizado(s)[/green]" + (f"  [dim]{skipped_files} ignorado(s)[/dim]" if skipped_files else ""))
        grid.add_row("migrate",   "[green]Atualizado[/green]" if migrate_ok else "[yellow]Nao encontrado[/yellow]")
        grid.add_row("backup",    f"[dim]{short_path(backup_dir) if backup_dir else 'dry-run'}[/dim]")
        grid.add_row("tempo",     f"[dim]{elapsed}s[/dim]")
        if apply_ok is not None:
            grid.add_row("apply", "[green]Aplicado[/green]" if apply_ok else "[red]Falhou[/red]")
        if log_path:
            grid.add_row("log",   f"[dim]{short_path(log_path)}[/dim]")
        console.print(Panel(
            grid,
            title="[green]Concluido com sucesso[/green]",
            border_style="green",
            width=66, padding=(0, 2),
        ))
    else:
        print(f"\n{'='*60}")
        print(f"  Resumo")
        print(f"{'='*60}")
        print(f"  Versao    : {current_version} -> {new_version}")
        print(f"  Manifests : {updated} atualizado(s)")
        print(f"  Migrate   : {'OK' if migrate_ok else 'nao encontrado'}")
        print(f"  Backup    : {backup_dir or 'dry-run'}")
        print(f"  Tempo     : {elapsed}s")
        if log_path:
            print(f"  Log       : {log_path}")
        print(f"{'='*60}\n")


# =============================================================
# Imagens fixas — nao atualizar
# =============================================================
FIXED_IMAGES = [
    'nossis-docmanager-backend',
    'nossis-docmanager-frontend',
    'nossis-docmanager-frontend-nginx',
    'mongodb',
    'zookeeper',
]

GHCR_REGISTRY = 'ghcr.io/alticelabsprojects'
ACR_REGISTRY  = 'acrdevopsfbdev2demo.azurecr.io/netwin'


def is_fixed_image(image):
    return any(f in image for f in FIXED_IMAGES)


def migrate_registry(image):
    if ACR_REGISTRY in image:
        repo = image.split('/')[-1].split(':')[0]
        tag  = image.split(':')[-1]
        return f'{GHCR_REGISTRY}/{repo}:{tag}'
    return image


def update_image_version(image, new_version):
    if is_fixed_image(image):
        return image
    image = migrate_registry(image)
    return re.sub(r':[^:]+$', f':{new_version}', image)


def update_labels_version(labels, new_version):
    if labels and 'app.kubernetes.io/version' in labels:
        labels['app.kubernetes.io/version'] = new_version
    return labels


def update_containers(containers, new_version):
    for c in containers:
        if 'image' in c:
            old = c['image']
            c['image'] = update_image_version(c['image'], new_version)
            if old != c['image']:
                info(f"  imagem: {old.split('/')[-1]}  →  {c['image'].split('/')[-1]}")
    return containers


def update_manifest(data, new_version):
    kind = data.get('kind', '')
    if 'metadata' in data:
        data['metadata']['labels'] = update_labels_version(
            data['metadata'].get('labels', {}), new_version)
    if kind in ('Deployment', 'StatefulSet', 'Job'):
        spec     = data.get('spec', {})
        template = spec.get('template', {})
        if 'metadata' in template:
            template['metadata']['labels'] = update_labels_version(
                template['metadata'].get('labels', {}), new_version)
        pod_spec = template.get('spec', {})
        update_containers(pod_spec.get('containers',     []), new_version)
        update_containers(pod_spec.get('initContainers', []), new_version)
    return data


def process_file(filepath, new_version):
    with open(filepath, 'r') as f:
        content = f.read()
    docs = [d for d in yaml.safe_load_all(content) if d]
    updated = [update_manifest(d, new_version) for d in docs]
    with open(filepath, 'w') as f:
        yaml.dump_all(updated, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)
    ok(f"{os.path.basename(filepath)}")
    return filepath


def resolve_migrate_manifest(manifest_dir):
    """Resolve o caminho do manifest de migrate do ambiente ativo."""
    # 1. Injetado pelo netwin.py via env var (mais confiavel)
    if MIGRATE_MANIFEST_PATH and os.path.exists(MIGRATE_MANIFEST_PATH):
        return MIGRATE_MANIFEST_PATH

    # 2. Inferir pelo diretorio de manifests: manifests/dev-interno -> db-migrate/dev-interno/
    base_dir = os.path.dirname(manifest_dir)
    env_name = os.path.basename(manifest_dir)  # ex: dev-interno, hml-dev
    candidate = os.path.join(base_dir, 'db-migrate', env_name, 'netwin-db-migrate.yaml')
    if os.path.exists(candidate):
        return candidate

    # 3. Fallback: db-migrate/ na raiz do projeto
    root = base_dir
    fallback = os.path.join(root, 'db-migrate', 'netwin-db-migrate.yaml')
    if os.path.exists(fallback):
        return fallback

    return candidate  # retorna o path esperado mesmo sem existir (para exibir no SKIP)


def process_migrate(migrate_manifest, new_version):
    if os.path.exists(migrate_manifest):
        try:
            with open(migrate_manifest, 'r') as f:
                content = f.read()
            docs = [d for d in yaml.safe_load_all(content) if d]
            updated = [update_manifest(d, new_version) for d in docs]
            with open(migrate_manifest, 'w') as f:
                yaml.dump_all(updated, f, default_flow_style=False,
                              allow_unicode=True, sort_keys=False)
            ok(f"{os.path.basename(migrate_manifest)}  [dim]({migrate_manifest})[/dim]")
            return True
        except Exception as e:
            err(f"{migrate_manifest} -> {e}")
            return False
    else:
        warn(f"Nao encontrado: {migrate_manifest}")
        return False


def get_current_version(manifest_dir):
    for f in sorted(os.listdir(manifest_dir)):
        if not f.endswith('.yaml'):
            continue
        try:
            with open(os.path.join(manifest_dir, f)) as fh:
                doc = yaml.safe_load(fh)
            if doc and 'metadata' in doc:
                v = doc['metadata'].get('labels', {}).get('app.kubernetes.io/version')
                if v:
                    return v
        except Exception:
            continue
    return 'unknown'


def create_backup(manifest_dir, migrate_manifest, current_version):
    backup_base = os.path.join(os.path.dirname(manifest_dir), 'backups')
    backup_dir  = os.path.join(backup_base, current_version)

    if os.path.exists(backup_dir):
        warn(f"Backup ja existe em backups/{current_version}/ — pulando")
        return backup_dir

    os.makedirs(backup_dir, exist_ok=True)
    count = 0

    for f in os.listdir(manifest_dir):
        if f.endswith(('.yaml', '.yml')):
            shutil.copy2(os.path.join(manifest_dir, f), os.path.join(backup_dir, f))
            count += 1

    if os.path.exists(migrate_manifest):
        shutil.copy2(migrate_manifest, os.path.join(backup_dir, os.path.basename(migrate_manifest)))
        count += 1

    yaml.dump(
        {'version': current_version, 'timestamp': datetime.datetime.now().isoformat(), 'files': count},
        open(os.path.join(backup_dir, '_backup-info.yaml'), 'w'),
        default_flow_style=False
    )

    ok(f"{count} arquivo(s) salvos em backups/{current_version}/")
    return backup_dir


def apply_manifests(directory, namespace):
    rprint(f"\n  [cyan]→  Aplicando manifests no namespace '{namespace}'...[/cyan]")
    result = subprocess.run(
        kubectl('apply', '-f', directory, '-n', namespace),
        capture_output=True, text=True
    )
    if result.stdout:
        for line in result.stdout.strip().split('\n'):
            print(f"  {line}")
    if result.stderr:
        for line in result.stderr.strip().split('\n'):
            print(f"  {YELLOW}{line}{RESET}")
    if result.returncode != 0:
        err("Falha ao aplicar manifests!")
        return False
    ok("Deploy aplicado com sucesso!")
    return True


# =============================================================
# Main
# =============================================================
def main():
    parser = argparse.ArgumentParser(
        description='Atualiza versao nos manifests Kubernetes do Netwin'
    )
    parser.add_argument('--version',   '-v', required=True,         help='Nova versao ex: 1.0.7-r1')
    parser.add_argument('--dir',       '-d', default='manifests',   help='Diretorio com os manifests yaml')
    parser.add_argument('--apply',     '-a', action='store_true',   help='Aplicar os manifests no cluster apos atualizar')
    parser.add_argument('--namespace', '-n', default='netwin',      help='Namespace do Kubernetes (default: netwin)')
    parser.add_argument('--dry-run',         action='store_true',   help='Mostrar o que seria feito sem alterar arquivos')
    args = parser.parse_args()

    # Listar yamls
    yaml_files = sorted([
        os.path.join(args.dir, f)
        for f in os.listdir(args.dir)
        if f.endswith(('.yaml', '.yml'))
    ])
    if not yaml_files:
        err(f"Nenhum arquivo yaml encontrado em: {args.dir}")
        sys.exit(1)

    current_version  = get_current_version(args.dir)
    migrate_manifest = resolve_migrate_manifest(args.dir)

    # Validar versao ANTES de iniciar log ou exibir banner
    if current_version == args.version:
        if RICH:
            from rich.panel import Panel as _P
            console.print(_P(
                f"[yellow]Versao atual ja e [bold]{args.version}[/bold] — nada a atualizar.[/yellow]",
                border_style="yellow", width=66
            ))
        else:
            warn(f"Versao atual ja e {args.version} — nada a atualizar.")
        sys.exit(0)

    # Configurar log
    os.makedirs(LOG_DIR, exist_ok=True)
    ts       = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = None if args.dry_run else os.path.join(
        LOG_DIR, f"update_{ENV_NAME}_{args.version}_{ts}.log"
    )
    tee = None
    if log_path:
        tee = Tee(log_path)
        sys.stdout = tee

    ts_start = datetime.datetime.now()

    print_banner(args, current_version, migrate_manifest, log_path)

    if args.dry_run:
        rprint("\n  [yellow]DRY-RUN — nenhum arquivo sera alterado:[/yellow]")
        rprint(f"  [dim]Backup seria criado em: backups/{current_version}/[/dim]")
        for f in yaml_files:
            rprint(f"  [dim]  {os.path.basename(f)}[/dim]")
        rprint(f"  [dim]  {os.path.basename(migrate_manifest)}  (migrate)[/dim]")
        return

    # Backup
    rprint(f"\n  [bold]Criando backup da versao {current_version}...[/bold]")
    backup_dir = create_backup(args.dir, migrate_manifest, current_version)

    # Atualizar manifests
    rprint(f"\n  [bold]Atualizando {len(yaml_files)} manifest(s)...[/bold]")
    updated = 0
    for filepath in yaml_files:
        try:
            process_file(filepath, args.version)
            updated += 1
        except Exception as e:
            err(f"{os.path.basename(filepath)} -> {e}")

    # Atualizar migrate
    rprint(f"\n  [bold]Atualizando job de migrate...[/bold]")
    migrate_ok = process_migrate(migrate_manifest, args.version)

    # Aplicar no cluster
    apply_ok = None
    if args.apply:
        apply_ok = apply_manifests(args.dir, args.namespace)
    else:
        rprint(f"\n  [dim]Para aplicar rode:[/dim]")
        rprint(f"  [cyan]kubectl apply -f {args.dir} -n {args.namespace}[/cyan]")
        if KUBE_CONTEXT:
            rprint(f"  [dim]  --context {KUBE_CONTEXT}[/dim]")

    elapsed = (datetime.datetime.now() - ts_start).seconds

    if tee:
        sys.stdout = tee._stdout
        tee.close()

    print_summary(
        updated        = updated,
        skipped_files  = len(yaml_files) - updated,
        migrate_ok     = migrate_ok,
        backup_dir     = backup_dir,
        current_version= current_version,
        new_version    = args.version,
        elapsed        = elapsed,
        log_path       = log_path,
        apply_ok       = apply_ok,
    )

    if log_path:
        rprint(f"\n  [cyan]→  Log salvo em: {log_path}[/cyan]\n")


if __name__ == '__main__':
    main()
