#!/usr/bin/env python3
# ==============================================================================
#   Script    : startup.py
#   Versao    : 2.0.0
#   Autor     : Diego Regis M. F. dos Santos
#   Email     : diego-f-santos@openlabs.com.br
#   Time      : OpenLabs - DevOps | Infra
#   Desc      : Orquestra a subida dos componentes Netwin na ordem correta.
#               Suporta dois modos de operacao:
#
#   Modo interativo (padrao):
#     - Pergunta quantos pods iniciar para cada componente
#     - Pergunta se aguarda ou nao cada componente ficar pronto
#     - Pergunta sobre componentes opcionais (Docmanager)
#     - Em caso de falha ou timeout pergunta se continua
#
#   Modo automatico (--auto):
#     - Replicas definidas no bloco startup.components do YAML do ambiente
#     - Aguarda SEMPRE cada componente ficar Ready antes do proximo
#     - Opcionais nao definidos no YAML sao pulados silenciosamente
#     - Em caso de falha ou timeout aborta imediatamente
#     - Gera log em logs/startup_<env>_<timestamp>.log
#
#   Ordem de subida:
#     MongoDB -> Zookeeper -> LB-BE -> LB-FE
#     -> Wildfly -> Wildfly Feas -> Wildfly Prov -> Backend
#     -> Geoserver -> Docmanager (*opcional) -> Tomcat -> Tomcat Prov -> Frontend
#
#   Uso       : python3 scripts/startup.py [--auto] [--dry-run] [--no-wait]
#
#   Params    :
#     --dir        Diretorio com os manifests yaml
#     --namespace  Namespace do Kubernetes
#     --auto       Modo automatico (usa replicas do ambiente)
#     --dry-run    Simular sem aplicar nada
#     --no-wait    Nao aguardar cada componente (apenas modo interativo)
#     --timeout    Timeout por componente em segundos (default: 300)
#
#   Exemplos  :
#     python3 scripts/startup.py --namespace nossis-netwin-dev-hml
#     python3 scripts/startup.py --auto --namespace nossis-netwin-dev-hml
#     python3 scripts/startup.py --auto --dry-run
#     python3 scripts/startup.py --no-wait --timeout 600
#
#   Dependencias:
#     - pyyaml  : pip install pyyaml
#     - rich    : pip install rich (opcional, melhora visualizacao)
#
#   Historico :
#     1.0.0  2026-01-01  Criacao inicial — modo interativo
#     2.0.0  2026-04-01  Modo automatico, log em arquivo, Rich, KUBECONTEXT
# ==============================================================================

import sys
import os
import re
import time
import datetime
import argparse
import subprocess
import json

# =============================================================
# Rich — opcional, fallback para ANSI se nao instalado
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
KUBE_CONTEXT       = os.environ.get('KUBECONTEXT', '')
AUTO_REPLICAS_JSON = os.environ.get('STARTUP_REPLICAS', '{}')
AUTO_DEFAULT       = int(os.environ.get('STARTUP_DEFAULT_REPLICAS', '1'))
ENV_NAME           = os.environ.get('ENV_NAME', 'env')
LOG_DIR            = os.environ.get('LOG_DIR', 'logs')

try:
    AUTO_REPLICAS = json.loads(AUTO_REPLICAS_JSON)
except Exception:
    AUTO_REPLICAS = {}


def kubectl(*args):
    cmd = ['kubectl']
    if KUBE_CONTEXT:
        cmd += ['--context', KUBE_CONTEXT]
    cmd += list(args)
    return cmd


