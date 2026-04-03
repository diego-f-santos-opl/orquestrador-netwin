#!/usr/bin/env python3
# ==============================================================================
#
#   netwin.py — Orquestrador de Deploy
#
#   Descrição : Menu interativo para gerenciar o ambiente Netwin no Kubernetes
#   Autor     : Operador
#   Uso       : python3 netwin.py
#
#   Estrutura esperada:
#     netwin-deploy/
#     ├── netwin.py          <- este script
#     ├── manifests/         <- yamls dos deployments
#     ├── db-migrate/        <- manifesto do job de migrate
#     ├── backups/           <- backups por versao (criado automaticamente)
#     ├── logs/              <- logs do migrate (criado automaticamente)
#     └── scripts/           <- scripts individuais chamados por este menu
#         ├── startup.py
#         ├── shutdown.py
#         ├── deploy.py
#         ├── update-version.py
#         ├── migrate.py
#         ├── rollout.py
#         └── clean-manifests.py
#
#   Comandos disponiveis:
#     1 - Startup    : Sobe os componentes na ordem correta
#     2 - Shutdown   : Escala deployments para 0
#     3 - Clean      : Limpa campos gerados pelo Kubernetes nos yamls
#     4 - Update     : Atualiza versao nos manifests e faz backup automatico
#     5 - Deploy     : Aplica manifests no cluster
#     6 - Migrate    : Atualiza e executa o job de migrate
#     7 - Rollout    : Status / Reiniciar / Reverter deployments
#
# ==============================================================================

import sys
import os
import subprocess
import shutil
import yaml
import unicodedata

SCRIPTS_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
MANIFESTS_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'manifests')
DB_MIGRATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db-migrate')
LOGS_DIR       = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
NAMESPACE      = 'netwin'

GREEN   = '\033[92m'
YELLOW  = '\033[93m'
RED     = '\033[91m'
CYAN    = '\033[96m'
BLUE    = '\033[94m'
MAGENTA = '\033[95m'
WHITE   = '\033[97m'
BOLD    = '\033[1m'
DIM     = '\033[2m'
RESET   = '\033[0m'


def clear():
    os.system('clear' if os.name != 'nt' else 'cls')


def term_width():
    return min(shutil.get_terminal_size((80, 20)).columns - 4, 66)


def visual_len(text):
    """Calcula largura visual real: remove ANSI e conta chars wide como 2."""
    import re
    clean = re.sub(r'\033\[[0-9;]*m', '', text)
    total = 0
    for ch in clean:
        eaw = unicodedata.east_asian_width(ch)
        total += 2 if eaw in ('W', 'F') else 1
    return total


def pad_right(colored_text, plain_text, width):
    """Adiciona espaços para atingir width baseado no tamanho visual real."""
    used = visual_len(plain_text)
    return colored_text + ' ' * max(0, width - used)


def get_current_version():
    manifest = os.path.join(DB_MIGRATE_DIR, 'netwin-db-migrate.yaml')
    try:
        with open(manifest) as f:
            doc = yaml.safe_load(f)
        return doc.get('metadata', {}).get('labels', {}).get('app.kubernetes.io/version', 'n/a')
    except Exception:
        return 'n/a'


def header():
    w       = term_width()
    version = get_current_version()

    title1  = 'NETWIN'
    title2  = 'Orquestrador de Deploy'
    info1   = f'versao: {version}'
    info2   = f'namespace: {NAMESPACE}'

    print()
    print(f"  {CYAN}{BOLD}+{'=' * w}+{RESET}")
    print(f"  {CYAN}{BOLD}|{' ' * w}|{RESET}")
    print(f"  {CYAN}{BOLD}|{WHITE}{BOLD}{title1:^{w}}{CYAN}{BOLD}|{RESET}")
    print(f"  {CYAN}{BOLD}|{DIM}{title2:^{w}}{CYAN}{BOLD}|{RESET}")
    print(f"  {CYAN}{BOLD}|{' ' * w}|{RESET}")
    print(f"  {CYAN}{BOLD}+{'=' * w}+{RESET}")
    print(f"  {CYAN}|{GREEN}{BOLD}{info1:^{w}}{CYAN}|{RESET}")
    print(f"  {CYAN}|{YELLOW}{BOLD}{info2:^{w}}{CYAN}|{RESET}")
    print(f"  {CYAN}+{'-' * w}+{RESET}")
    print()


