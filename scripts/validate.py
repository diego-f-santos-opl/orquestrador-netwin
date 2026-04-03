#!/usr/bin/env python3
# ==============================================================================
#   validate.py — Validação pré-deploy do ambiente Netwin
# ==============================================================================

import sys
import os
import argparse
import subprocess

MANIFESTS_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'manifests')
DB_MIGRATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'db-migrate')

KUBE_CONTEXT = os.environ.get('KUBECONTEXT', '')


def kubectl(*args):
    cmd = ['kubectl']
    if KUBE_CONTEXT:
        cmd += ['--context', KUBE_CONTEXT]
    cmd += list(args)
    return cmd


def run(args):
    return subprocess.run(args, capture_output=True, text=True)


def krun(*args):
    return run(kubectl(*args))


REQUIRED_SECRETS = [
    'netwin-credentials', 'vtal-ca', 'ghcr-secret',
    'mongo-key', 'netwin-web-certificate', 'netwin-zookeeper-jaas',
]

REQUIRED_CONFIGMAPS = [
    'netwin-deploy-conf', 'rsyslog-conf', 'netwin-mongod-conf',
    'netwin-zoo-cfg', 'netwin-zookeeper-config', 'netwin-zookeeper-init',
]

REQUIRED_SERVICEACCOUNTS = ['sa-nossis']

REQUIRED_MANIFESTS = [
    'netwin-backend.yaml', 'netwin-frontend.yaml',
    'netwin-wildfly.yaml', 'netwin-wildfly-feas.yaml', 'netwin-wildfly-prov.yaml',
    'netwin-geoserver.yaml', 'netwin-loadbalancer.yaml', 'netwin-loadbalancer-be.yaml',
    'netwin-tomcat.yaml', 'netwin-tomcat-prov.yaml',
]

GREEN  = '\033[92m'
YELLOW = '\033[93m'
RED    = '\033[91m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
DIM    = '\033[2m'
RESET  = '\033[0m'

def ok(msg):      print(f"  {GREEN}{BOLD}OK{RESET}    {msg}")
def fail(msg):    print(f"  {RED}{BOLD}FAIL{RESET}  {msg}")
def warn(msg):    print(f"  {YELLOW}{BOLD}WARN{RESET}  {msg}")
def info(msg):    print(f"  {CYAN}{BOLD}INFO{RESET}  {msg}")
def section(t):
    print(f"\n  {BOLD}{t}{RESET}")
    print(f"  {DIM}{'─' * 50}{RESET}")


def check_cluster(namespace):
    section("Conectividade com o cluster")
    # Usa 'version' pois 'cluster-info' requer permissão de listar services no kube-system
    result = krun('version', '--request-timeout=5s')
    if result.returncode != 0:
        fail("Cluster inacessivel — verifique o kubeconfig")
        return False
    ok("Cluster acessivel")

    result = krun('get', 'namespace', namespace)
    if result.returncode != 0:
        fail(f"Namespace '{namespace}' nao encontrado")
        return False
    ok(f"Namespace '{namespace}' existe")
    return True


def check_secrets(namespace):
    section("Secrets")
    errors = 0
    for secret in REQUIRED_SECRETS:
        result = krun('get', 'secret', secret, '-n', namespace)
        if result.returncode == 0: ok(secret)
        else: fail(f"{secret} — NAO ENCONTRADO"); errors += 1
    return errors == 0


def check_configmaps(namespace):
    section("ConfigMaps")
    errors = 0
    for cm in REQUIRED_CONFIGMAPS:
        result = krun('get', 'configmap', cm, '-n', namespace)
        if result.returncode == 0: ok(cm)
        else: fail(f"{cm} — NAO ENCONTRADO"); errors += 1
    return errors == 0


def check_serviceaccounts(namespace):
    section("ServiceAccounts")
    errors = 0
    for sa in REQUIRED_SERVICEACCOUNTS:
        result = krun('get', 'serviceaccount', sa, '-n', namespace)
        if result.returncode == 0: ok(sa)
        else: fail(f"{sa} — NAO ENCONTRADO"); errors += 1
    return errors == 0


def check_manifests(manifest_dir):
    section("Manifests locais")
    errors = 0

    if not os.path.exists(manifest_dir):
        fail(f"Diretorio nao encontrado: {manifest_dir}")
        return False

    for manifest in REQUIRED_MANIFESTS:
        path = os.path.join(manifest_dir, manifest)
        if os.path.exists(path): ok(manifest)
        else: fail(f"{manifest} — NAO ENCONTRADO em {manifest_dir}/"); errors += 1

    migrate = os.path.join(DB_MIGRATE_DIR, 'netwin-db-migrate.yaml')
    if os.path.exists(migrate): ok("netwin-db-migrate.yaml")
    else: warn("netwin-db-migrate.yaml — nao encontrado em db-migrate/")

    return errors == 0


def main():
    parser = argparse.ArgumentParser(description='Validacao pre-deploy do ambiente Netwin')
    parser.add_argument('--namespace', '-n', default='netwin', help='Namespace (default: netwin)')
    parser.add_argument('--dir', '-d', default=MANIFESTS_DIR, help='Diretorio com os manifests yaml')
    args = parser.parse_args()

    print(f"\n  {CYAN}{BOLD}{'=' * 52}{RESET}")
    print(f"  {CYAN}{BOLD}  Netwin — Validacao Pre-Deploy{RESET}")
    print(f"  {CYAN}{BOLD}{'=' * 52}{RESET}")
    print(f"\n  Namespace : {args.namespace}")
    print(f"  Manifests : {args.dir}")
    if KUBE_CONTEXT:
        print(f"  Context   : {KUBE_CONTEXT}")

    results = []

    cluster_ok = check_cluster(args.namespace)
    results.append(('Cluster e Namespace', cluster_ok))

    if not cluster_ok:
        print(f"\n  {RED}{BOLD}Cluster inacessivel — abortando validacao.{RESET}\n")
        sys.exit(1)

    results.append(('Secrets',         check_secrets(args.namespace)))
    results.append(('ConfigMaps',      check_configmaps(args.namespace)))
    results.append(('ServiceAccounts', check_serviceaccounts(args.namespace)))
    results.append(('Manifests locais',check_manifests(args.dir)))

    print(f"\n  {BOLD}{'=' * 52}{RESET}")
    print(f"  {BOLD}  Resumo{RESET}")
    print(f"  {DIM}{'─' * 52}{RESET}")

    all_ok = True
    for name, result in results:
        if result: print(f"  {GREEN}{BOLD}OK{RESET}    {name}")
        else: print(f"  {RED}{BOLD}FAIL{RESET}  {name}"); all_ok = False

    print(f"  {DIM}{'─' * 52}{RESET}")

    if all_ok:
        print(f"\n  {GREEN}{BOLD}Ambiente pronto para deploy!{RESET}\n")
        sys.exit(0)
    else:
        print(f"\n  {RED}{BOLD}Corrija os problemas antes de fazer deploy.{RESET}\n")
        sys.exit(1)


if __name__ == '__main__':
    main()
