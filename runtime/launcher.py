"""Own the complete Image Studio lifecycle inside one container."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def log(event, **values):
    print(json.dumps({"component": "launcher", "event": event, **values}), flush=True)


def verify_models(check_files, model_root=Path('/models')):
    root = model_root.resolve()
    checked = {}
    for check_file in check_files:
        for line in Path(check_file).read_text().splitlines():
            if not line.strip():
                continue
            size, digest, relative = line.split(maxsplit=2)
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Model path is outside model directory')
            expected = (int(size), digest)
            if path in checked:
                if checked[path] != expected:
                    raise ValueError('Conflicting model checks: ' + relative)
                continue
            if path.stat().st_size != expected[0]:
                raise ValueError('Model size mismatch: ' + relative)
            with path.open('rb') as handle:
                if hashlib.file_digest(handle, 'sha256').hexdigest() != digest:
                    raise ValueError('Model checksum mismatch: ' + relative)
            checked[path] = expected
            log('model_verified', file=relative)


def commands():
    models = '/models/'
    gen_encoder = os.getenv('GEN_ENCODER_FILE', 'Qwen-Image-2.1-Turbo-Abliterated-Uncensored-Q8_0.gguf')
    vlm_model = os.getenv('VLM_MODEL_FILE', 'Qwen-Image-2.1-Turbo-Abliterated-Uncensored-Q8_0.gguf')
    projector = models + 'mmproj-Qwen-Image-2.1-Turbo-Abliterated-Uncensored-F16.gguf'
    return {
        'gen': ['/usr/local/bin/sd-server', '--listen-ip', '127.0.0.1', '--listen-port', '8001',
                '--diffusion-model', models + 'Qwen-Image-2.1-Turbo-AD-Q6_K.gguf',
                '--llm', models + gen_encoder, '--llm_vision', projector,
                '--vae', models + 'vae/qwen_image_2.1_vae_bf16.safetensors',
                '--backend', 'CUDA0', '--params-backend', 'CUDA0', '--auto-fit', 'off',
                '--eager-load', '--conditioning-cache-size', '0', '--fa', '--steps', '8',
                '--cfg-scale', '1.0', '--sampling-method', 'euler',
                '--sigmas', '1.0,0.978453,0.95418,0.926626,0.89508,0.845148,0.704534,0.414568,0.0',
                '--width', '1024', '--height', '1024', '--log-level', 'info'],
        'vlm': ['/usr/local/bin/llama-server', '--host', '127.0.0.1', '--port', '8002',
                '--model', models + vlm_model, '--mmproj', projector, '--alias', 'qwen-image-analysis',
                '--ctx-size', '8192', '--parallel', '1', '--cache-ram', '0', '--ctx-checkpoints', '0',
                '--no-cache-idle-slots', '--gpu-layers', 'all', '--mmproj-device', 'CUDA0',
                '--fit', 'off', '--flash-attn', 'on', '--jinja'],
        'api': [sys.executable, '-m', 'uvicorn', 'main:app', '--app-dir', '/app/gateway',
                '--host', '0.0.0.0', '--port', '8000', '--workers', '1', '--no-access-log'],
        'webui': [sys.executable, '-m', 'uvicorn', 'server:app', '--app-dir', '/app/webui',
                  '--host', '0.0.0.0', '--port', '8080', '--workers', '1', '--no-access-log'],
    }


def telemetry(stop, root=Path('/output/telemetry')):
    executables = {'gen': '/usr/local/bin/sd-server', 'vlm': '/usr/local/bin/llama-server'}
    root.mkdir(parents=True, exist_ok=True)
    while not stop.is_set():
        memory = {}
        try:
            result = subprocess.run(['nvidia-smi', '--query-compute-apps=process_name,used_gpu_memory',
                                     '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=3, check=True)
            for line in result.stdout.splitlines():
                executable, value = (part.strip() for part in line.rsplit(',', 1))
                if value.isdigit():
                    memory[executable] = memory.get(executable, 0) + int(value)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        try:
            cgroup = int(Path('/sys/fs/cgroup/memory.current').read_text())
            for backend, executable in executables.items():
                temporary = root / (backend + '.json.tmp')
                temporary.write_text(json.dumps({'gpu_mib': memory.get(executable),
                    'cgroup_memory_bytes': cgroup, 'cgroup_scope': 'container', 'measured_at': int(time.time())}))
                temporary.replace(root / (backend + '.json'))
        except OSError as error:
            log('telemetry_error', error=str(error))
        stop.wait(1)


def supervise(children_commands, stop, grace=40):
    children = []
    readers = []

    def forward(name, stream):
        for line in stream:
            print(f'[{name}] {line.rstrip()}', flush=True)
        stream.close()

    try:
        for name, command in children_commands.items():
            if stop.is_set():
                return 0
            process = subprocess.Popen(command, start_new_session=True, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, errors='replace', bufsize=1)
            children.append((name, process))
            reader = threading.Thread(target=forward, args=(name, process.stdout), daemon=True)
            reader.start()
            readers.append(reader)
            log('process_started', name=name, pid=process.pid)
        while not stop.wait(.2):
            for name, process in children:
                status = process.poll()
                if status is not None:
                    log('process_exited', name=name, exit_code=status)
                    return status if status > 0 else 1
        return 0
    finally:
        # Signal complete groups, including any subprocesses started by a backend.
        for name, process in children:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + grace
        for name, process in children:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                log('process_killed', name=name)
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        for reader in readers:
            reader.join(timeout=1)


def main():
    stop = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda _signal, _frame: stop.set())
    observer = None
    try:
        verify_models(['/app/config/model-checks-gen.txt', '/app/config/model-checks-vlm.txt'])
        observer = threading.Thread(target=telemetry, args=(stop,), daemon=True)
        observer.start()
        return supervise(commands(), stop)
    except Exception as error:
        log('startup_failed', error=str(error))
        return 1
    finally:
        stop.set()
        if observer:
            observer.join(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
