#!/usr/bin/env python3
# ==============================================================================
#   Script    : migrate.py
#   Versao    : 2.0.0
#   Autor     : Diego Regis M. F. dos Santos
#   Email     : diego-f-santos@openlabs.com.br
#   Time      : OpenLabs - DevOps | Infra
#   Desc      : Atualiza a versao no manifesto do job de migrate, verifica
#               conectividade com o banco Oracle, executa o job no cluster,
#               aguarda a conclusao, coleta o log do pod e valida a execucao
#               consultando DATABASE_SCHEMA_UPDATES no Oracle.
#               Gera evidencia em TXT e HTML apos validacao.
#
#   Banco     : Oracle — configurado via env vars do ambiente ativo
#               (injetado pelo netwin.py: DB_HOST, DB_PORT, DB_SERVICE,
#                DB_USER, DB_PASSWORD)
#               Requer: pip install oracledb
#
#   Validacao : Consulta DATABASE_SCHEMA_UPDATES filtrando EXEC_DATE
#               dos ultimos 2 dias. Gera evidencia TXT e HTML.
#
#   Uso       : python3 scripts/migrate.py --version 1.0.7-r1
#
#   Params    :
#     --version        Nova versao ex: 1.0.7-r1
#     --validate-only  Somente validar no banco, sem executar o job
#     --env            Nome do ambiente (ex: dev-interno, hml-dev)
#     --manifest       Manifesto do job (override)
#     --namespace      Namespace Kubernetes (default: netwin)
#     --log-dir        Diretorio de logs (default: logs/)
#     --timeout        Timeout aguardando o job em segundos (default: 600)
#     --dry-run        Simular sem alterar nem aplicar
#
#   Exemplos  :
#     python3 scripts/migrate.py --version 1.0.7-r1
#     python3 scripts/migrate.py --version 1.0.7-r1 --dry-run
#     python3 scripts/migrate.py --validate-only
#     python3 scripts/migrate.py --version 1.0.7-r1 --timeout 900
#
#   Dependencias:
#     - pyyaml   : pip install pyyaml
#     - oracledb : pip install oracledb
#     - rich     : pip install rich (opcional)
#
#   Historico :
#     1.0.0  2026-01-01  Criacao inicial
#     2.0.0  2026-04-01  Rich, cabeçalho, DB por ambiente, KUBECONTEXT, melhorias visuais
# ==============================================================================

import sys
import os
import re
import time
import socket
import datetime
import argparse
import subprocess
import yaml

# =============================================================
# Dependencias opcionais
# =============================================================
try:
    import oracledb
    ORACLEDB_AVAILABLE = True
except ImportError:
    ORACLEDB_AVAILABLE = False

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

# Banco Oracle — lido do ambiente ativo, fallback dev-interno
DB_HOST     = os.environ.get('DB_HOST',     '10.51.201.2')
DB_PORT     = int(os.environ.get('DB_PORT', 1521))
DB_SERVICE  = os.environ.get('DB_SERVICE',  'netwdev')
DB_USER     = os.environ.get('DB_USER',     'NETWIN_SOL')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'NETWIN_SOL')

ACR_REGISTRY  = 'acrdevopsfbdev2demo.azurecr.io/netwin'
GHCR_REGISTRY = 'ghcr.io/alticelabsprojects'

# =============================================================
# Cores / helpers
# =============================================================
GREEN  = '\033[92m'
YELLOW = '\033[93m'
RED    = '\033[91m'
CYAN   = '\033[96m'
DIM    = '\033[2m'
RESET  = '\033[0m'

def ok(msg):   print(f"  {GREEN}✔  {msg}{RESET}")
def warn(msg): print(f"  {YELLOW}⚠  {msg}{RESET}")
def err(msg):  print(f"  {RED}✘  {msg}{RESET}")
def info(msg): print(f"  {CYAN}→  {msg}{RESET}")

