#!/usr/bin/env python3
# ==============================================================================
#
#   env-manager.py — Gerenciador de ambientes Netwin
#
#   Descricao : Permite registrar, editar, listar e remover ambientes.
#               Cada ambiente tem seu proprio namespace, cluster, registry,
#               banco Oracle e diretorio de manifests.
#   Uso       : python3 scripts/env-manager.py
#
#   Exemplos:
#     python3 scripts/env-manager.py --list
#     python3 scripts/env-manager.py --add
#     python3 scripts/env-manager.py --edit dev-interno
#     python3 scripts/env-manager.py --remove hml-dev
#
# ==============================================================================

import sys
import os
import argparse
import yaml

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
ENVS_DIR = os.path.join(BASE_DIR, 'environments')

GREEN  = '\033[92m'
YELLOW = '\033[93m'
RED    = '\033[91m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
DIM    = '\033[2m'
RESET  = '\033[0m'

def ok(msg):   print(f"  {GREEN}OK{RESET}    {msg}")
def err(msg):  print(f"  {RED}ERRO{RESET}  {msg}")
def info(msg): print(f"  {CYAN}>>>{RESET}  {msg}")


def ask(question, default=None):
    hint = f" [{default}]" if default else ""
    resp = input(f"  {YELLOW}?{RESET} {question}{hint}: ").strip()
    return resp if resp else default


def ask_yn(question, default='s'):
    hint = '[S/n]' if default == 's' else '[s/N]'
    resp = input(f"  {YELLOW}?{RESET} {question} {hint}: ").strip().lower()
    if not resp:
        return default == 's'
    return resp in ('s', 'sim', 'y', 'yes')


def list_envs():
    os.makedirs(ENVS_DIR, exist_ok=True)
    return sorted([f for f in os.listdir(ENVS_DIR) if f.endswith('.yaml')])


