"""Cross-platform, read-only directory navigation for the local settings UI."""
import os
from pathlib import Path
import string


def directory_roots():
    if os.name == 'nt':
        return [f'{letter}:\\' for letter in string.ascii_uppercase if Path(f'{letter}:\\').is_dir()]
    values = ['/', str(Path.home())]
    return list(dict.fromkeys(values))


def list_directories(raw_path=None):
    raw = (raw_path or '').strip()
    if '\x00' in raw:
        raise ValueError('目录路径包含无效字符')
    if os.name == 'nt' and raw.startswith(('\\\\', '//')):
        raise ValueError('目录选择器不浏览网络共享；可以确认安全后手动输入 UNC 路径')
    path = Path(raw).expanduser() if raw else Path.home()
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError('目录不存在或无法访问')
    if not path.is_dir():
        raise ValueError('所选路径不是目录')
    directories = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        directories.append({'name': entry.name, 'path': str(Path(entry.path).resolve())})
                except OSError:
                    continue
    except PermissionError:
        raise ValueError('没有权限读取这个目录')
    directories.sort(key=lambda item: item['name'].casefold())
    parent = None if path.parent == path else str(path.parent)
    return {'current': str(path), 'parent': parent, 'roots': directory_roots(),
            'directories': directories[:500], 'truncated': len(directories) > 500,
            'platform': 'windows' if os.name == 'nt' else 'linux'}
