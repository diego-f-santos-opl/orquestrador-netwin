#!/usr/bin/env python3
# ==============================================================================
#
#   shutdown.py — Escala deployments para 0 (desliga componentes)
#
#   Descricao : Escala todos ou um componente especifico para 0 replicas.
#               MongoDB e Zookeeper sao mantidos no ar por padrao e so
#               sao desligados se --include-data for informado (com confirmacao).
#   Uso       : python3 scripts/shutdown.py --help
#
#   Exemplos:
#     python3 scripts/shutdown.py --all
#     python3 scripts/shutdown.py --all --include-data
#     python3 scripts/shutdown.py --name deployment/netwin-backend
#     python3 scripts/shutdown.py --name deployment/netwin-backend --name deployment/netwin-frontend
#     python3 scripts/shutdown.py --all --dry-run
#     python3 scripts/shutdown.py --all --namespace netwin
#
# ==============================================================================

import sys
import os
import argparse
import subprocess


# Contexto do cluster — injetado pelo netwin.py via env var KUBECONTEXT
KUBE_CONTEXT = os.environ.get('KUBECONTEXT', '')


def kubectl(*args):
    """Monta comando kubectl com --context se disponivel."""
    cmd = ['kubectl']
    if KUBE_CONTEXT:
        cmd += ['--context', KUBE_CONTEXT]
    cmd += list(args)
    return cmd


# =============================================================
# Componentes que NÃO são desligados por padrão
# (precisam de confirmação explícita)
# =============================================================
DATA_COMPONENTS = [
    {'kind': 'statefulset', 'name': 'netwin-mongo',     'label': 'MongoDB'},
    {'kind': 'statefulset', 'name': 'netwin-zookeeper', 'label': 'Zookeeper'},
]

# =============================================================
# Cores
# =============================================================
GREEN  = '\033[92m'
YELLOW = '\033[93m'
RED    = '\033[91m'
CYAN   = '\033[96m'
RESET  = '\033[0m'

def ok(msg):   print(f"  {GREEN}✔ {msg}{RESET}")
def warn(msg): print(f"  {YELLOW}⚠ {msg}{RESET}")
def err(msg):  print(f"  {RED}✘ {msg}{RESET}")
def info(msg): print(f"  {CYAN}→ {msg}{RESET}")


def run(args, capture=True):
    return subprocess.run(args, capture_output=capture, text=True)


def krun(*args, capture=True):
    """Atalho: executa kubectl com contexto automatico."""
    return run(kubectl(*args), capture=capture)


def get_replicas(kind, name, namespace):
    result = krun(
        'get', kind, name,
        '-n', namespace,
        '-o', 'jsonpath={.spec.replicas}'
    )
    try:
        return int(result.stdout.strip())
    except Exception:
        return 0


def scale_zero(kind, name, namespace, dry_run=False):
    replicas = get_replicas(kind, name, namespace)
    if replicas == 0:
        warn(f"{name} — já está em 0 réplicas")
        return True

    if dry_run:
        ok(f"[dry-run] {name} seria escalado de {replicas} → 0")
        return True

    result = krun(
        'scale', kind, name,
        '--replicas=0',
        '-n', namespace
    )
    if result.returncode == 0:
        ok(f"{name} — escalado de {replicas} → 0")
        return True
    else:
        err(f"{name} — {result.stderr.strip()}")
        return False


def get_all_deployments(namespace, instance='netwin'):
    result = krun(
        'get', 'deployments',
        '-n', namespace,
        '-l', f'app.kubernetes.io/instance={instance}',
        '-o', 'jsonpath={range .items[*]}{.metadata.name}\n{end}'
    )
    if result.returncode != 0:
        return []
    return [r for r in result.stdout.strip().split('\n') if r]


def ask_user(question):
    while True:
        resp = input(f"\n  {YELLOW}? {question} [s/n]: {RESET}").strip().lower()
        if resp in ('s', 'sim', 'y', 'yes'):
            return True
        if resp in ('n', 'nao', 'não', 'no'):
            return False


