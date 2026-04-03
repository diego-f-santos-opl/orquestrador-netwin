#!/usr/bin/env python3
# ==============================================================================
#   rollout.py — Monitora e gerencia rollout de Deployments/StatefulSets
# ==============================================================================

import sys
import os
import argparse
import subprocess

KUBE_CONTEXT = os.environ.get('KUBECONTEXT', '')


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


def get_deployments(namespace):
    result = krun(
        'get', 'deployments,statefulsets',
        '-n', namespace,
        '-o', 'jsonpath={range .items[*]}{.kind}/{.metadata.name}\n{end}'
    )
    if result.returncode != 0:
        print(f"ERRO ao listar recursos: {result.stderr}")
        sys.exit(1)
    return [r for r in result.stdout.strip().split('\n') if r]


def rollout_status(resource, namespace):
    kind, name = resource.split('/')
    result = krun(
        'get', kind, name, '-n', namespace,
        '-o', 'jsonpath={.metadata.name}|{.spec.replicas}|{.status.readyReplicas}|{.status.updatedReplicas}|{.status.availableReplicas}'
    )
    if result.returncode != 0:
        print(f"  ERRO: {resource} nao encontrado")
        return False

    parts     = result.stdout.strip().split('|')
    name_val  = parts[0] if len(parts) > 0 else '?'
    desired   = (parts[1] if len(parts) > 1 else '0') or '0'
    ready     = (parts[2] if len(parts) > 2 else '0') or '0'
    updated   = (parts[3] if len(parts) > 3 else '0') or '0'
    available = (parts[4] if len(parts) > 4 else '0') or '0'

    if desired == '0':
        status = 'PARADO'
    elif ready == desired:
        status = 'OK'
    else:
        status = 'PENDENTE'

    print(f"  {name_val:<35} desired={desired}  ready={ready}  updated={updated}  available={available}  [{status}]")
    return True


def get_replicas(resource, namespace):
    kind, name = resource.split('/')
    result = krun('get', kind, name, '-n', namespace, '-o', 'jsonpath={.spec.replicas}')
    try:
        return int(result.stdout.strip())
    except Exception:
        return 0


def rollout_restart(resource, namespace):
    replicas = get_replicas(resource, namespace)
    if replicas == 0:
        print(f"  SKIP: {resource} — replicas=0, deployment parado (use startup.py para iniciar)")
        return False
    print(f"  Reiniciando {resource}...")
    result = krun('rollout', 'restart', resource, '-n', namespace)
    if result.returncode == 0:
        print(f"  OK: {resource}")
    else:
        print(f"  ERRO: {resource} → {result.stderr.strip()}")
    return result.returncode == 0


def rollout_undo(resource, namespace):
    print(f"  Revertendo {resource}...")
    result = krun('rollout', 'undo', resource, '-n', namespace)
    if result.returncode == 0:
        print(f"  OK: {resource}")
    else:
        print(f"  ERRO: {resource} → {result.stderr.strip()}")
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description='Gerencia rollout de Deployments e StatefulSets')
    parser.add_argument('--status',  '-s', action='store_true', help='Ver status do rollout')
    parser.add_argument('--restart', '-r', action='store_true', help='Reiniciar deployments')
    parser.add_argument('--undo',    '-u', action='store_true', help='Reverter para versao anterior')
    parser.add_argument('--name',    '-f', help='Recurso especifico ex: deployment/netwin-backend')
    parser.add_argument('--namespace', '-n', default='netwin', help='Namespace (default: netwin)')
    args = parser.parse_args()

    if not any([args.status, args.restart, args.undo]):
        parser.print_help(); sys.exit(0)

    # Verificar conexão
    result = krun('version', '--request-timeout=5s')
    if result.returncode != 0:
        print("ERRO: Nao foi possivel conectar ao cluster Kubernetes.")
        sys.exit(1)

    print(f"Namespace : {args.namespace}")

    if args.name:
        resources = [args.name]
    else:
        resources = get_deployments(args.namespace)
        if not resources:
            print("Nenhum Deployment ou StatefulSet encontrado.")
            sys.exit(0)
        print(f"Recursos  : {len(resources)} encontrados")

    print()

    if args.status:
        print("Status do rollout:")
        for r in resources:
            rollout_status(r, args.namespace)
    elif args.restart:
        print("Reiniciando deployments:")
        for r in resources:
            rollout_restart(r, args.namespace)
    elif args.undo:
        print("Revertendo deployments:")
        for r in resources:
            rollout_undo(r, args.namespace)


if __name__ == '__main__':
    main()
