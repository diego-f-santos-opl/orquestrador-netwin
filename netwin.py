#!/usr/bin/env python3
# ==============================================================================
#
#   netwin.py — Orquestrador de Deploy
#
#   Descrição : Menu interativo para gerenciar o ambiente Netwin no Kubernetes
#   Uso       : python3 netwin.py
#
#   Estrutura esperada:
#     netwin-deploy/
#     ├── netwin.py
#     ├── environments/        <- YAMLs de ambiente
#     ├── manifests/
#     ├── db-migrate/
#     ├── backups/
#     ├── logs/
#     └── scripts/
#         ├── startup.py
#         ├── shutdown.py
#         ├── deploy.py
#         ├── update-version.py
#         ├── migrate.py
#         ├── rollout.py
#         ├── validate.py
#         └── clean-manifests.py
#
# ==============================================================================

import sys
import os
import subprocess
import yaml

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns
    from rich.text import Text
    from rich.prompt import Prompt, Confirm
    from rich import box
    RICH = True
except ImportError:
    RICH = False
    print("AVISO: 'rich' nao instalado. Execute: pip install rich")
    print("       Continuando com modo texto simples...\n")

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR    = os.path.join(BASE_DIR, 'scripts')
DB_MIGRATE_DIR = os.path.join(BASE_DIR, 'db-migrate')
LOGS_DIR       = os.path.join(BASE_DIR, 'logs')
ENVS_DIR       = os.path.join(BASE_DIR, 'environments')

ACTIVE_ENV    = {}
MANIFESTS_DIR = os.path.join(BASE_DIR, 'manifests')
NAMESPACE     = 'netwin'

console = Console() if RICH else None


# ==============================================================================
# Helpers de UI
# ==============================================================================

def clear():
    os.system('clear' if os.name != 'nt' else 'cls')


def rprint(msg):
    if RICH:
        console.print(msg)
    else:
        import re
        print(re.sub(r'\[.*?\]', '', msg))


def ask(question, default=None):
    if RICH:
        hint = f" [[dim]{default}[/dim]]" if default else ""
        resp = Prompt.ask(f"\n  [cyan]>[/cyan] [white]{question}[/white]{hint}")
        return resp.strip() if resp.strip() else default
    else:
        hint = f" [{default}]" if default else ""
        resp = input(f"\n  > {question}{hint}: ").strip()
        return resp if resp else default


def ask_yn(question):
    if RICH:
        return Confirm.ask(f"  [cyan]>[/cyan] [white]{question}[/white]")
    else:
        while True:
            resp = input(f"  > {question} [s/n]: ").strip().lower()
            if resp in ('s', 'sim', 'y', 'yes'): return True
            if resp in ('n', 'nao', 'não', 'no'): return False


def pause():
    if RICH:
        console.print()
        Prompt.ask("  [dim]Pressione Enter para continuar[/dim]", default="")
    else:
        input("\n  Pressione Enter para continuar...")


def error(msg):
    if RICH:
        console.print(f"\n  [bold red][ERRO][/bold red] {msg}")
    else:
        print(f"\n  [ERRO] {msg}")


def info(msg):
    if RICH:
        console.print(f"\n  [bold cyan][INFO][/bold cyan] {msg}")
    else:
        print(f"\n  [INFO] {msg}")


# ==============================================================================
# Ambientes
# ==============================================================================

def load_environments():
    os.makedirs(ENVS_DIR, exist_ok=True)
    envs = []
    for f in sorted(os.listdir(ENVS_DIR)):
        if f.endswith('.yaml'):
            try:
                with open(os.path.join(ENVS_DIR, f)) as fh:
                    env = yaml.safe_load(fh)
                if env:
                    envs.append(env)
            except Exception:
                pass
    return envs