def draw_group(label, color, options):
    w = term_width()
    # titulo centralizado com tracejado
    inner = w - 2
    dash_total = inner - len(label) - 2
    ld = dash_total // 2
    rd = dash_total - ld
    print(f"  {DIM}+{'-' * ld} {color}{BOLD}{label}{RESET}{DIM} {'-' * rd}+{RESET}")

    for key, icon, name, desc, _ in options:
        # linha sem cores para medir
        plain = f" {key}  {icon}  {name:<10}  {desc} "
        pad   = w - visual_len(plain)
        # linha com cores
        row   = f" {YELLOW}{BOLD}{key}{RESET}  {WHITE}{icon}{RESET}  {BOLD}{WHITE}{name:<10}{RESET}  {DIM}{desc}{RESET} "
        print(f"  {DIM}|{RESET}{row}{' ' * pad}{DIM}|{RESET}")

    print(f"  {DIM}+{'-' * w}+{RESET}")
    print()


def print_menu():
    clear()
    header()
    for group in MENU_GROUPS:
        draw_group(group['label'], group['color'], group['options'])

    w = term_width()
    plain = " 0  x  Sair "
    pad   = w - visual_len(plain)
    row   = f" {RED}{BOLD}0{RESET}  {DIM}x  Sair{RESET} "
    print(f"  {DIM}+{'-' * w}+{RESET}")
    print(f"  {DIM}|{RESET}{row}{' ' * pad}{DIM}|{RESET}")
    print(f"  {DIM}+{'-' * w}+{RESET}")


def ask(question, default=None):
    hint = f"{DIM} [{default}]{RESET}" if default else ""
    resp = input(f"\n  {CYAN}>{RESET} {WHITE}{question}{RESET}{hint}: ").strip()
    return resp if resp else default


def ask_yn(question):
    while True:
        resp = input(f"  {CYAN}>{RESET} {WHITE}{question}{RESET} {DIM}[s/n]:{RESET} ").strip().lower()
        if resp in ('s', 'sim', 'y', 'yes'): return True
        if resp in ('n', 'nao', 'não', 'no'): return False


def pause():
    print()
    input(f"  {DIM}Pressione Enter para continuar...{RESET}")


def error(msg):   print(f"\n  {RED}{BOLD}[ERRO]{RESET} {msg}")
def info(msg):    print(f"\n  {CYAN}{BOLD}[INFO]{RESET} {msg}")


def run_script(script_name, args):
    script_path = os.path.join(SCRIPTS_DIR, script_name)
    if not os.path.exists(script_path):
        error(f"Script nao encontrado: {script_path}")
        pause(); return
    w = term_width()
    cmd = [sys.executable, script_path] + args
    print(f"\n  {DIM}{'-' * w}{RESET}")
    info(f"Executando: {CYAN}{script_name}{RESET}")
    print(f"  {DIM}{'-' * w}{RESET}\n")
    subprocess.run(cmd)
    pause()


# =============================================================
# Acoes
# =============================================================

def action_startup():
    clear(); header()
    print(f"  {CYAN}{BOLD}[ Startup ]{RESET}\n")
    print(f"  {DIM}Ordem: MongoDB > Zookeeper > LB > Wildfly > Backend > Geoserver > Tomcat > Frontend{RESET}\n")
    dry_run = ask_yn("Simular sem aplicar? (dry-run)")
    no_wait = ask_yn("Nao aguardar cada componente?")
    timeout = ask("Timeout por componente (seg)", "300")
    ns      = ask("Namespace", NAMESPACE)
    args = ['--dir', MANIFESTS_DIR, '--namespace', ns, '--timeout', timeout]
    if dry_run: args.append('--dry-run')
    if no_wait: args.append('--no-wait')
    run_script('startup.py', args)


def action_shutdown():
    clear(); header()
    print(f"  {RED}{BOLD}[ Shutdown ]{RESET}\n")
    print(f"  {YELLOW}1{RESET}  Desligar todos os deployments")
    print(f"  {YELLOW}2{RESET}  Desligar um componente especifico")
    print(f"  {DIM}0  Voltar{RESET}\n")
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


def action_clean():
    clear(); header()
    print(f"  {WHITE}{BOLD}[ Clean ]{RESET}\n")
    files = sorted([f for f in os.listdir(MANIFESTS_DIR) if f.endswith('.yaml')])
    if not files:
        error(f"Nenhum yaml em: {MANIFESTS_DIR}"); pause(); return
    print(f"  {DIM}Arquivos: {len(files)}{RESET}")
    for f in files:
        print(f"  {DIM}  - {f}{RESET}")
    if ask_yn(f"\n  Limpar todos os {len(files)} arquivo(s)?"):
        run_script('clean-manifests.py', [os.path.join(MANIFESTS_DIR, f) for f in files])


