"""Healthy means both model backends, API and WebUI are ready."""
import json
import urllib.request

def check():
    for url in ('http://127.0.0.1:8000/health', 'http://127.0.0.1:8080/ready'):
        with urllib.request.urlopen(url, timeout=4) as response:
            if json.load(response).get('status') != 'ok':
                raise RuntimeError('Service is not ready: ' + url)

if __name__ == '__main__':
    check()