def rprint(msg):
    if RICH:
        console.print(msg)
    else:
        print(re.sub(r'\[/?[a-z_ ]*\]', '', msg))

def short_path(path, max_len=48):
    home = os.path.expanduser('~')
    rel  = path.replace(home, '~') if path.startswith(home) else path
    if len(rel) <= max_len:
        return rel
    parts = rel.replace('\\', '/').split('/')
    result = rel
    while len(result) > max_len and len(parts) > 2:
        parts = ['...'] + parts[2:]
        result = '/'.join(parts)
    return result


def ask_user(question):
    while True:
        resp = input(f"\n  {YELLOW}? {question} [s/n]: {RESET}").strip().lower()
        if resp in ('s', 'sim', 'y', 'yes'): return True
        if resp in ('n', 'nao', 'não', 'no'): return False


# =============================================================
# Banner principal
# =============================================================
def print_banner(job_name, current_version, new_version, namespace,
                 manifest, dry_run, timeout, log_dir):
    if RICH:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim",   justify="right",  min_width=14)
        grid.add_column(style="white", justify="left",   min_width=42)
        grid.add_row("job",          f"[white]{job_name}[/white]")
        grid.add_row("versao atual", f"[yellow]{current_version}[/yellow]")
        grid.add_row("nova versao",  f"[green bold]{new_version}[/green bold]")
        grid.add_row("namespace",    f"[dim]{namespace}[/dim]")
        grid.add_row("manifesto",    f"[dim]{short_path(manifest)}[/dim]")
        grid.add_row("banco",        f"[dim]{DB_HOST}:{DB_PORT}/{DB_SERVICE}[/dim]")
        grid.add_row("dry-run",      "[yellow]Sim[/yellow]" if dry_run else "[dim]Nao[/dim]")
        grid.add_row("timeout",      f"[dim]{timeout}s[/dim]")
        grid.add_row("log-dir",      f"[dim]{short_path(log_dir)}[/dim]")
        console.print(Panel(
            grid,
            title="[bold white]Netwin DB Migrate[/bold white]",
            border_style="magenta",
            width=72, padding=(0, 2),
        ))
        console.print()
    else:
        print(f"\n{'='*60}")
        print(f"  Netwin DB Migrate")
        print(f"{'='*60}")
        print(f"  Job          : {job_name}")
        print(f"  Versao atual : {current_version}")
        print(f"  Nova versao  : {new_version}")
        print(f"  Namespace    : {namespace}")
        print(f"  Manifesto    : {manifest}")
        print(f"  Banco        : {DB_HOST}:{DB_PORT}/{DB_SERVICE}")
        print(f"  Dry-run      : {'Sim' if dry_run else 'Nao'}")
        print(f"  Timeout      : {timeout}s")
        print(f"{'='*60}\n")


def print_result(result_status, log_file=None):
    if RICH:
        if result_status == 'success':
            msg    = "[green bold]MIGRATE CONCLUIDO COM SUCESSO![/green bold]"
            border = "green"
        elif result_status == 'failed':
            msg    = "[red bold]MIGRATE FALHOU! Verifique o log.[/red bold]"
            border = "red"
        elif result_status == 'timeout':
            msg    = "[yellow bold]TIMEOUT! O job ainda pode estar rodando.[/yellow bold]"
            border = "yellow"
        else:
            msg    = "[red bold]ERRO INESPERADO.[/red bold]"
            border = "red"
        content = msg
        if log_file:
            content += f"\n[dim]Log: {short_path(log_file)}[/dim]"
        console.print(Panel(content, border_style=border, width=72, padding=(0, 2)))
    else:
        print(f"\n{'='*60}")
        if result_status == 'success':
            print(f"  {GREEN}MIGRATE CONCLUIDO COM SUCESSO!{RESET}")
        elif result_status == 'failed':
            print(f"  {RED}MIGRATE FALHOU! Verifique o log.{RESET}")
        elif result_status == 'timeout':
            print(f"  {YELLOW}TIMEOUT! O job ainda pode estar rodando.{RESET}")
        else:
            print(f"  {RED}ERRO INESPERADO.{RESET}")
        if log_file:
            print(f"  Log: {log_file}")
        print(f"{'='*60}\n")


