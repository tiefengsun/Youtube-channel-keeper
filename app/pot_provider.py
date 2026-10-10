import importlib.metadata
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.request import urlopen


log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
PROVIDER_VERSION = '2.0.2'
PROVIDER_PORT = 4416
PROVIDER_ROOT = ROOT / '.runtime' / f'bgutil-ytdlp-pot-provider-{PROVIDER_VERSION}'
PROVIDER_ENTRY = PROVIDER_ROOT / 'server' / 'build' / 'main.js'


def plugin_version():
    try:
        return importlib.metadata.version('bgutil-ytdlp-pot-provider')
    except importlib.metadata.PackageNotFoundError:
        return None


def provider_status(timeout=0.35):
    try:
        with urlopen(f'http://127.0.0.1:{PROVIDER_PORT}/ping', timeout=timeout) as response:
            data = json.load(response)
        version = str(data.get('version') or '')
        return {'ready': bool(version), 'version': version, 'installed': PROVIDER_ENTRY.is_file()}
    except (OSError, ValueError, TypeError):
        return {'ready': False, 'version': '', 'installed': PROVIDER_ENTRY.is_file()}


class PotProvider:
    """Own the loopback-only BgUtils token server used by yt-dlp's plugin."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.process = None

    def start(self):
        status = provider_status()
        if status['ready']:
            log.info('Using existing PO Token provider v%s', status['version'])
            return True
        node = shutil.which('node')
        if not node or not PROVIDER_ENTRY.is_file() or not plugin_version():
            log.warning('PO Token provider is not installed; high-quality YouTube formats may be unavailable')
            return False
        self.process = subprocess.Popen(
            [node, str(PROVIDER_ENTRY), '--host', '127.0.0.1', '--port', str(PROVIDER_PORT)],
            cwd=PROVIDER_ENTRY.parent.parent, stdin=subprocess.DEVNULL,
            # Upstream prints generated short-lived tokens to stdout. Never
            # persist or expose that stream; application logs stay credential-free.
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            start_new_session=os.name != 'nt')
        # Loading the native canvas dependency can take roughly 30-45 seconds
        # on the first Windows launch, especially under antivirus scanning.
        for _ in range(240):
            if provider_status(timeout=0.2)['ready']:
                log.info('PO Token provider started on 127.0.0.1:%s', PROVIDER_PORT)
                return True
            if self.process.poll() is not None:
                break
            time.sleep(0.25)
        self.stop()
        log.error('PO Token provider did not become ready')
        return False

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.process = None