def select_environment():
    global ACTIVE_ENV, MANIFESTS_DIR, NAMESPACE

    envs = load_environments()
    if not envs:
        if RICH:
            console.print(Panel(
                "[red]Nenhum ambiente cadastrado![/red]\n"
                "[dim]Use: python3 scripts/env-manager.py --add[/dim]",
                border_style="red"
            ))
        else:
            print("\n  Nenhum ambiente cadastrado!")
        sys.exit(1)

    clear()

    if RICH:
        console.print()
        t = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
            border_style="cyan",
            title="[bold white]NETWIN[/bold white]  [dim]Orquestrador de Deploy[/dim]",
            title_style="",
            expand=False,
            min_width=70,
        )
        t.add_column("#",          style="bold yellow", width=4,  justify="center")
        t.add_column("Ambiente",   style="bold white",  min_width=16)
        t.add_column("Namespace",  style="dim",         min_width=28)
        t.add_column("Cluster",    style="dim",         min_width=10)
        t.add_column("Banco",      style="dim",         min_width=8)

        for i, env in enumerate(envs, 1):
            label  = env.get('label', env['name'])
            ns     = env.get('namespace', '')
            ctype  = env.get('cluster', {}).get('type', '')
            db_ok  = bool(env.get('database', {}).get('host'))
            db_st  = "[green]✔[/green]" if db_ok else "[yellow]?[/yellow]"
            t.add_row(str(i), label, ns, ctype, db_st)

        console.print(t)
        console.print()
        console.print("  [red]0[/red]  [dim]Sair[/dim]")
        console.print()
    else:
        print("\n  Selecione o ambiente:\n")
        for i, env in enumerate(envs, 1):
            label = env.get('label', env['name'])
            ns    = env.get('namespace', '')
            print(f"  {i}  {label:<20}  {ns}")
        print("\n  0  Sair\n")

    while True:
        try:
            resp = ask("Escolha").strip()
            if resp == '0':
                clear()
                rprint("\n  [cyan]Ate logo![/cyan]\n")
                sys.exit(0)
            idx = int(resp) - 1
            if 0 <= idx < len(envs):
                ACTIVE_ENV    = envs[idx]
                NAMESPACE     = ACTIVE_ENV.get('namespace', 'netwin')
                MANIFESTS_DIR = os.path.join(BASE_DIR, ACTIVE_ENV.get('manifests_dir', 'manifests'))
                os.makedirs(MANIFESTS_DIR, exist_ok=True)
                break
            else:
                rprint("  [red]Opcao invalida.[/red]")
        except (ValueError, KeyboardInterrupt):
            rprint("  [red]Opcao invalida.[/red]")


# ==============================================================================
# Header e Menu
# ==============================================================================

def get_migrate_manifest():
    env_name  = ACTIVE_ENV.get('name', 'dev-interno') if ACTIVE_ENV else 'dev-interno'
    candidate = os.path.join(DB_MIGRATE_DIR, env_name, 'netwin-db-migrate.yaml')
    if os.path.exists(candidate):
        return candidate
    fallback = os.path.join(DB_MIGRATE_DIR, 'netwin-db-migrate.yaml')
    if os.path.exists(fallback):
        return fallback
    return os.path.join('db-migrate', 'netwin-db-migrate.yaml')


def get_current_version():
    manifest = get_migrate_manifest()
    try:
        with open(manifest) as f:
            doc = yaml.safe_load(f)
        return doc.get('metadata', {}).get('labels', {}).get('app.kubernetes.io/version', 'n/a')
    except Exception:
        return 'n/a'


def print_header():
    version = get_current_version()
    label   = ACTIVE_ENV.get('label', '—') if ACTIVE_ENV else '—'

    if RICH:
        from rich.table import Table as _GridTable
        grid = _GridTable.grid(padding=(0, 2))
        grid.add_column(style="dim",    justify="right",  min_width=10)
        grid.add_column(style="bold",   justify="left",   min_width=24)
        grid.add_row("versao",    f"[cyan]{version}[/cyan]")
        grid.add_row("ambiente",  f"[green]{label}[/green]")
        grid.add_row("namespace", f"[yellow]{NAMESPACE}[/yellow]")
        console.print(Panel(
            grid,
            title="[bold white]NETWIN[/bold white]  [dim]Orquestrador de Deploy[/dim]",
            border_style="cyan",
            width=66,
            padding=(0, 2),
        ))
        console.print()
    else:
        print(f"\n  NETWIN  —  {label}  —  {NAMESPACE}  —  v{version}\n")
        print(f"  {'─' * 50}\n")


