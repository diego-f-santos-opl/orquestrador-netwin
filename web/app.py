#!/usr/bin/env python3
# ==============================================================================
#   app.py — Netwin Web Interface
#   Backend Flask com suporte a SSE para streaming de logs em tempo real
#   Uso: python3 web/app.py
# ==============================================================================

import sys
import os
import json
import yaml
import subprocess
import threading
import queue
import datetime

from flask import Flask, render_template, request, jsonify, Response, stream_with_context

BASE_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SCRIPTS_DIR = os.path.join(BASE_DIR, 'scripts')
ENVS_DIR    = os.path.join(BASE_DIR, 'environments')
LOGS_DIR    = os.path.join(BASE_DIR, 'logs')

app = Flask(__name__)
app.config['SECRET_KEY'] = 'netwin-secret-key'

# Fila global para streaming de logs
log_queues = {}


# =============================================================
# Helpers
# =============================================================

def load_environments():
    envs = []
    if not os.path.exists(ENVS_DIR):
        return envs
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


def get_env(name):
    path = os.path.join(ENVS_DIR, f"{name}.yaml")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def get_current_version():
    manifest = os.path.join(BASE_DIR, 'db-migrate', 'netwin-db-migrate.yaml')
    try:
        with open(manifest) as f:
            doc = yaml.safe_load(f)
        return doc.get('metadata', {}).get('labels', {}).get('app.kubernetes.io/version', 'n/a')
    except Exception:
        return 'n/a'


def run_kubectl(args, namespace):
    try:
        result = subprocess.run(
            ['kubectl'] + args + ['-n', namespace],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, '', str(e)


def get_pods(namespace):
    ok, out, _ = run_kubectl([
        'get', 'pods',
        '-o', 'jsonpath={range .items[*]}{.metadata.name},{.status.phase},{.status.containerStatuses[0].ready},{.status.containerStatuses[0].restartCount}\n{end}'
    ], namespace)
    pods = []
    if ok and out.strip():
        for line in out.strip().split('\n'):
            if not line:
                continue
            parts = line.split(',')
            if len(parts) >= 2:
                pods.append({
                    'name':     parts[0],
                    'phase':    parts[1] if len(parts) > 1 else 'Unknown',
                    'ready':    parts[2] if len(parts) > 2 else 'false',
                    'restarts': parts[3] if len(parts) > 3 else '0',
                })
    return pods


def get_deployments(namespace):
    ok, out, _ = run_kubectl([
        'get', 'deployments',
        '-o', 'jsonpath={range .items[*]}{.metadata.name},{.spec.replicas},{.status.readyReplicas},{.status.updatedReplicas}\n{end}'
    ], namespace)
    deploys = []
    if ok and out.strip():
        for line in out.strip().split('\n'):
            if not line:
                continue
            parts = line.split(',')
            desired = parts[1] if len(parts) > 1 else '0'
            ready   = parts[2] if len(parts) > 2 else '0'
            deploys.append({
                'name':    parts[0],
                'desired': desired or '0',
                'ready':   ready   or '0',
                'updated': parts[3] if len(parts) > 3 else '0',
                'status':  'ok' if (desired and ready and desired == ready and desired != '0') else ('stopped' if desired == '0' else 'pending'),
            })
    return deploys


def stream_script(task_id, script_name, args):
    """Executa script e envia output via SSE."""
    q = log_queues.get(task_id)
    if not q:
        return

    script_path = os.path.join(SCRIPTS_DIR, script_name)
    cmd = [sys.executable, script_path] + args

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        for line in proc.stdout:
            q.put({'type': 'log', 'data': line.rstrip()})
        proc.wait()
        status = 'success' if proc.returncode == 0 else 'error'
        q.put({'type': 'done', 'status': status, 'code': proc.returncode})
    except Exception as e:
        q.put({'type': 'log',  'data': f'ERRO: {e}'})
        q.put({'type': 'done', 'status': 'error', 'code': 1})
    finally:
        q.put(None)  # sentinel


# =============================================================
# Rotas
# =============================================================

@app.route('/')
def index():
    envs = load_environments()
    return render_template('index.html', envs=envs)


@app.route('/api/environments')
def api_environments():
    return jsonify(load_environments())


@app.route('/api/status/<env_name>')
def api_status(env_name):
    env = get_env(env_name)
    if not env:
        return jsonify({'error': 'Ambiente nao encontrado'}), 404

    namespace   = env.get('namespace', 'netwin')
    pods        = get_pods(namespace)
    deployments = get_deployments(namespace)
    version     = get_current_version()

    return jsonify({
        'env':         env,
        'namespace':   namespace,
        'version':     version,
        'pods':        pods,
        'deployments': deployments,
        'timestamp':   datetime.datetime.now().isoformat(),
    })


@app.route('/api/run/<task_id>', methods=['POST'])
def api_run(task_id):
    data   = request.json or {}
    script = data.get('script')
    args   = data.get('args', [])

    if not script:
        return jsonify({'error': 'Script nao informado'}), 400

    # Criar fila para este task
    log_queues[task_id] = queue.Queue()

    # Executar script em thread separada
    t = threading.Thread(target=stream_script, args=(task_id, script, args), daemon=True)
    t.start()

    return jsonify({'task_id': task_id, 'status': 'started'})


@app.route('/api/stream/<task_id>')
def api_stream(task_id):
    """SSE endpoint para streaming de logs."""
    def generate():
        q = log_queues.get(task_id)
        if not q:
            yield f"data: {json.dumps({'type': 'error', 'data': 'Task nao encontrada'})}\n\n"
            return

        while True:
            try:
                msg = q.get(timeout=30)
                if msg is None:
                    break
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get('type') == 'done':
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

        log_queues.pop(task_id, None)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':    'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


@app.route('/api/logs/<env_name>/<pod_name>')
def api_pod_logs(env_name, pod_name):
    env       = get_env(env_name)
    if not env:
        return jsonify({'error': 'Ambiente nao encontrado'}), 404
    namespace = env.get('namespace', 'netwin')
    lines     = request.args.get('lines', '100')
    ok, out, err = run_kubectl(['logs', pod_name, f'--tail={lines}'], namespace)
    return jsonify({'logs': out if ok else err, 'ok': ok})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n  Netwin Web iniciado em http://localhost:{port}\n")
    app.run(debug=True, host='0.0.0.0', port=port, threaded=True)