# =============================================================
# Kubernetes
# =============================================================
def kubectl(*args):
    cmd = ['kubectl']
    if KUBE_CONTEXT:
        cmd += ['--context', KUBE_CONTEXT]
    cmd += list(args)
    return cmd

def run(args, capture=True):
    return subprocess.run(args, capture_output=capture, text=True)

def krun(*args, capture=True):
    return run(kubectl(*args), capture=capture)

def job_exists(name, namespace):
    return krun('get', 'job', name, '-n', namespace).returncode == 0

def delete_job(name, namespace):
    info(f"Removendo job antigo: {name}...")
    krun('delete', 'job', name, '-n', namespace, '--ignore-not-found')
    time.sleep(3)
    ok("Job antigo removido")

def apply_job(manifest, namespace):
    result = krun('apply', '-f', manifest, '-n', namespace)
    return result.returncode == 0, result.stderr.strip()

def get_job_status(name, namespace):
    result = krun('get', 'job', name, '-n', namespace,
                  '-o', 'jsonpath={.status.succeeded}/{.status.failed}/{.status.active}')
    if result.returncode != 0:
        return None, None, None
    parts = result.stdout.strip().split('/')
    try:
        return (
            int(parts[0]) if parts[0] else 0,
            int(parts[1]) if parts[1] else 0,
            int(parts[2]) if parts[2] else 0,
        )
    except Exception:
        return 0, 0, 0

def get_pod_name(job_name, namespace):
    result = krun('get', 'pods', '-n', namespace,
                  '-l', f'job-name={job_name}',
                  '-o', 'jsonpath={.items[0].metadata.name}')
    return result.stdout.strip() if result.returncode == 0 else None

def get_pod_logs(pod_name, namespace):
    result = krun('logs', pod_name, '-n', namespace)
    return result.stdout if result.returncode == 0 else result.stderr

def wait_job(name, namespace, timeout=600):
    info(f"Aguardando conclusao do job (timeout: {timeout}s)...")
    elapsed  = 0
    interval = 10
    while elapsed < timeout:
        succeeded, failed, active = get_job_status(name, namespace)
        if succeeded is None:
            err("Job nao encontrado!")
            return 'error'
        if succeeded > 0:
            return 'success'
        if failed and failed > 0:
            return 'failed'
        print(f"  {DIM}  [{elapsed:>4}s]  active={active}  succeeded={succeeded}  failed={failed}{RESET}")
        time.sleep(interval)
        elapsed += interval
    return 'timeout'


# =============================================================
# Manifesto
# =============================================================
def update_image(image, new_version):
    if ACR_REGISTRY in image:
        repo = image.split('/')[-1].split(':')[0]
        return f'{GHCR_REGISTRY}/{repo}:{new_version}'
    return re.sub(r':[^:]+$', f':{new_version}', image)

def update_manifest(manifest_path, new_version):
    with open(manifest_path) as f:
        doc = yaml.safe_load(f)
    old_version = doc.get('metadata', {}).get('labels', {}).get('app.kubernetes.io/version', 'desconhecida')
    for labels in [
        doc.get('metadata', {}).get('labels', {}),
        doc.get('spec', {}).get('template', {}).get('metadata', {}).get('labels', {})
    ]:
        if 'app.kubernetes.io/version' in labels:
            labels['app.kubernetes.io/version'] = new_version
    containers = doc.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
    for c in containers:
        if 'image' in c:
            c['image'] = update_image(c['image'], new_version)
    pull_secrets = doc.get('spec', {}).get('template', {}).get('spec', {}).get('imagePullSecrets', [])
    new_secrets = []
    for s in pull_secrets:
        if s.get('name') in ('acrdevopsfbdev2demo', 'acr-secret'):
            new_secrets.append({'name': 'ghcr-secret'})
        else:
            new_secrets.append(s)
    if not any(s.get('name') == 'ghcr-secret' for s in new_secrets):
        new_secrets.append({'name': 'ghcr-secret'})
    doc['spec']['template']['spec']['imagePullSecrets'] = new_secrets
    with open(manifest_path, 'w') as f:
        yaml.dump(doc, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return old_version


# =============================================================
# Oracle / Validacao
# =============================================================
def check_db_connection(host, port, timeout=5):
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, socket.error):
        return False