def print_menu():
    clear()
    print_header()

    if RICH:
        for group in MENU_GROUPS:
            t = Table(
                box=None,
                show_header=False,
                padding=(0, 1),
                expand=False,
                show_edge=False,
            )
            t.add_column("key",  style="bold yellow", width=3,  justify="right",  no_wrap=True)
            t.add_column("name", style="bold white",  width=13, no_wrap=True)
            t.add_column("desc", style="dim",         width=42, no_wrap=True)

            for key, icon, name, desc, _ in group["options"]:
                t.add_row(key, f"{icon}  {name}", desc)

            console.print(Panel(
                t,
                title=f"[bold {group['color']}]{group['label']}[/bold {group['color']}]",
                border_style=group["color"],
                width=66,
                expand=False,
                padding=(0, 1),
            ))

        console.print("  [red bold]0[/red bold]  [dim]Sair[/dim]\n")
    else:
        for group in MENU_GROUPS:
            print(f"  --- {group['label']} ---")
            for key, icon, name, desc, _ in group['options']:
                print(f"  {key:<4} {name:<12}  {desc}")
            print()
        print("  0    Sair\n")


# ==============================================================================
# run_script — injeta KUBECONTEXT + KUBECONFIG
# ==============================================================================

def run_script(script_name, args):
    script_path = os.path.join(SCRIPTS_DIR, script_name)
    if not os.path.exists(script_path):
        error(f"Script nao encontrado: {script_path}")
        pause(); return

    cmd = [sys.executable, script_path] + args

    if RICH:
        console.print(f"\n  [dim]{'─' * 60}[/dim]")
        console.print(f"  [bold cyan][INFO][/bold cyan] Executando: [cyan]{script_name}[/cyan]")
        console.print(f"  [dim]{'─' * 60}[/dim]\n")
    else:
        print(f"\n  {'─' * 60}")
        print(f"  [INFO] Executando: {script_name}")
        print(f"  {'─' * 60}\n")

    # Propagar contexto, kubeconfig e banco do ambiente ativo para os scripts filhos
    env = os.environ.copy()
    if ACTIVE_ENV:
        kube_context = ACTIVE_ENV.get('cluster', {}).get('context', '')
        kube_config  = ACTIVE_ENV.get('cluster', {}).get('kubeconfig', '')
        if kube_context:
            env['KUBECONTEXT'] = kube_context
        if kube_config:
            env['KUBECONFIG'] = os.path.expanduser(kube_config)
        db = ACTIVE_ENV.get('database', {})
        if db.get('host'):
            env['DB_HOST']     = str(db.get('host', ''))
            env['DB_PORT']     = str(db.get('port', 1521))
            env['DB_SERVICE']  = str(db.get('service', ''))
            env['DB_USER']     = str(db.get('user', ''))
            env['DB_PASSWORD'] = str(db.get('password', ''))
        # Startup automatico — replicas por componente
        startup_cfg = ACTIVE_ENV.get('startup', {})
        if startup_cfg:
            import json
            env['STARTUP_REPLICAS']         = json.dumps(startup_cfg.get('components', {}))
            env['STARTUP_DEFAULT_REPLICAS'] = str(startup_cfg.get('defaults', {}).get('replicas', 1))
        env['ENV_NAME']         = ACTIVE_ENV.get('name', 'env')
        env['LOG_DIR']          = os.path.join(LOGS_DIR, ACTIVE_ENV.get('name', 'env'))
        env['MIGRATE_MANIFEST'] = get_migrate_manifest()

    subprocess.run(cmd, env=env)
    pause()


# ==============================================================================
# Acoes
# ==============================================================================

