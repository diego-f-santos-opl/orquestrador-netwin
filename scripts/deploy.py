#!/usr/bin/env python3
# ==============================================================================
#   deploy.py — Aplica manifests no cluster Kubernetes
# ==============================================================================

import sys
import os
import argparse
import subprocess

# Contexto do cluster — injetado pelo netwin.py via env var KUBECONTEXT
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


def apply_file(filepath, namespace, dry_run=False):
    name = os.path.basename(filepath)
    cmd = kubectl('apply', '-f', filepath, '-n', namespace)
    if dry_run:
        cmd += ['--dry-run=client']

    result = run(cmd)

    if result.returncode == 0:
        print(f"  OK: {name}")
        if result.stdout:
            for line in result.stdout.strip().split('\n'):
                print(f"      {line}")
    else:
        print(f"  ERRO: {name}")
        if result.stderr:
            print(f"      {result.stderr.strip()}")

    return result.returncode == 0


def get_yaml_files(directory):
    files = []
    for f in sorted(os.listdir(directory)):
        if f.endswith('.yaml') or f.endswith('.yml'):
            files.append(os.path.join(directory, f))
    return files


def main():
    parser = argparse.ArgumentParser(description='Aplica manifests Kubernetes no cluster')
    parser.add_argument('--file', '-f', help='Aplicar um manifesto especifico')
    parser.add_argument('--dir',  '-d', default='manifests', help='Diretorio com os manifests')
    parser.add_argument('--namespace', '-n', default='netwin', help='Namespace (default: netwin)')
    parser.add_argument('--dry-run', action='store_true', help='Simular o deploy sem aplicar nada')
    args = parser.parse_args()

    # Verificar conexão
    result = krun('version', '--request-timeout=5s')
    if result.returncode != 0:
        print("ERRO: Nao foi possivel conectar ao cluster Kubernetes.")
        sys.exit(1)

    print(f"Namespace : {args.namespace}")
    print(f"Dry-run   : {'Sim' if args.dry_run else 'Nao'}")
    print()

    if args.file:
        if not os.path.exists(args.file):
            print(f"ERRO: Arquivo nao encontrado: {args.file}")
            sys.exit(1)
        print(f"Aplicando: {args.file}")
        ok = apply_file(args.file, args.namespace, args.dry_run)
        sys.exit(0 if ok else 1)

    files = get_yaml_files(args.dir)
    if not files:
        print(f"Nenhum arquivo yaml encontrado em: {args.dir}")
        sys.exit(1)

    print(f"Aplicando {len(files)} manifesto(s) de: {args.dir}\n")

    success = 0
    failed  = 0
    for filepath in files:
        ok = apply_file(filepath, args.namespace, args.dry_run)
        if ok: success += 1
        else:  failed  += 1

    print(f"\nResultado: {success} OK | {failed} com erro")
    if failed > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
