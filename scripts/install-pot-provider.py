"""Install the version-matched native BgUtils PO Token server."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = '2.0.2'
RUNTIME = ROOT / '.runtime'
TARGET = RUNTIME / f'bgutil-ytdlp-pot-provider-{VERSION}'
ENTRY = TARGET / 'server' / 'build' / 'main.js'
URL = f'https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/{VERSION}.zip'


def version_tuple(value):
    return tuple(int(part) for part in value.strip().lstrip('v').split('.')[:3])


def run(command, cwd=None):
    result = subprocess.run(command, cwd=cwd, text=True, errors='replace')
    if result.returncode:
        raise RuntimeError(f"Command failed: {' '.join(map(str, command))}")


def main():
    if ENTRY.is_file():
        print(f'PO Token provider {VERSION} is already installed.')
        return
    node = shutil.which('node')
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    npx = shutil.which('npx.cmd' if os.name == 'nt' else 'npx')
    if not node or not npm or not npx:
        raise RuntimeError('Node.js 22 or newer, npm and npx are required.')
    version = subprocess.check_output([node, '--version'], text=True).strip()
    if version_tuple(version) < (22,):
        raise RuntimeError(f'Node.js 22 or newer is required; current version is {version}.')

    print(f'Downloading PO Token provider {VERSION}...')
    request = Request(URL, headers={'User-Agent': 'Channel-Keeper installer'})
    with urlopen(request, timeout=60) as response:
        archive = response.read()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='channel-keeper-pot-', dir=RUNTIME) as temporary:
        temp = Path(temporary)
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            root = temp.resolve()
            for member in bundle.infolist():
                destination = (temp / member.filename).resolve()
                if destination != root and root not in destination.parents:
                    raise RuntimeError('Provider archive contains an unsafe path.')
            bundle.extractall(temp)
        extracted = temp / f'bgutil-ytdlp-pot-provider-{VERSION}'
        server = extracted / 'server'
        print('Installing PO Token provider dependencies...')
        run([npm, 'ci', '--no-audit', '--no-fund'], cwd=server)
        run([npx, 'tsc'], cwd=server)
        if not (server / 'build' / 'main.js').is_file():
            raise RuntimeError('PO Token provider build did not create build/main.js.')
        if TARGET.exists():
            TARGET.resolve().relative_to(RUNTIME.resolve())
            shutil.rmtree(TARGET)
        shutil.move(str(extracted), str(TARGET))
    print(f'PO Token provider {VERSION} installed successfully.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'PO Token provider installation failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