def action_startup():
    clear(); print_header()
    rprint("  [cyan bold][ Startup ][/cyan bold]\n")
    rprint("  [dim]Ordem: MongoDB > Zookeeper > LB > Wildfly > Backend > Geoserver > Tomcat > Frontend[/dim]\n")
    rprint("  [yellow]1[/yellow]  Startup interativo   [dim](pergunta replicas de cada componente)[/dim]")
    rprint("  [yellow]2[/yellow]  Startup automatico   [dim](usa replicas definidas no ambiente)[/dim]")
    rprint("  [dim]0  Voltar[/dim]\n")
    opt = ask("Escolha")
    if opt == '0' or not opt: return

    ns      = ask("Namespace", NAMESPACE)
    dry_run = ask_yn("Simular sem aplicar? (dry-run)")
    timeout = ask("Timeout por componente (seg)", "300")

    if opt == '1':
        no_wait = ask_yn("Nao aguardar cada componente?")
        args = ['--dir', MANIFESTS_DIR, '--namespace', ns, '--timeout', timeout]
        if dry_run: args.append('--dry-run')
        if no_wait: args.append('--no-wait')
        run_script('startup.py', args)

    elif opt == '2':
        # Verificar se o ambiente tem startup configurado
        startup_cfg = ACTIVE_ENV.get('startup', {}) if ACTIVE_ENV else {}
        if not startup_cfg:
            error("Ambiente nao possui bloco 'startup' configurado.")
            rprint("  [dim]Adicione em environments/<env>.yaml:[/dim]")
            rprint("  [dim]startup:[/dim]")
            rprint("  [dim]  defaults:[/dim]")
            rprint("  [dim]    replicas: 1[/dim]")
            rprint("  [dim]  components:[/dim]")
            rprint("  [dim]    netwin-wildfly: 2[/dim]")
            pause(); return

        # Mostrar replicas configuradas
        rprint("\n  [dim]Replicas configuradas:[/dim]")
        components = startup_cfg.get('components', {})
        default_r  = startup_cfg.get('defaults', {}).get('replicas', 1)
        for comp, r in components.items():
            rprint(f"  [dim]  {comp:<35}[/dim] [cyan]{r}[/cyan]")
        rprint(f"  [dim]  {'(padrao)':<35}[/dim] [cyan]{default_r}[/cyan]")
        console.print() if RICH else print()

        args = ['--auto', '--dir', MANIFESTS_DIR, '--namespace', ns, '--timeout', timeout]
        if dry_run: args.append('--dry-run')
        run_script('startup.py', args)


def action_shutdown():
    clear(); print_header()
    rprint("  [red bold][ Shutdown ][/red bold]\n")
    rprint("  [yellow]1[/yellow]  Desligar todos os deployments")
    rprint("  [yellow]2[/yellow]  Desligar um componente especifico")
    rprint("  [dim]0  Voltar[/dim]\n")
    opt = ask("Escolha")
    if opt == '0' or not opt: return
    ns      = ask("Namespace", NAMESPACE)
    dry_run = ask_yn("Simular sem aplicar? (dry-run)")
    if opt == '1':
        include_data = ask_yn("Incluir MongoDB e Zookeeper?")
        args = ['--all', '--namespace', ns]
        if include_data: args.append('--include-data')
        if dry_run: args.append('--dry-run')
        run_script('shutdown.py', args)
    elif opt == '2':
        name = ask("Nome (ex: deployment/netwin-backend)")
        if not name: error("Nome nao informado."); pause(); return
        args = ['--name', name, '--namespace', ns]
        if dry_run: args.append('--dry-run')
        run_script('shutdown.py', args)


def action_validate():
    clear(); print_header()
    rprint("  [green bold][ Validate ][/green bold]\n")
    rprint("  [dim]Verifica cluster, namespace, secrets, configmaps e manifests[/dim]\n")
    ns = ask("Namespace", NAMESPACE)
    run_script('validate.py', ['--namespace', ns, '--dir', MANIFESTS_DIR])


def action_clean():
    clear(); print_header()
    rprint("  [white bold][ Clean ][/white bold]\n")

    # Coletar yamls de manifests/
    manifest_files = sorted([
        os.path.join(MANIFESTS_DIR, f)
        for f in os.listdir(MANIFESTS_DIR) if f.endswith('.yaml')
    ]) if os.path.isdir(MANIFESTS_DIR) else []

    # Coletar yamls de db-migrate/<env>/
    db_migrate_env_dir = os.path.join(DB_MIGRATE_DIR, ACTIVE_ENV.get('name', '')) if ACTIVE_ENV else ''
    db_files = sorted([
        os.path.join(db_migrate_env_dir, f)
        for f in os.listdir(db_migrate_env_dir) if f.endswith('.yaml')
    ]) if db_migrate_env_dir and os.path.isdir(db_migrate_env_dir) else []

    all_files = manifest_files + db_files

    if not all_files:
        error(f"Nenhum yaml encontrado em: {MANIFESTS_DIR}"); pause(); return

    rprint(f"  [dim]manifests/  : {len(manifest_files)} arquivo(s)[/dim]")
    for f in manifest_files:
        rprint(f"  [dim]    - {os.path.basename(f)}[/dim]")
    if db_files:
        rprint(f"  [dim]db-migrate/ : {len(db_files)} arquivo(s)[/dim]")
        for f in db_files:
            rprint(f"  [dim]    - {os.path.basename(f)}[/dim]")

    if ask_yn(f"\n  Limpar {len(all_files)} arquivo(s)?"):
        run_script('clean-manifests.py', all_files)


