import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

from .engine import diagnostics


_upgrade_lock = threading.Lock()
_managed_packages = {
    'yt_dlp': ('yt-dlp', 'yt-dlp[default]'),
    'ejs': ('yt-dlp-ejs', 'yt-dlp-ejs'),
}


def _creation_flags():
    return subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def _run_version(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors='replace',
                                timeout=12, creationflags=_creation_flags())
    except (OSError, subprocess.TimeoutExpired):
        return ''
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0].strip() if result.returncode == 0 and output else ''


def _version_parts(value):
    return tuple(int(part) for part in re.findall(r'\d+', value or ''))


def _is_newer(latest, current):
    return bool(latest and current and _version_parts(latest) > _version_parts(current))


def _latest_pypi(package):
    request = Request(f'https://pypi.org/pypi/{package}/json',
                      headers={'User-Agent': 'Channel-Keeper update checker'})
    last_error = None
    for _ in range(2):
        try:
            with urlopen(request, timeout=6) as response:
                return json.load(response)['info']['version']
        except Exception as exc:
            last_error = exc
    raise last_error


def check_runtime_environment(latest_provider=_latest_pypi):
    current = diagnostics()
    node_version = _run_version(['node', '--version']) if current['node'] else ''
    ffmpeg_line = _run_version(['ffmpeg', '-version']) if current['ffmpeg'] else ''
    ffprobe_line = _run_version(['ffprobe', '-version']) if current['ffprobe'] else ''
    ffmpeg_version = re.search(r'ffmpeg version\s+([^\s]+)', ffmpeg_line)
    ffprobe_version = re.search(r'ffprobe version\s+([^\s]+)', ffprobe_line)
    components = [
        {'key': 'python', 'name': 'Python', 'current': current['python'],
         'latest': None, 'usable': _version_parts(current['python']) >= (3, 10),
         'managed': False, 'detail': '需要 3.10 或更高版本；由操作系统管理'},
        {'key': 'yt_dlp', 'name': 'yt-dlp', 'current': current['yt_dlp'] or '',
         'latest': None, 'usable': bool(current['yt_dlp']), 'managed': True, 'detail': ''},
        {'key': 'ffmpeg', 'name': 'FFmpeg',
         'current': ffmpeg_version.group(1) if ffmpeg_version else '',
         'latest': None, 'usable': bool(ffmpeg_line), 'managed': False,
         'detail': '负责音视频合并；由操作系统管理'},
        {'key': 'ffprobe', 'name': 'ffprobe',
         'current': ffprobe_version.group(1) if ffprobe_version else '',
         'latest': None, 'usable': bool(ffprobe_line), 'managed': False,
         'detail': '随 FFmpeg 安装；由操作系统管理'},
        {'key': 'node', 'name': 'Node.js', 'current': node_version.lstrip('v'),
         'latest': None, 'usable': _version_parts(node_version) >= (20,),
         'managed': False, 'detail': '需要 20 或更高版本；由操作系统管理'},
        {'key': 'ejs', 'name': 'YouTube EJS', 'current': current['ejs'] or '',
         'latest': None, 'usable': bool(current['ejs']), 'managed': True, 'detail': ''},
    ]
    lookup_errors = []
    by_key = {item['key']: item for item in components}
    with ThreadPoolExecutor(max_workers=len(_managed_packages)) as pool:
        latest_futures = {key: pool.submit(latest_provider, package)
                          for key, (package, _) in _managed_packages.items()}
    for key, future in latest_futures.items():
        try:
            by_key[key]['latest'] = future.result()
        except Exception as exc:
            lookup_errors.append(f"{by_key[key]['name']}：{exc}")
            by_key[key]['detail'] = '无法连接软件源，已完成本机可用性检查'
        by_key[key]['update_available'] = _is_newer(
            by_key[key]['latest'], by_key[key]['current'])
    for item in components:
        item.setdefault('update_available', False)
    missing = [item['name'] for item in components if not item['usable']]
    updates = [item['key'] for item in components if item['update_available']]
    return {'checked_at': time.time(), 'usable': not missing, 'missing': missing,
            'updates': updates, 'components': components, 'lookup_errors': lookup_errors}


def upgrade_runtime_components(checker=check_runtime_environment, runner=subprocess.run):
    if not _upgrade_lock.acquire(blocking=False):
        raise RuntimeError('已有升级任务正在进行，请稍候')
    try:
        before = checker()
        targets = [_managed_packages[key][1] for key in before['updates'] if key in _managed_packages]
        if not targets:
            return {'changed': False, 'message': '项目组件已是最新版本', 'environment': before}
        command = [sys.executable, '-m', 'pip', 'install', '--upgrade', *targets]
        try:
            result = runner(command, capture_output=True, text=True, errors='replace', timeout=600,
                            creationflags=_creation_flags())
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('升级超时，请检查网络后重试') from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or 'pip 未返回错误详情').strip()[-2000:]
            raise RuntimeError(f'升级失败：{detail}')
        after = checker()
        return {'changed': True, 'message': '升级完成，并已重新检查运行环境',
                'environment': after}
    finally:
        _upgrade_lock.release()