# =============================================================
# Ordem de subida
# =============================================================
STARTUP_ORDER = [
    {'name': 'MongoDB',              'kind': 'statefulset', 'resource': 'netwin-mongo',               'manifest': 'netwin-mongo.yaml',               'always_up': True,  'optional': False},
    {'name': 'Zookeeper',            'kind': 'statefulset', 'resource': 'netwin-zookeeper',           'manifest': 'netwin-zookeeper.yaml',           'always_up': True,  'optional': False},
    {'name': 'LoadBalancer Backend', 'kind': 'deployment',  'resource': 'netwin-loadbalancer-be',     'manifest': 'netwin-loadbalancer-be.yaml',     'always_up': False, 'optional': False},
    {'name': 'LoadBalancer Frontend','kind': 'deployment',  'resource': 'netwin-loadbalancer',        'manifest': 'netwin-loadbalancer.yaml',        'always_up': False, 'optional': False},
    {'name': 'Wildfly',              'kind': 'deployment',  'resource': 'netwin-wildfly',             'manifest': 'netwin-wildfly.yaml',             'always_up': False, 'optional': False},
    {'name': 'Wildfly Feas',         'kind': 'deployment',  'resource': 'netwin-wildfly-feas',        'manifest': 'netwin-wildfly-feas.yaml',        'always_up': False, 'optional': False},
    {'name': 'Wildfly Prov',         'kind': 'deployment',  'resource': 'netwin-wildfly-prov',        'manifest': 'netwin-wildfly-prov.yaml',        'always_up': False, 'optional': False},
    {'name': 'Backend',              'kind': 'deployment',  'resource': 'netwin-backend',             'manifest': 'netwin-backend.yaml',             'always_up': False, 'optional': False},
    {'name': 'Geoserver',            'kind': 'deployment',  'resource': 'netwin-geoserver',           'manifest': 'netwin-geoserver.yaml',           'always_up': False, 'optional': False},
    {'name': 'Docmanager Backend',   'kind': 'deployment',  'resource': 'netwin-docmanager-backend',  'manifest': 'netwin-docmanager-backend.yaml',  'always_up': False, 'optional': True},
    {'name': 'Docmanager Frontend',  'kind': 'deployment',  'resource': 'netwin-docmanager-frontend', 'manifest': 'netwin-docmanager-frontend.yaml', 'always_up': False, 'optional': True},
    {'name': 'Tomcat',               'kind': 'deployment',  'resource': 'netwin-tomcat',              'manifest': 'netwin-tomcat.yaml',              'always_up': False, 'optional': False},
    {'name': 'Tomcat Prov',          'kind': 'deployment',  'resource': 'netwin-tomcat-prov',         'manifest': 'netwin-tomcat-prov.yaml',         'always_up': False, 'optional': False},
    {'name': 'Frontend',             'kind': 'deployment',  'resource': 'netwin-frontend',            'manifest': 'netwin-frontend.yaml',            'always_up': False, 'optional': False},
]

# =============================================================
# Logger — duplica stdout para arquivo (modo auto)
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
# Helpers de output
# =============================================================
GREEN  = '\033[92m'
YELLOW = '\033[93m'
RED    = '\033[91m'
CYAN   = '\033[96m'
DIM    = '\033[2m'
RESET  = '\033[0m'

def rprint(msg):
    if RICH:
        console.print(msg)
    else:
        print(re.sub(r'\[/?[a-z_ ]*\]', '', msg))

def ok(msg):   print(f"  {GREEN}✔  {msg}{RESET}")
def warn(msg): print(f"  {YELLOW}⚠  {msg}{RESET}")
def err(msg):  print(f"  {RED}✘  {msg}{RESET}")
def info(msg): print(f"  {CYAN}→  {msg}{RESET}")