def validate_migration(exec_date, version='unknown', log_dir='logs'):
    if not ORACLEDB_AVAILABLE:
        warn("oracledb nao instalado — validacao ignorada")
        warn("Instale com: pip install oracledb")
        return False
    info(f"Conectando ao banco Oracle ({DB_HOST}:{DB_PORT}/{DB_SERVICE})...")
    try:
        dsn  = oracledb.makedsn(DB_HOST, DB_PORT, service_name=DB_SERVICE)
        conn = oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=dsn)
        cur  = conn.cursor()
        cur.execute(
            "SELECT FILENAME, VERSION, EXEC_ON_SCHEMA, EXEC_DATE "
            "FROM DATABASE_SCHEMA_UPDATES "
            "WHERE TRUNC(EXEC_DATE) >= TRUNC(:exec_date) - 2 "
            "ORDER BY EXEC_DATE DESC",
            exec_date=exec_date
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            warn("Nenhum registro encontrado nos ultimos 2 dias")
            return False

        if RICH:
            t = Table(box=box.ROUNDED, show_lines=False, header_style="bold cyan",
                      border_style="dim")
            t.add_column("#",         style="dim",    width=4,  justify="right")
            t.add_column("Filename",  style="white",  min_width=30, max_width=50, no_wrap=False)
            t.add_column("Version",   style="yellow", width=10)
            t.add_column("Schema",    style="green",  width=14)
            t.add_column("Exec Date", style="cyan",   width=20)
            for idx, row in enumerate(rows, 1):
                filename, ver, schema, date = row
                t.add_row(str(idx), str(filename), str(ver), str(schema),
                          str(date)[:19] if date else '')
            console.print()
            console.print(t)
        else:
            sep = '-' * 78
            print(f"\n  {sep}")
            print(f"  {'#':<4} {'FILENAME':<45} {'VER':<10} {'SCHEMA':<14} EXEC_DATE")
            print(f"  {sep}")
            for idx, row in enumerate(rows, 1):
                filename, ver, schema, date = row
                fname = str(filename)
                fname = fname if len(fname) <= 44 else '...' + fname[-41:]
                print(f"  {idx:<4} {fname:<45} {str(ver):<10} {GREEN}{str(schema):<14}{RESET} {str(date)[:19] if date else ''}")
            print(f"  {sep}")

        ok(f"{len(rows)} registro(s) encontrado(s)")
        txt_file, html_file = save_evidence(rows, version, exec_date, log_dir)
        ok(f"Evidencia TXT  : {short_path(txt_file)}")
        ok(f"Evidencia HTML : {short_path(html_file)}")
        return True

    except oracledb.DatabaseError as e:
        err(f"Erro ao consultar banco: {e}")
        return False
    except Exception as e:
        err(f"Erro inesperado: {e}")
        return False


def save_evidence(rows, version, exec_date, log_dir):
    os.makedirs(log_dir, exist_ok=True)
    ts        = exec_date.strftime('%Y%m%d_%H%M%S')
    date_str  = exec_date.strftime('%d/%m/%Y %H:%M:%S')
    base_name = f"evidence_migrate_{version}_{ts}"

    # TXT
    txt_file = os.path.join(log_dir, f"{base_name}.txt")
    sep = '-' * 80
    with open(txt_file, 'w') as f:
        f.write('=' * 80 + '\n')
        f.write('  Netwin DB Migrate -- Evidencia de Validacao\n')
        f.write('=' * 80 + '\n')
        f.write(f'  Versao    : {version}\n')
        f.write(f'  Data      : {date_str}\n')
        f.write(f'  Registros : {len(rows)}\n')
        f.write(f'  Banco     : {DB_HOST}:{DB_PORT}/{DB_SERVICE}\n')
        f.write('=' * 80 + '\n\n')
        f.write(f'  {sep}\n')
        f.write(f'  {"#":<4} {"FILENAME":<50} {"VERSION":<10} {"SCHEMA":<15} EXEC_DATE\n')
        f.write(f'  {sep}\n')
        for idx, row in enumerate(rows, 1):
            filename, ver, schema, date = row
            f.write(f'  {idx:<4} {str(filename):<50} {str(ver):<10} {str(schema):<15} {str(date)[:19] if date else ""}\n')
        f.write(f'  {sep}\n')
        f.write(f'\n  {len(rows)} registro(s) — {exec_date.strftime("%d/%m/%Y")}\n')
        f.write('\n' + '=' * 80 + '\n')
        f.write(f'  Gerado em {date_str} — Netwin Orquestrador de Deploy\n')
        f.write(f'  Autor: Diego Regis M. F. dos Santos — OpenLabs DevOps | Infra\n')

    # HTML
    html_file = os.path.join(log_dir, f"{base_name}.html")
    rows_html = ''
    for idx, row in enumerate(rows, 1):
        filename, ver, schema, date = row
        bg = '#1e1e2e' if idx % 2 == 0 else '#181825'
        rows_html += (
            f'<tr style="background:{bg}">'
            f'<td style="text-align:center;color:#6c7086">{idx}</td>'
            f'<td style="word-break:break-all">{filename}</td>'
            f'<td style="color:#f9e2af">{ver}</td>'
            f'<td style="color:#a6e3a1">{schema}</td>'
            f'<td style="color:#89dceb">{str(date)[:19] if date else ""}</td>'
            f'</tr>\n'
        )
    html_content = (
        '<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">'
        f'<title>Evidencia Migrate {version}</title>'
        '<style>'
        'body{font-family:monospace;background:#11111b;color:#cdd6f4;padding:2rem}'
        'h1{color:#89dceb;font-size:1.2rem;border-bottom:1px solid #313244;padding-bottom:.5rem}'
        '.info{background:#181825;border:1px solid #313244;border-radius:6px;padding:1rem;margin-bottom:1.5rem;font-size:.9rem}'
        '.info span{color:#a6adc8}.info b{color:#cba6f7}'
        'table{width:100%;border-collapse:collapse;font-size:.85rem}'
        'thead tr{background:#1e1e2e;color:#89b4fa}'
        'th{padding:.6rem 1rem;text-align:left;border-bottom:2px solid #313244}'
        'td{padding:.5rem 1rem;border-bottom:1px solid #1e1e2e}'
        '.ok{color:#a6e3a1;font-weight:bold}'
        '.footer{margin-top:1.5rem;color:#6c7086;font-size:.8rem;border-top:1px solid #313244;padding-top:.5rem}'
        '</style></head><body>'
        '<h1>Netwin DB Migrate — Evidencia de Validacao</h1>'
        '<div class="info">'
        f'<span>Versao:</span> <b>{version}</b> &nbsp;&nbsp;'
        f'<span>Data:</span> <b>{date_str}</b> &nbsp;&nbsp;'
        f'<span>Banco:</span> <b>{DB_HOST}:{DB_PORT}/{DB_SERVICE}</b> &nbsp;&nbsp;'
        f'<span>Registros:</span> <b class="ok">{len(rows)}</b>'
        '</div>'
        '<table><thead><tr><th>#</th><th>Filename</th><th>Version</th><th>Schema</th><th>Exec Date</th></tr></thead>'
        f'<tbody>{rows_html}</tbody></table>'
        f'<div class="footer">Gerado em {date_str} — Netwin Orquestrador de Deploy<br>'
        f'Autor: Diego Regis M. F. dos Santos — OpenLabs DevOps | Infra</div>'
        '</body></html>'
    )
    with open(html_file, 'w') as f:
        f.write(html_content)

    return txt_file, html_file


def save_log(log_content, version, log_dir):
    os.makedirs(log_dir, exist_ok=True)
    ts       = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = os.path.join(log_dir, f"migrate_{version}_{ts}.log")
    with open(filename, 'w') as f:
        f.write(f"# Netwin DB Migrate Log\n")
        f.write(f"# Versao    : {version}\n")
        f.write(f"# Timestamp : {datetime.datetime.now().isoformat()}\n")
        f.write(f"# Banco     : {DB_HOST}:{DB_PORT}/{DB_SERVICE}\n")
        f.write(f"# Autor     : Diego Regis M. F. dos Santos\n")
        f.write(f"{'='*60}\n\n")
        f.write(log_content)
    return filename


# =============================================================
# Main
# =============================================================
def main():
    parser = argparse.ArgumentParser(
        description='Atualiza versao do job de migrate e executa no cluster'
    )
    parser.add_argument('--version',       '-v', required=False,        help='Nova versao ex: 1.0.7-r1')
    parser.add_argument('--validate-only',       action='store_true',   help='Somente validar no banco sem executar o job')
    parser.add_argument('--env',           '-e', default=None,          help='Nome do ambiente (ex: dev-interno, hml-dev)')
    parser.add_argument('--manifest',      '-m', default=None,          help='Manifesto do job (override)')
    parser.add_argument('--namespace',     '-n', default='netwin',      help='Namespace (default: netwin)')
    parser.add_argument('--log-dir',       '-l', default=LOG_DIR,       help='Diretorio de logs')
    parser.add_argument('--timeout',       '-t', type=int, default=600, help='Timeout em segundos (default: 600)')
    parser.add_argument('--dry-run',             action='store_true',   help='Simular sem alterar nem aplicar')
    args = parser.parse_args()

    # Resolver manifesto
    if not args.manifest:
        if args.env:
            args.manifest = os.path.join('db-migrate', args.env, 'netwin-db-migrate.yaml')
        else:
            ns_to_env = {
                'netwin':                'dev-interno',
                'nossis-netwin-dev-hml': 'hml-dev',
            }
            env_name  = ns_to_env.get(args.namespace, args.namespace)
            candidate = os.path.join('db-migrate', env_name, 'netwin-db-migrate.yaml')
            args.manifest = candidate if os.path.exists(candidate) else os.path.join('db-migrate', 'netwin-db-migrate.yaml')

    # Modo validacao apenas
    if args.validate_only:
        if RICH:
            console.print(Panel(
                f"[dim]Consultando DATABASE_SCHEMA_UPDATES — ultimos 2 dias[/dim]\n"
                f"[dim]Banco: {DB_HOST}:{DB_PORT}/{DB_SERVICE}[/dim]",
                title="[bold white]Netwin DB Migrate — Validacao[/bold white]",
                border_style="magenta", width=72
            ))
        else:
            print(f"\n{'='*60}\n  Netwin DB Migrate — Validacao\n{'='*60}\n")
        exec_date = datetime.datetime.now()
        validate_migration(exec_date, version='manual', log_dir=args.log_dir)
        print()
        sys.exit(0)

    if not args.version:
        err("Informe a versao com --version ex: 1.0.7-r1")
        sys.exit(1)

    if not os.path.exists(args.manifest):
        err(f"Manifesto nao encontrado: {args.manifest}")
        sys.exit(1)

    # Verificar conexao cluster
    result = krun('version', '--request-timeout=5s')
    if result.returncode != 0:
        err("Nao foi possivel conectar ao cluster Kubernetes.")
        sys.exit(1)

    with open(args.manifest) as f:
        doc = yaml.safe_load(f)
    current_version = doc.get('metadata', {}).get('labels', {}).get('app.kubernetes.io/version', 'desconhecida')
    job_name        = doc.get('metadata', {}).get('name', 'netwin-db-migrate')

    print_banner(job_name, current_version, args.version, args.namespace,
                 args.manifest, args.dry_run, args.timeout, args.log_dir)

    info(f"Atualizando manifesto: {current_version} → {args.version}...")
    if args.dry_run:
        ok(f"[dry-run] Manifesto seria atualizado para {args.version}")
        ok(f"[dry-run] Job seria aplicado e aguardado no cluster")
        sys.exit(0)

    update_manifest(args.manifest, args.version)
    ok(f"Manifesto atualizado: {current_version} → {args.version}")

    # Verificar banco Oracle
    print()
    info(f"Verificando conectividade com o banco Oracle ({DB_HOST}:{DB_PORT})...")
    if check_db_connection(DB_HOST, DB_PORT):
        ok(f"Banco acessivel em {DB_HOST}:{DB_PORT}")
    else:
        err(f"Banco inacessivel em {DB_HOST}:{DB_PORT}!")
        if not ask_user("Banco inacessivel. Deseja executar o migrate mesmo assim?"):
            info("Job nao executado. Verifique a conectividade com o banco.")
            sys.exit(1)

    if not ask_user("Deseja executar o job de migrate agora?"):
        info("Job nao executado. Manifesto atualizado e pronto para uso.")
        sys.exit(0)

    # Remover job antigo se existir
    if job_exists(job_name, args.namespace):
        warn(f"Job '{job_name}' ja existe — sera removido antes de aplicar")
        delete_job(job_name, args.namespace)

    print()
    info(f"Aplicando {args.manifest}...")
    applied, apply_err = apply_job(args.manifest, args.namespace)
    if not applied:
        err(f"Falha ao aplicar: {apply_err}")
        sys.exit(1)
    ok("Job aplicado com sucesso")

    # Aguardar conclusao
    print()
    result_status = wait_job(job_name, args.namespace, args.timeout)

    # Coletar logs do pod
    print()
    info("Coletando logs do pod...")
    pod_name    = get_pod_name(job_name, args.namespace)
    log_content = ''

    if pod_name:
        info(f"Pod: {pod_name}")
        log_content = get_pod_logs(pod_name, args.namespace)
        lines = log_content.strip().split('\n')
        if RICH:
            from rich.panel import Panel as _P
            console.print(_P(
                '\n'.join(f"[dim]{l}[/dim]" for l in lines[-20:]),
                title=f"[dim]Ultimas 20 linhas — {pod_name}[/dim]",
                border_style="dim", width=72
            ))
        else:
            print(f"\n  {'─'*56}")
            print(f"  Ultimas 20 linhas do log — {pod_name}")
            print(f"  {'─'*56}")
            for line in lines[-20:]:
                print(f"  {line}")
            print(f"  {'─'*56}\n")
    else:
        warn("Pod nao encontrado — log indisponivel")

    # Salvar log
    log_file = None
    if log_content:
        log_file = save_log(log_content, args.version, args.log_dir)
        ok(f"Log salvo: {short_path(log_file)}")

    # Resultado
    print()
    print_result(result_status, log_file)

    # Validacao pos-migrate
    if result_status == 'success':
        if RICH:
            console.print(Panel(
                f"[dim]Consultando DATABASE_SCHEMA_UPDATES — banco {DB_HOST}:{DB_PORT}/{DB_SERVICE}[/dim]",
                title="[bold white]Validacao Pos-Migrate[/bold white]",
                border_style="cyan", width=72
            ))
        else:
            print(f"\n{'='*60}\n  Validacao pos-migrate\n{'='*60}\n")
        validate_migration(datetime.datetime.now(),
                           version=args.version or 'unknown',
                           log_dir=args.log_dir)
        print()

    sys.exit(0 if result_status == 'success' else 1)


if __name__ == '__main__':
    main()