def load_env(name):
    path = os.path.join(ENVS_DIR, f"{name}.yaml")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def save_env(data):
    os.makedirs(ENVS_DIR, exist_ok=True)
    path = os.path.join(ENVS_DIR, f"{data['name']}.yaml")
    with open(path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return path


def print_env(env):
    w = 50
    db   = env.get('database', {})
    cl   = env.get('cluster', {})
    conn = f"{db.get('host','')}:{db.get('port','')}/{db.get('service','')}"

    print(f"\n  {CYAN}+{'─' * w}+{RESET}")
    print(f"  {CYAN}|{BOLD}  {env.get('label', env['name']):<{w-2}}{RESET}{CYAN}|{RESET}")
    print(f"  {CYAN}+{'─' * w}+{RESET}")
    fields = [
        ('Nome',          env.get('name', '')),
        ('Namespace',     env.get('namespace', '')),
        ('Cluster tipo',  cl.get('type', '')),
        ('Cluster host',  cl.get('host', '')),
        ('Context',       cl.get('context', '') or '(padrao)'),
        ('Registry',      env.get('registry', '')),
        ('Banco',         conn),
        ('DB Usuario',    db.get('user', '')),
        ('Manifests',     env.get('manifests_dir', '')),
    ]
    for label, value in fields:
        value = str(value) if value else f"{DIM}(nao configurado){RESET}"
        print(f"  {CYAN}|{RESET}  {label:<14} {value}")
    print(f"  {CYAN}+{'─' * w}+{RESET}")


def cmd_list():
    files = list_envs()
    if not files:
        print(f"\n  {YELLOW}Nenhum ambiente cadastrado.{RESET}\n")
        return
    print(f"\n  {BOLD}Ambientes cadastrados: {len(files)}{RESET}")
    for f in files:
        env = load_env(f.replace('.yaml', ''))
        if env:
            print_env(env)
    print()


def cmd_add():
    print(f"\n  {BOLD}Cadastrar novo ambiente{RESET}\n")

    name = ask("Nome do ambiente (ex: hml-dev)")
    if not name:
        err("Nome obrigatorio."); return

    name = name.lower().replace(' ', '-')
    if os.path.exists(os.path.join(ENVS_DIR, f"{name}.yaml")):
        err(f"Ambiente '{name}' ja existe. Use --edit para editar.")
        return

    label     = ask("Label de exibicao (ex: HML-DEV)", name.upper())
    namespace = ask("Namespace Kubernetes")
    c_type    = ask("Tipo do cluster", "rancher")
    c_host    = ask("Host do cluster")
    c_context = ask("Contexto kubectl (Enter para usar o atual)", "") or ""
    registry  = ask("Registry das imagens", "ghcr.io/alticelabsprojects")
    db_host   = ask("Banco Oracle - Host")
    db_port   = ask("Banco Oracle - Porta", "1521")
    db_svc    = ask("Banco Oracle - Service")
    db_user   = ask("Banco Oracle - Usuario", "NETWIN_SOL")
    db_pass   = ask("Banco Oracle - Senha") or ""
    mdir      = ask("Diretorio de manifests", f"manifests/{name}")

    data = {
        'name':      name,
        'label':     label,
        'cluster': {
            'type':    c_type,
            'host':    c_host or '',
            'context': c_context,
        },
        'namespace':  namespace or '',
        'registry':   registry,
        'database': {
            'host':     db_host or '',
            'port':     int(db_port) if db_port else 1521,
            'service':  db_svc or '',
            'user':     db_user,
            'password': db_pass,
        },
        'manifests_dir': mdir,
    }

    path = save_env(data)
    ok(f"Ambiente '{name}' salvo em: {path}")

    mdir_full = os.path.join(BASE_DIR, mdir)
    os.makedirs(mdir_full, exist_ok=True)
    ok(f"Diretorio de manifests criado: {mdir_full}")


def cmd_edit(name):
    env = load_env(name)
    if not env:
        err(f"Ambiente '{name}' nao encontrado.")
        return

    print(f"\n  {BOLD}Editando: {name}{RESET}")
    print(f"  {DIM}Pressione Enter para manter o valor atual{RESET}\n")

    env['label']                = ask("Label",         env.get('label', name))
    env['namespace']            = ask("Namespace",     env.get('namespace', ''))
    env['cluster']['type']      = ask("Cluster tipo",  env['cluster'].get('type', 'rancher'))
    env['cluster']['host']      = ask("Cluster host",  env['cluster'].get('host', ''))
    env['cluster']['context']   = ask("Context",       env['cluster'].get('context', '')) or ''
    env['registry']             = ask("Registry",      env.get('registry', ''))
    env['database']['host']     = ask("Banco host",    env['database'].get('host', ''))
    env['database']['port']     = int(ask("Banco porta", str(env['database'].get('port', 1521))))
    env['database']['service']  = ask("Banco service", env['database'].get('service', ''))
    env['database']['user']     = ask("Banco usuario", env['database'].get('user', ''))
    env['database']['password'] = ask("Banco senha",   env['database'].get('password', '')) or ''
    env['manifests_dir']        = ask("Manifests dir", env.get('manifests_dir', f'manifests/{name}'))

    path = save_env(env)
    ok(f"Ambiente '{name}' atualizado: {path}")

    mdir_full = os.path.join(BASE_DIR, env['manifests_dir'])
    os.makedirs(mdir_full, exist_ok=True)


def cmd_remove(name):
    env = load_env(name)
    if not env:
        err(f"Ambiente '{name}' nao encontrado.")
        return

    print_env(env)
    confirmar = ask_yn(f"\n  Confirma remover o ambiente '{name}'?", default='n')
    if not confirmar:
        info("Cancelado.")
        return

    os.remove(os.path.join(ENVS_DIR, f"{name}.yaml"))
    ok(f"Ambiente '{name}' removido.")


def main():
    parser = argparse.ArgumentParser(
        description='Gerenciador de ambientes Netwin',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
exemplos:
  python3 scripts/env-manager.py --list
  python3 scripts/env-manager.py --add
  python3 scripts/env-manager.py --edit hml-dev
  python3 scripts/env-manager.py --remove hml-dev
        """
    )
    parser.add_argument('--list',   '-l', action='store_true', help='Listar ambientes')
    parser.add_argument('--add',    '-a', action='store_true', help='Adicionar novo ambiente')
    parser.add_argument('--edit',   '-e', metavar='NOME',      help='Editar ambiente')
    parser.add_argument('--remove', '-r', metavar='NOME',      help='Remover ambiente')

    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.add:
        cmd_add()
    elif args.edit:
        cmd_edit(args.edit)
    elif args.remove:
        cmd_remove(args.remove)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
