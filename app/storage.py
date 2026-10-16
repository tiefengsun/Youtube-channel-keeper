"""Readable, Windows-safe per-channel download folders."""
import json
from pathlib import Path
import re
import threading
import unicodedata

_directory_lock = threading.Lock()


def safe_channel_name(name, channel_id):
    name = unicodedata.normalize('NFKC', name or '')
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', name).strip(' .')[:70].rstrip(' .')
    if not name:
        name = f'频道-{channel_id}'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.I):
        name = '_' + name
    return name


def channel_directory(root, name, channel_id, channel_key=''):
    # The instance lock guarantees one service process per data directory. A
    # process-wide lock keeps concurrent workers from seeing a new folder
    # before its ownership marker has been written.
    with _directory_lock:
        return _channel_directory(root, name, channel_id, channel_key)


def _channel_directory(root, name, channel_id, channel_key=''):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    name = safe_channel_name(name, channel_id)
    for attempt in range(1000):
        suffix = '' if attempt == 0 else f' [{channel_id}]' if attempt == 1 else f' [{channel_id}-{attempt}]'
        folder = root / (name + suffix)
        # Do not follow an existing junction/symlink or use somebody else's folder.
        if folder.resolve() != folder:
            continue
        marker = folder / '.channel-keeper.json'
        try:
            folder.mkdir()
        except FileExistsError:
            if not folder.is_dir() or marker.is_symlink():
                continue
            try:
                owner = json.loads(marker.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if isinstance(owner, dict):
                same_local_id = owner.get('channel_id') == channel_id
                same_channel = bool(channel_key and owner.get('channel_key') == channel_key)
                if same_local_id or same_channel:
                    # The database id changes when a subscription is removed and
                    # later added again. The normalized channel URL is stable, so
                    # refresh the marker and keep using the original folder.
                    expected = {'channel_id': channel_id}
                    if channel_key:
                        expected['channel_key'] = channel_key
                    if owner != expected:
                        marker.write_text(json.dumps(expected, ensure_ascii=False), encoding='utf-8')
                    return folder
        else:
            owner = {'channel_id': channel_id}
            if channel_key:
                owner['channel_key'] = channel_key
            marker.write_text(json.dumps(owner, ensure_ascii=False), encoding='utf-8')
            return folder
    raise RuntimeError('无法创建频道目录：同名文件夹过多，请检查下载路径')