def action_update():
    clear(); header()
    print(f"  {BLUE}{BOLD}[ Update ]{RESET}\n")
    print(f"  {DIM}Versao atual: {GREEN}{get_current_version()}{RESET}\n")
    version = ask("Nova versao (ex: 1.0.6-r2)")
    if not version: error("Versao nao informada."); pause(); return
    ns      = ask("Namespace", NAMESPACE)
    dry_run = ask_yn("Simular sem aplicar? (dry-run)")
    args = ['--version', version, '--dir', MANIFESTS_DIR, '--namespace', ns]
    if dry_run: args.append('--dry-run')
    run_script('update-version.py', args)


def action_deploy():
    clear(); header()
    print(f"  {GREEN}{BOLD}[ Deploy ]{RESET}\n")
    print(f"  {YELLOW}1{RESET}  Aplicar todos os manifests")
    print(f"  {YELLOW}2{RESET}  Aplicar um manifest especifico")
    print(f"  {DIM}0  Voltar{RESET}\n")
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
        print()
        for i, f in enumerate(yamls, 1):
            print(f"  {YELLOW}{i:2}{RESET}  {f}")
        escolha = ask("\n  Numero do manifest")
        try:
            manifest = os.path.join(MANIFESTS_DIR, yamls[int(escolha) - 1])
        except (ValueError, IndexError):
            error("Opcao invalida."); pause(); return
        args = ['--file', manifest, '--namespace', ns]
        if dry_run: args.append('--dry-run')
        run_script('deploy.py', args)


def action_migrate():
    clear(); header()
    print(f"  {MAGENTA}{BOLD}[ Migrate ]{RESET}\n")
    print(f"  {DIM}Versao atual: {GREEN}{get_current_version()}{RESET}\n")
    version = ask("Nova versao (ex: 1.0.6-r2)")
    if not version: error("Versao nao informada."); pause(); return
    ns      = ask("Namespace", NAMESPACE)
    timeout = ask("Timeout em segundos", "600")
    dry_run = ask_yn("Simular sem aplicar? (dry-run)")
    manifest = os.path.join(DB_MIGRATE_DIR, 'netwin-db-migrate.yaml')
    args = ['--version', version, '--manifest', manifest,
            '--namespace', ns, '--timeout', timeout, '--log-dir', LOGS_DIR]
    if dry_run: args.append('--dry-run')
    run_script('migrate.py', args)


def action_rollout():
    clear(); header()
    print(f"  {CYAN}{BOLD}[ Rollout ]{RESET}\n")
    print(f"  {YELLOW}1{RESET}  Status de todos os deployments")
    print(f"  {YELLOW}2{RESET}  Status de um deployment especifico")
    print(f"  {DIM}  ---{RESET}")
    print(f"  {YELLOW}3{RESET}  Reiniciar todos os deployments")
    print(f"  {YELLOW}4{RESET}  Reiniciar um deployment especifico")
    print(f"  {DIM}  ---{RESET}")
    print(f"  {YELLOW}5{RESET}  Reverter todos para versao anterior")
    print(f"  {YELLOW}6{RESET}  Reverter um deployment especifico")
    print(f"  {DIM}0  Voltar{RESET}\n")
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


# =============================================================
# Grupos: key, icon, name, desc
# =============================================================

MENU_GROUPS = [
    {
        'label': 'Ambiente',
        'color': CYAN,
        'options': [
            ('1', '^', 'Startup',   'Subir componentes na ordem correta',       action_startup),
            ('2', 'v', 'Shutdown',  'Desligar componentes (scale 0)',            action_shutdown),
        ]
    },
    {
        'label': 'Manifests',
        'color': GREEN,
        'options': [
            ('3', '*', 'Clean',     'Limpar yamls exportados do cluster',        action_clean),
            ('4', '+', 'Update',    'Atualizar versao nos manifests',            action_update),
            ('5', '>', 'Deploy',    'Aplicar manifests no cluster',              action_deploy),
        ]
    },
    {
        'label': 'Job',
        'color': MAGENTA,
        'options': [
            ('6', '@', 'Migrate',   'Atualizar e executar job de migrate',       action_migrate),
        ]
    },
    {
        'label': 'Monitoramento',
        'color': BLUE,
        'options': [
            ('7', 'o', 'Rollout',   'Status / Reiniciar / Reverter deployments', action_rollout),
        ]
    },
]


def main():
    while True:
        print_menu()
        opt = ask("\n  Escolha uma opcao")

        if opt == '0':
            clear()
            print(f"\n  {CYAN}Ate logo!{RESET}\n")
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


if __name__ == '__main__':
    main()