def action_update():
    clear(); print_header()
    rprint("  [blue bold][ Update ][/blue bold]\n")
    rprint(f"  [dim]Versao atual: [/dim][green]{get_current_version()}[/green]\n")
    version = ask("Nova versao (ex: 1.0.6-r2)")
    if not version: error("Versao nao informada."); pause(); return
    ns      = ask("Namespace", NAMESPACE)
    dry_run = ask_yn("Simular sem aplicar? (dry-run)")
    args = ['--version', version, '--dir', MANIFESTS_DIR, '--namespace', ns]
    if dry_run: args.append('--dry-run')
    run_script('update-version.py', args)


def action_deploy():
    clear(); print_header()
    rprint("  [green bold][ Deploy ][/green bold]\n")
    rprint("  [yellow]1[/yellow]  Aplicar todos os manifests")
    rprint("  [yellow]2[/yellow]  Aplicar um manifest especifico")
    rprint("  [dim]0  Voltar[/dim]\n")
    opt = ask("Escolha")
    if opt == '0' or not opt: return
    ns      = ask("Namespace", NAMESPACE)
    dry_run = ask_yn("Simular sem aplicar? (dry-run)")
    if opt == '1':
        args = ['--dir', MANIFESTS_DIR, '--namespace', ns]
        if dry_run: args.append('--dry-run')
        run_script('deploy.py', args)
    elif opt == '2':
        yamls = sorted([f for f in os.listdir(MANIFESTS_DIR) if f.endswith('.yaml')])
        if not yamls:
            error(f"Nenhum manifest em: {MANIFESTS_DIR}"); pause(); return
        console.print() if RICH else print()
        for i, f in enumerate(yamls, 1):
            rprint(f"  [yellow]{i:2}[/yellow]  {f}")
        escolha = ask("\n  Numero do manifest")
        try:
            manifest = os.path.join(MANIFESTS_DIR, yamls[int(escolha) - 1])
        except (ValueError, IndexError):
            error("Opcao invalida."); pause(); return
        args = ['--file', manifest, '--namespace', ns]
        if dry_run: args.append('--dry-run')
        run_script('deploy.py', args)


def action_migrate():
    clear(); print_header()
    rprint("  [magenta bold][ Migrate ][/magenta bold]\n")
    env_name = ACTIVE_ENV.get('name', 'dev-interno') if ACTIVE_ENV else 'dev-interno'
    rprint(f"  [dim]Ambiente  : [/dim][yellow]{env_name}[/yellow]")
    rprint(f"  [dim]Versao    : [/dim][green]{get_current_version()}[/green]\n")
    version = ask("Nova versao (ex: 1.0.6-r2)")
    if not version: error("Versao nao informada."); pause(); return
    ns      = ask("Namespace", NAMESPACE)
    timeout = ask("Timeout em segundos", "600")
    dry_run = ask_yn("Simular sem aplicar? (dry-run)")
    manifest = get_migrate_manifest()
    args = ['--version', version, '--manifest', manifest,
            '--namespace', ns, '--timeout', timeout, '--log-dir', LOGS_DIR]
    if dry_run: args.append('--dry-run')
    run_script('migrate.py', args)


def action_validate_migrate():
    clear(); print_header()
    rprint("  [magenta bold][ Validate Migrate ][/magenta bold]\n")
    rprint("  [dim]Consulta DATABASE_SCHEMA_UPDATES no banco Oracle[/dim]")
    rprint("  [dim]filtrando pelos registros dos ultimos 2 dias.[/dim]\n")
    ns       = ask("Namespace", NAMESPACE)
    manifest = get_migrate_manifest()
    run_script('migrate.py', ['--validate-only', '--manifest', manifest, '--namespace', ns])