def print_banner(mode, namespace, manifests_dir, dry_run, no_wait, timeout,
                 auto_replicas=None, auto_default=1, log_path=None):
    if RICH:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim",   justify="right",  min_width=12)
        grid.add_column(style="white", justify="left",   min_width=32)
        color = "yellow" if mode == 'AUTO' else "cyan"
        grid.add_row("modo",      f"[bold {color}]{mode}[/bold {color}]")
        grid.add_row("namespace", f"[yellow]{namespace}[/yellow]")
        grid.add_row("manifests", f"[dim]{manifests_dir}[/dim]")
        grid.add_row("dry-run",   "[yellow]Sim[/yellow]" if dry_run else "[dim]Nao[/dim]")
        if mode == 'AUTO':
            grid.add_row("wait",  "[green]Sempre (obrigatorio)[/green]")
            grid.add_row("log",   f"[dim]{log_path or 'dry-run, sem log'}[/dim]")
        else:
            grid.add_row("no-wait", "[yellow]Sim[/yellow]" if no_wait else "[dim]Nao[/dim]")
            grid.add_row("timeout", f"[dim]{timeout}s por componente[/dim]")
        console.print(Panel(
            grid,
            title=f"[bold white]Netwin Startup[/bold white]  [dim]{'Automatico' if mode == 'AUTO' else 'Interativo'}[/dim]",
            border_style=color,
            width=66, padding=(0, 2),
        ))
        if mode == 'AUTO' and auto_replicas:
            t = Table(box=None, show_header=True, padding=(0, 1),
                      show_edge=False, header_style="bold dim")
            t.add_column("Componente", style="white",  min_width=35)
            t.add_column("Replicas",   style="cyan",   width=8, justify="right")
            for k, v in auto_replicas.items():
                t.add_row(k, str(v))
            t.add_row("[dim](demais — padrao)[/dim]", f"[dim]{auto_default}[/dim]")
            console.print(Panel(t, title="[dim]Replicas configuradas[/dim]",
                                border_style="dim", width=66, padding=(0, 1)))
        console.print()
    else:
        print(f"\n{'='*60}")
        print(f"  Netwin Startup  [{mode}]")
        print(f"{'='*60}")
        print(f"  Namespace : {namespace}")
        print(f"  Manifests : {manifests_dir}")
        print(f"  Dry-run   : {'Sim' if dry_run else 'Nao'}")
        if mode == 'AUTO':
            print(f"  Wait      : Sempre")
            print(f"  Log       : {log_path or 'dry-run'}")
            if auto_replicas:
                print(f"  Replicas  :")
                for k, v in auto_replicas.items():
                    print(f"    {k:<35} {v}")
                print(f"    {'(padrao)':<35} {auto_default}")
        else:
            print(f"  No-wait   : {'Sim' if no_wait else 'Nao'}")
            print(f"  Timeout   : {timeout}s")
        print(f"{'='*60}\n")


def print_component_header(i, total, name, optional=False):
    tag = " [dim](opcional)[/dim]" if optional else ""
    if RICH:
        console.print(f"\n  [dim][{i}/{total}][/dim]  [bold white]{name}[/bold white]{tag}")
        console.print(f"  [dim]{'─' * 54}[/dim]")
    else:
        opt = " (opcional)" if optional else ""
        print(f"\n  [{i}/{total}] {name}{opt}")
        print(f"  {'─' * 54}")


def print_summary(success, skipped, failed, elapsed, log_path=None):
    if RICH:
        t = Table(box=None, show_header=False, padding=(0, 2), show_edge=False)
        t.add_column(style="dim",   justify="right",  min_width=10)
        t.add_column(style="white", justify="left",   min_width=20)
        t.add_row("sucesso", f"[green]{success}[/green]")
        t.add_row("pulados", f"[yellow]{skipped}[/yellow]")
        t.add_row("falhas",  f"[red]{failed}[/red]")
        t.add_row("tempo",   f"[dim]{elapsed}s[/dim]")
        if log_path:
            t.add_row("log", f"[dim]{log_path}[/dim]")
        border = "green" if failed == 0 else "red"
        title  = "[green]Concluido com sucesso[/green]" if failed == 0 else "[red]Concluido com falhas[/red]"
        console.print(Panel(t, title=title, border_style=border, width=66, padding=(0, 2)))
    else:
        print(f"\n{'='*60}")
        print(f"  Resumo")
        print(f"{'='*60}")
        print(f"  {GREEN}✔ Sucesso : {success}{RESET}")
        print(f"  {YELLOW}⚠ Pulados : {skipped}{RESET}")
        print(f"  {RED}✘ Falhas  : {failed}{RESET}")
        print(f"  Tempo     : {elapsed}s")
        if log_path:
            print(f"  Log       : {log_path}")
        print(f"{'='*60}\n")


# =============================================================
# Kubernetes helpers
# =============================================================
def run(args, capture=True):
    return subprocess.run(args, capture_output=capture, text=True)