def main():
    parser = argparse.ArgumentParser(
        description='Escala deployments para 0 (desliga componentes)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
exemplos:
  Desligar todos os deployments (exceto MongoDB e Zookeeper):
    python3 shutdown.py --all

  Desligar todos incluindo MongoDB e Zookeeper (pergunta antes):
    python3 shutdown.py --all --include-data

  Desligar um componente especifico:
    python3 shutdown.py --name deployment/netwin-backend

  Desligar varios componentes:
    python3 shutdown.py --name deployment/netwin-backend --name deployment/netwin-frontend

  Simular sem aplicar:
    python3 shutdown.py --all --dry-run

  Especificar namespace:
    python3 shutdown.py --all --namespace netwin
        """
    )
    parser.add_argument(
        '--all', '-a',
        action='store_true',
        help='Desligar todos os deployments do namespace'
    )
    parser.add_argument(
        '--name', '-f',
        action='append',
        help='Componente especifico ex: deployment/netwin-backend (pode repetir)'
    )
    parser.add_argument(
        '--include-data',
        action='store_true',
        help='Incluir MongoDB e Zookeeper no shutdown (pergunta antes)'
    )
    parser.add_argument(
        '--namespace', '-n',
        default='netwin',
        help='Namespace do Kubernetes (default: netwin)'
    )
    parser.add_argument(
        '--instance',
        default='netwin',
        help='Label app.kubernetes.io/instance (default: netwin)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simular sem aplicar nada'
    )

    args = parser.parse_args()

    if not args.all and not args.name:
        parser.print_help()
        sys.exit(0)

    # Verificar conexão — usa 'version' pois 'cluster-info' requer permissão
    # de listar services no kube-system (pode falhar com Forbidden)
    result = krun('version', '--request-timeout=5s')
    if result.returncode != 0:
        err("Não foi possível conectar ao cluster Kubernetes.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Netwin Shutdown")
    print(f"{'='*60}")
    print(f"  Namespace : {args.namespace}")
    print(f"  Dry-run   : {'Sim' if args.dry_run else 'Não'}")
    print(f"{'='*60}\n")

    success = 0
    skipped = 0
    failed  = 0

    # Desligar componente(s) específico(s)
    if args.name:
        for resource in args.name:
            parts = resource.split('/')
            if len(parts) != 2:
                err(f"Formato inválido: {resource} — use kind/nome ex: deployment/netwin-backend")
                failed += 1
                continue
            kind, name = parts
            print(f"  {name}")
            ok_result = scale_zero(kind, name, args.namespace, args.dry_run)
            if ok_result:
                success += 1
            else:
                failed += 1
        print()

    # Desligar todos
    elif args.all:
        deployments = get_all_deployments(args.namespace, args.instance)
        if not deployments:
            warn("Nenhum deployment encontrado.")
            sys.exit(0)

        # Nomes dos componentes de dados para excluir da lista geral
        data_names = [d['name'] for d in DATA_COMPONENTS]

        print(f"Desligando {len(deployments)} deployment(s)...\n")
        for name in deployments:
            if name in data_names:
                continue  # tratado separadamente abaixo
            print(f"  {name}")
            ok_result = scale_zero('deployment', name, args.namespace, args.dry_run)
            if ok_result:
                success += 1
            else:
                failed += 1
            print()

        # Tratar MongoDB e Zookeeper separadamente
        if args.include_data:
            print(f"{'='*60}")
            print(f"  Componentes de dados (MongoDB / Zookeeper)")
            print(f"{'='*60}\n")
            for comp in DATA_COMPONENTS:
                print(f"  {comp['label']} ({comp['name']})")
                if not args.dry_run:
                    desligar = ask_user(f"Deseja desligar o {comp['label']}?")
                    if not desligar:
                        info("Mantido no ar")
                        skipped += 1
                        print()
                        continue
                ok_result = scale_zero(comp['kind'], comp['name'], args.namespace, args.dry_run)
                if ok_result:
                    success += 1
                else:
                    failed += 1
                print()
        else:
            print(f"  {YELLOW}⚠ MongoDB e Zookeeper mantidos no ar.{RESET}")
            print(f"  {CYAN}→ Use --include-data para incluí-los.{RESET}\n")
            skipped += len(DATA_COMPONENTS)

    # Resumo
    print(f"{'='*60}")
    print(f"  Resumo")
    print(f"{'='*60}")
    print(f"  {GREEN}✔ Desligados : {success}{RESET}")
    print(f"  {YELLOW}⚠ Mantidos   : {skipped}{RESET}")
    print(f"  {RED}✘ Falhas     : {failed}{RESET}")
    print(f"{'='*60}\n")

    if failed > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