def action_rollout():
    clear(); print_header()
    rprint("  [cyan bold][ Rollout ][/cyan bold]\n")
    rprint("  [yellow]1[/yellow]  Status de todos os deployments")
    rprint("  [yellow]2[/yellow]  Status de um deployment especifico")
    rprint("  [dim]  ---[/dim]")
    rprint("  [yellow]3[/yellow]  Reiniciar todos os deployments")
    rprint("  [yellow]4[/yellow]  Reiniciar um deployment especifico")
    rprint("  [dim]  ---[/dim]")
    rprint("  [yellow]5[/yellow]  Reverter todos para versao anterior")
    rprint("  [yellow]6[/yellow]  Reverter um deployment especifico")
    rprint("  [dim]0  Voltar[/dim]\n")
    opt = ask("Escolha")
    if opt == '0' or not opt: return
    ns = ask("Namespace", NAMESPACE)
    if opt == '1':
        run_script('rollout.py', ['--status', '--namespace', ns])
    elif opt == '2':
        name = ask("Nome (ex: deployment/netwin-backend)")
        if name: run_script('rollout.py', ['--status', '--name', name, '--namespace', ns])
    elif opt == '3':
        if ask_yn("Confirma reiniciar TODOS os deployments?"):
            run_script('rollout.py', ['--restart', '--namespace', ns])
    elif opt == '4':
        name = ask("Nome (ex: deployment/netwin-backend)")
        if name: run_script('rollout.py', ['--restart', '--name', name, '--namespace', ns])
    elif opt == '5':
        if ask_yn("Confirma reverter TODOS para versao anterior?"):
            run_script('rollout.py', ['--undo', '--namespace', ns])
    elif opt == '6':
        name = ask("Nome (ex: deployment/netwin-backend)")
        if name: run_script('rollout.py', ['--undo', '--name', name, '--namespace', ns])


def action_logs():
    clear(); print_header()
    rprint("  [cyan bold][ Logs ][/cyan bold]\n")

    result = subprocess.run(
        ['kubectl', 'get', 'pods', '-n', NAMESPACE,
         '-o', 'jsonpath={range .items[*]}{.metadata.name}\n{end}'],
        capture_output=True, text=True
    )
    if result.returncode != 0 or not result.stdout.strip():
        rprint(f"  [red]Nenhum pod encontrado no namespace {NAMESPACE}[/red]")
        pause(); return

    pods = [p for p in result.stdout.strip().split('\n') if p]

    if RICH:
        t = Table(box=box.SIMPLE, show_header=False, padding=(0,1))
        t.add_column("#",    style="yellow", width=4, justify="right")
        t.add_column("pod",  style="white")
        for i, pod in enumerate(pods, 1):
            t.add_row(str(i), pod)
        console.print(t)
    else:
        for i, pod in enumerate(pods, 1):
            print(f"  {i:2}  {pod}")

    escolha = ask("\n  Numero do pod")
    try:
        pod = pods[int(escolha) - 1]
    except (ValueError, IndexError):
        error("Opcao invalida."); pause(); return

    rprint(f"\n  [bold]Opcoes de log:[/bold]")
    rprint("  [yellow]1[/yellow]  Ultimas 50 linhas")
    rprint("  [yellow]2[/yellow]  Ultimas 100 linhas")
    rprint("  [yellow]3[/yellow]  Seguir em tempo real (Ctrl+C para sair)")
    rprint("  [dim]0  Voltar[/dim]")
    opt = ask("Escolha", "1")

    rprint(f"\n  [dim]{'─' * 60}[/dim]")
    rprint(f"  [cyan bold][INFO][/cyan bold] Logs do pod: [cyan]{pod}[/cyan]")
    rprint(f"  [dim]{'─' * 60}[/dim]\n")

    try:
        if opt == '1':
            subprocess.run(['kubectl', 'logs', pod, '-n', NAMESPACE, '--tail=50'])
        elif opt == '2':
            subprocess.run(['kubectl', 'logs', pod, '-n', NAMESPACE, '--tail=100'])
        elif opt == '3':
            rprint("  [dim]Ctrl+C para voltar ao menu...[/dim]\n")
            subprocess.run(['kubectl', 'logs', pod, '-n', NAMESPACE, '-f'])
    except KeyboardInterrupt:
        rprint("\n\n  [yellow]Interrompido — voltando ao menu...[/yellow]")
    pause()