def krun(*args, capture=True):
    return run(kubectl(*args), capture=capture)


def is_ready(kind, resource, namespace):
    result = krun('get', kind, resource, '-n', namespace,
                  '-o', 'jsonpath={.status.readyReplicas}/{.spec.replicas}')
    if result.returncode != 0:
        return False, '0/0'
    status = result.stdout.strip() or '0/0'
    parts  = status.split('/')
    try:
        ready   = int(parts[0]) if parts[0] else 0
        desired = int(parts[1]) if parts[1] else 0
        return (ready > 0 and ready >= desired), status
    except Exception:
        return False, status


def apply_manifest(manifest_path, namespace, dry_run=False):
    cmd = kubectl('apply', '-f', manifest_path, '-n', namespace)
    if dry_run:
        cmd += ['--dry-run=client']
    result = run(cmd)
    return result.returncode == 0, result.stderr.strip()


def set_replicas(manifest_path, replicas):
    import yaml
    with open(manifest_path) as f:
        doc = yaml.safe_load(f)
    doc['spec']['replicas'] = replicas
    with open(manifest_path, 'w') as f:
        yaml.dump(doc, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def wait_ready(kind, resource, namespace, timeout=300):
    info(f"Aguardando {resource} ficar pronto (timeout: {timeout}s)...")
    elapsed  = 0
    interval = 10
    while elapsed < timeout:
        ready, status = is_ready(kind, resource, namespace)
        if ready:
            return True
        print(f"  {DIM}  [{elapsed:>4}s]  {status} — aguardando...{RESET}")
        time.sleep(interval)
        elapsed += interval
    return False


# =============================================================
# Input helpers
# =============================================================
def ask_user(question):
    while True:
        resp = input(f"\n  {YELLOW}? {question} [s/n]: {RESET}").strip().lower()
        if resp in ('s', 'sim', 'y', 'yes'): return True
        if resp in ('n', 'nao', 'não', 'no'): return False


def ask_replicas(name, default=1):
    while True:
        resp = input(f"  {YELLOW}? Replicas para {name} [default: {default}]: {RESET}").strip()
        if resp == '': return default
        try:
            val = int(resp)
            if val >= 0: return val
        except ValueError:
            pass
        print(f"  {RED}Digite um numero valido.{RESET}")


# =============================================================
# Processar componente
# =============================================================
def process_component(comp, args, auto_mode, auto_replicas, auto_default):
    name      = comp['name']
    kind      = comp['kind']
    resource  = comp['resource']
    manifest  = os.path.join(args.dir, comp['manifest'])
    always_up = comp['always_up']
    optional  = comp['optional']

    if not os.path.exists(manifest) and not args.dry_run:
        warn(f"Manifesto nao encontrado: {comp['manifest']} — pulando")
        return 'skipped'

    # Always_up — apenas validar (MongoDB, Zookeeper)
    if always_up:
        ready, status = is_ready(kind, resource, args.namespace)
        if ready:
            ok(f"Ja esta no ar ({status})")
            return 'success'
        err(f"Nao esta pronto ({status})!")
        if auto_mode:
            err("MODO AUTO: componente de dados nao esta no ar — abortando.")
            return 'abort'
        return 'failed' if not ask_user(f"{name} nao esta no ar. Deseja continuar mesmo assim?") else 'failed_continue'

    # Modo automatico
    if auto_mode:
        if optional and resource not in auto_replicas:
            info("Opcional nao configurado no ambiente — pulando")
            return 'skipped'
        replicas = auto_replicas.get(resource, auto_default)
        info(f"Replicas (auto): {replicas}")
    else:
        if optional and not args.dry_run:
            ready, status = is_ready(kind, resource, args.namespace)
            if ready:
                ok(f"Ja esta no ar ({status})")
                return 'success'
            if not ask_user(f"Deseja iniciar o {name}?"):
                info("Pulado pelo usuario")
                return 'skipped'

        if args.dry_run:
            ok(f"[dry-run] {comp['manifest']} seria aplicado")
            return 'success'

        replicas = ask_replicas(name)
        if replicas == 0:
            info("Pulado — 0 replicas solicitadas")
            return 'skipped'

    if args.dry_run:
        ok(f"[dry-run] {comp['manifest']} seria aplicado com {replicas} replica(s)")
        return 'success'

    set_replicas(manifest, replicas)
    info(f"Aplicando {comp['manifest']} — {replicas} replica(s)...")

    applied, error_msg = apply_manifest(manifest, args.namespace)
    if not applied:
        err(f"Falha ao aplicar: {error_msg}")
        if auto_mode:
            err("MODO AUTO: abortando.")
            return 'abort'
        return 'failed' if not ask_user(f"Falha no {name}. Deseja continuar?") else 'failed_continue'

    ok("Manifesto aplicado com sucesso")

    # Aguardar Ready — obrigatorio no modo auto
    no_wait = args.no_wait if not auto_mode else False
    if not no_wait:
        ready = wait_ready(kind, resource, args.namespace, args.timeout)
        if ready:
            _, status = is_ready(kind, resource, args.namespace)
            ok(f"Pronto! ({status})")
            return 'success'
        err(f"Timeout aguardando {name}!")
        if auto_mode:
            err("MODO AUTO: abortando.")
            return 'abort'
        return 'failed' if not ask_user(f"Timeout no {name}. Deseja continuar?") else 'failed_continue'

    return 'success'


# =============================================================
# Main
# =============================================================
def main():
    parser = argparse.ArgumentParser(
        description='Orquestra a subida dos componentes Netwin na ordem correta'
    )
    parser.add_argument('--dir',       '-d', default='manifests',  help='Diretorio com os manifests yaml')
    parser.add_argument('--namespace', '-n', default='netwin',     help='Namespace do Kubernetes')
    parser.add_argument('--auto',            action='store_true',  help='Modo automatico: usa replicas do YAML do ambiente')
    parser.add_argument('--dry-run',         action='store_true',  help='Simular sem aplicar nada')
    parser.add_argument('--no-wait',         action='store_true',  help='Nao aguardar cada componente (modo interativo)')
    parser.add_argument('--timeout',   '-t', type=int, default=300, help='Timeout por componente em segundos (default: 300)')
    args = parser.parse_args()

    auto_mode     = args.auto
    auto_replicas = AUTO_REPLICAS.copy()

    result = krun('version', '--request-timeout=5s')
    if result.returncode != 0:
        err("Nao foi possivel conectar ao cluster Kubernetes.")
        sys.exit(1)

    tee      = None
    log_path = None
    if auto_mode and not args.dry_run:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts       = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        log_path = os.path.join(LOG_DIR, f"startup_{ENV_NAME}_{ts}.log")
        tee      = Tee(log_path)
        sys.stdout = tee

    ts_start = datetime.datetime.now()

    print_banner(
        mode          = 'AUTO' if auto_mode else 'INTERATIVO',
        namespace     = args.namespace,
        manifests_dir = args.dir,
        dry_run       = args.dry_run,
        no_wait       = args.no_wait,
        timeout       = args.timeout,
        auto_replicas = auto_replicas if auto_mode else None,
        auto_default  = AUTO_DEFAULT,
        log_path      = log_path,
    )

    success = 0
    skipped = 0
    failed  = 0
    total   = len(STARTUP_ORDER)

    for i, comp in enumerate(STARTUP_ORDER, 1):
        print_component_header(i, total, comp['name'], comp['optional'])
        result = process_component(comp, args, auto_mode, auto_replicas, AUTO_DEFAULT)

        if result == 'success':
            success += 1
        elif result == 'skipped':
            skipped += 1
        elif result == 'failed_continue':
            failed += 1
        elif result in ('failed', 'abort'):
            failed += 1
            warn("Startup interrompido.")
            break

    elapsed = (datetime.datetime.now() - ts_start).seconds

    if tee:
        sys.stdout = tee._stdout
        tee.close()

    print_summary(success, skipped, failed, elapsed, log_path)

    if log_path:
        rprint(f"\n  [cyan]→ Log salvo em: {log_path}[/cyan]\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
