#!/usr/bin/env python3
# ==============================================================================
#
#   clean-manifests.py — Limpa campos gerados pelo Kubernetes nos yamls
#
#   Descricao : Remove campos gerados automaticamente pelo Kubernetes dos
#               arquivos yaml exportados com kubectl get ... -o yaml.
#               Gera arquivos com sufixo -clean.yaml sem alterar o original.
#   Uso       : python3 scripts/clean-manifests.py manifests/*.yaml
#
#   O que remove:
#     - resourceVersion, uid, creationTimestamp, generation
#     - kubectl.kubernetes.io/last-applied-configuration
#     - deployment.kubernetes.io/revision
#     - terminationMessagePath, terminationMessagePolicy
#     - schedulerName, dnsPolicy, restartPolicy
#     - serviceAccount (duplicado do serviceAccountName)
#     - status
#
#   Exemplos:
#     python3 scripts/clean-manifests.py manifests/netwin-backend.yaml
#     python3 scripts/clean-manifests.py manifests/*.yaml
#
# ==============================================================================

import sys
import yaml
import os

FIELDS_TO_REMOVE_METADATA = [
    'resourceVersion',
    'uid',
    'creationTimestamp',
    'generation',
    'managedFields',
    'ownerReferences',
    'finalizers',
    'selfLink',
]

ANNOTATIONS_TO_REMOVE = [
    'kubectl.kubernetes.io/last-applied-configuration',
    'deployment.kubernetes.io/revision',
    'field.cattle.io/publicEndpoints',
    'kubectl.kubernetes.io/restartedAt',
]

CONTAINER_FIELDS_TO_REMOVE = [
    'terminationMessagePath',
    'terminationMessagePolicy',
]

SPEC_FIELDS_TO_REMOVE = [
    'schedulerName',
    'dnsPolicy',
    'restartPolicy',
    'terminationGracePeriodSeconds',
    'serviceAccount',  # manter serviceAccountName, remover serviceAccount (duplicado)
]

# Campos do spec do Job gerados automaticamente pelo controller
JOB_SPEC_FIELDS_TO_REMOVE = [
    'selector',        # gerado automaticamente pelo controller
    'completionMode',  # default NonIndexed
    'parallelism',     # default 1
    'suspend',         # default false
]

# Labels injetadas pelo controller no template do Job
JOB_TEMPLATE_LABELS_TO_REMOVE = [
    'batch.kubernetes.io/controller-uid',
    'batch.kubernetes.io/job-name',
    'controller-uid',
    'job-name',
]

# Annotations injetadas pelo Job tracking
JOB_ANNOTATIONS_TO_REMOVE = [
    'batch.kubernetes.io/job-tracking',
]

# defaultMode em volumes (420 = 0644, valor default — desnecessario)
VOLUME_FIELDS_TO_REMOVE = [
    'defaultMode',
]


def clean_metadata(metadata):
    for field in FIELDS_TO_REMOVE_METADATA:
        metadata.pop(field, None)

    annotations = metadata.get('annotations', {})
    for ann in ANNOTATIONS_TO_REMOVE:
        annotations.pop(ann, None)
    if not annotations:
        metadata.pop('annotations', None)

    return metadata


def clean_container(container):
    for field in CONTAINER_FIELDS_TO_REMOVE:
        container.pop(field, None)
    return container


def clean_pod_spec(spec):
    for field in SPEC_FIELDS_TO_REMOVE:
        spec.pop(field, None)

    # Limpar containers
    for c in spec.get('containers', []):
        clean_container(c)

    # Limpar initContainers
    for c in spec.get('initContainers', []):
        clean_container(c)

    # Limpar creationTimestamp dos metadados do template
    return spec


def clean_template_metadata(metadata):
    metadata.pop('creationTimestamp', None)
    annotations = metadata.get('annotations', {})
    for ann in ANNOTATIONS_TO_REMOVE:
        annotations.pop(ann, None)
    if not annotations:
        metadata.pop('annotations', None)
    return metadata


def clean_volumes(volumes):
    """Remove defaultMode dos volumes (valor default do K8s)."""
    for vol in volumes:
        for source_key in ('configMap', 'secret', 'projected'):
            source = vol.get(source_key)
            if isinstance(source, dict):
                for field in VOLUME_FIELDS_TO_REMOVE:
                    source.pop(field, None)
                # Limpar items dentro do source
                for item in source.get('items', []):
                    for field in VOLUME_FIELDS_TO_REMOVE:
                        item.pop(field, None)


def clean_job_template_labels(labels):
    """Remove labels injetadas pelo controller do Job."""
    for label in JOB_TEMPLATE_LABELS_TO_REMOVE:
        labels.pop(label, None)
    return labels


def clean_manifest(data):
    kind = data.get('kind', '')

    # Limpar metadata principal
    if 'metadata' in data:
        meta = data['metadata']
        # Remover annotations especificas de Job
        annotations = meta.get('annotations', {})
        for ann in JOB_ANNOTATIONS_TO_REMOVE:
            annotations.pop(ann, None)
        data['metadata'] = clean_metadata(meta)

    # Remover status
    data.pop('status', None)

    # Limpar spec de Deployment / StatefulSet / Job
    if kind in ('Deployment', 'StatefulSet', 'Job'):
        spec = data.get('spec', {})

        # Campos extras especificos de Job
        if kind == 'Job':
            for field in JOB_SPEC_FIELDS_TO_REMOVE:
                spec.pop(field, None)

        # Limpar template metadata
        template = spec.get('template', {})
        if 'metadata' in template:
            tmeta = template['metadata']
            # Remover labels injetadas pelo controller
            if kind == 'Job':
                clean_job_template_labels(tmeta.get('labels', {}))
            template['metadata'] = clean_template_metadata(tmeta)

        # Limpar pod spec
        pod_spec = template.get('spec', {})
        clean_pod_spec(pod_spec)

        # Limpar defaultMode dos volumes
        clean_volumes(pod_spec.get('volumes', []))

    return data


def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Suporte a múltiplos documentos num mesmo arquivo
    docs = list(yaml.safe_load_all(content))
    cleaned_docs = []

    for doc in docs:
        if doc is None:
            continue
        cleaned_docs.append(clean_manifest(doc))

    with open(filepath, 'w') as f:
        yaml.dump_all(
            cleaned_docs,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False
        )

    print(f"OK: {os.path.basename(filepath)}")
    return filepath


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python3 clean-manifests.py arquivo1.yaml arquivo2.yaml ...")
        print("     python3 clean-manifests.py *.yaml")
        sys.exit(1)

    files = sys.argv[1:]
    print(f"Processando {len(files)} arquivo(s)...\n")

    for filepath in files:
        if not os.path.exists(filepath):
            print(f"SKIP: {filepath} (nao encontrado)")
            continue

        try:
            process_file(filepath)
        except Exception as e:
            print(f"ERRO: {filepath} → {e}")

    print("\nConcluído! Arquivos limpos gerados com sufixo '-clean'.")