def action_export():
    clear(); print_header()
    rprint("  [cyan bold][ Export ][/cyan bold]\n")
    rprint("  [dim]Exporta os yamls dos deployments do cluster para manifests/[/dim]\n")

    ns = ask("Namespace", NAMESPACE)

    result = subprocess.run(
        ['kubectl', 'get', 'deployments', '-n', ns,
         '-o', 'jsonpath={range .items[*]}{.metadata.name}\n{end}'],
        capture_output=True, text=True
    )
    if result.returncode != 0 or not result.stdout.strip():
        error(f"Nenhum deployment encontrado no namespace {ns}")
        pause(); return

    deployments = [d for d in result.stdout.strip().split('\n') if d]
    rprint(f"  [bold]Deployments encontrados: {len(deployments)}[/bold]")
    for d in deployments:
        rprint(f"  [dim]  • {d}[/dim]")

    if not ask_yn(f"\n  Exportar {len(deployments)} deployment(s) para manifests/?"):
        rprint("  [dim]Cancelado.[/dim]"); pause(); return

    os.makedirs(MANIFESTS_DIR, exist_ok=True)
    success = 0
    failed  = 0

    for deploy in deployments:
        output_file = os.path.join(MANIFESTS_DIR, f"{deploy}.yaml")
        result = subprocess.run(
            ['kubectl', 'get', 'deployment', deploy, '-n', ns, '-o', 'yaml'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            with open(output_file, 'w') as f:
                f.write(result.stdout)
            rprint(f"  [green]OK[/green]    {deploy}.yaml")
            success += 1
        else:
            rprint(f"  [red]ERRO[/red]  {deploy} — {result.stderr.strip()}")
            failed += 1

    rprint(f"\n  [green]Exportados : {success}[/green]")
    if failed:
        rprint(f"  [red]Falhas     : {failed}[/red]")
    rprint(f"\n  [dim]Arquivos salvos em: {MANIFESTS_DIR}[/dim]")
    pause()


# ==============================================================================
# Grupos de menu
# ==============================================================================

MENU_GROUPS = [
    {
        'label': 'Ambiente',
        'color': 'cyan',
        'options': [
            ('1',  '^', 'Startup',    'Subir componentes na ordem correta',        action_startup),
            ('2',  'v', 'Shutdown',   'Desligar componentes (scale 0)',             action_shutdown),
        ]
    },
    {
        'label': 'Manifests',
        'color': 'green',
        'options': [
            ('3',  '?', 'Validate',   'Validar ambiente antes do deploy',           action_validate),
            ('4',  '*', 'Clean',      'Limpar yamls exportados do cluster',         action_clean),
            ('5',  '+', 'Update',     'Atualizar versao nos manifests',             action_update),
            ('6',  '>', 'Deploy',     'Aplicar manifests no cluster',               action_deploy),
        ]
    },
    {
        'label': 'Job',
        'color': 'magenta',
        'options': [
            ('7',  '@', 'Migrate',    'Atualizar e executar job de migrate',        action_migrate),
            ('8',  '?', 'Val.Migr',   'Validar execucao do migrate no banco',       action_validate_migrate),
        ]
    },
    {
        'label': 'Monitoramento',
        'color': 'blue',
        'options': [
            ('9',  'o', 'Rollout',    'Status / Reiniciar / Reverter deployments',  action_rollout),
            ('10', '~', 'Logs',       'Ver logs dos pods no cluster',               action_logs),
            ('11', '^', 'Export',     'Exportar yamls do cluster para manifests/',  action_export),
        ]
    },
]


# ==============================================================================
# Main
# ==============================================================================

def main():
    select_environment()
    while True:
        try:
            print_menu()
            opt = ask("\n  Escolha uma opcao")

            if opt == '0':
                clear()
                rprint("\n  [cyan]Ate logo![/cyan]\n")
                sys.exit(0)

            found = False
            for group in MENU_GROUPS:
                for key, icon, name, desc, action in group['options']:
                    if opt == key:
                        action()
                        found = True
                        break
                if found:
                    break

            if not found:
                error("Opcao invalida.")
                pause()

        except KeyboardInterrupt:
            rprint("\n\n  [yellow]Ctrl+C detectado — voltando ao menu...[/yellow]")
            import time; time.sleep(1)
            continue


if __name__ == '__main__':
    main()
