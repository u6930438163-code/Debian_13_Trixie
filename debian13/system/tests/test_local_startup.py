import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request


REPOSITORY = Path(__file__).resolve().parents[3]


def unused_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


class LocalStartupTests(unittest.TestCase):
    def test_mcp_uses_custom_local_api_port(self):
        api_port, ui_port = unused_port(), unused_port()
        while api_port == ui_port:
            ui_port = unused_port()
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            for name in ('DEBIAN13_API_TOKEN', 'DEBIAN13_SYSTEM_API_URL',
                         'MODEL_CHAT_URL', 'MODEL_API_KEY', 'MODEL_NAME'):
                env.pop(name, None)
            env.update(DEBIAN13_STATE_DIR=tmp + '/state', WORKSPACE_DIR=tmp + '/workspace',
                       DEBIAN13_API_PORT=str(api_port), DEBIAN13_UI_PORT=str(ui_port))
            process = subprocess.Popen(
                ['bash', str(REPOSITORY / 'debian13/start-local.sh')], cwd=REPOSITORY,
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{ui_port}/api/v1/status', timeout=.2):
                            break
                    except (OSError, urllib.error.URLError):
                        if process.poll() is not None:
                            self.fail('Local startup exited before the UI was ready')
                        time.sleep(.1)
                else:
                    self.fail('Local startup did not become ready')

                token = (Path(tmp) / 'state/operator-token').read_text().strip()
                body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                   'params': {'name': 'debian13_diagnostics', 'arguments': {}}}).encode()
                request = urllib.request.Request(
                    f'http://127.0.0.1:{api_port}/mcp', data=body, method='POST',
                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token,
                             'Accept': 'application/json, text/event-stream'},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertFalse(json.load(response)['result']['isError'])
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    unittest.main()
