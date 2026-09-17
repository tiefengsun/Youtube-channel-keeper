from collections import deque
from contextlib import contextmanager
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import tempfile

from .storage import channel_directory

log = logging.getLogger(__name__)


def cookies_rotated(value):
    return 'provided youtube account cookies are no longer valid' in str(value).lower()


class Interrupted(Exception):
    pass


@contextmanager
def isolated_cookies(settings):
    # yt-dlp writes its cookie jar on exit. Give concurrent scan/download
    # processes separate copies and never modify the user's source file.
    if not settings.get('cookies_file'):
        yield settings
        return
    with tempfile.TemporaryDirectory(prefix='channel-keeper-cookies-') as folder:
        cookie_copy = Path(folder) / 'cookies.txt'
        shutil.copyfile(settings['cookies_file'], cookie_copy)
        yield {**settings, 'cookies_file': str(cookie_copy)}


def base_command(settings):
    args = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--encoding', 'utf-8',
            '--no-colors', '--socket-timeout', '30', '--retries', '3',
            '--extractor-retries', '2', '--js-runtimes', 'node']
    if settings['proxy']:
        args += ['--proxy', settings['proxy']]
    if settings['cookies_file']:
        args += ['--cookies', settings['cookies_file']]
    return args


def download_command(job, settings):
    if job.get('kind') == 'manual':
        folder = Path(job['output_dir']).resolve()
        output = f"%(title).120B [%(id)s] [{job['resolution']}p].%(ext)s"
    else:
        root = Path(settings['output_dir']).resolve()
        folder = channel_directory(root, job.get('channel_name'), job['channel_id'])
        output = '%(title).120B [%(id)s].%(ext)s'
    args = base_command(settings) + ['--no-playlist', '--no-simulate', '--newline', '--progress',
        '--continue', '--windows-filenames', '--trim-filenames', '180',
        '--paths', str(folder), '--output', output,
        '--progress-template', 'download:__PROGRESS__%(progress)j',
        '--print', 'after_move:__FILE__%(filepath)j']
    fmt = job['format']
    cap = f"[height<={job['resolution']}]" if job['resolution'] else ''
    if fmt in ('mp3', 'm4a'):
        args += ['-f', 'bestaudio/best', '-x', '--audio-format', fmt, '--audio-quality', '0']
    else:
        # Resolution is a ceiling, not an exact-match requirement. Select the
        # highest stream below it first, then prefer codecs/streams suited to
        # the requested output container. This avoids dropping from (for
        # example) 2160p to 1080p merely because 2160p is only offered as WebM.
        selector = f'bestvideo{cap}+bestaudio/best{cap}'
        sort = 'res,ext:mp4:m4a' if fmt == 'mp4' else 'res,ext:webm:webm' if fmt == 'webm' else 'res'
        args += ['-f', selector, '--format-sort-force', '-S', sort,
                 '--merge-output-format', fmt, '--remux-video', fmt]
    args += ['--', f"https://www.youtube.com/watch?v={job['video_id']}"]
    return args


def diagnostics():
    try:
        version = importlib.metadata.version('yt-dlp')
    except importlib.metadata.PackageNotFoundError:
        version = None
    try:
        ejs = importlib.metadata.version('yt-dlp-ejs')
    except importlib.metadata.PackageNotFoundError:
        ejs = None
    return {'python': sys.version.split()[0], 'yt_dlp': version, 'ejs': ejs,
            'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe'),
            'node': shutil.which('node')}


def kill_process(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


class Engine:
    def __init__(self, store):
        self.store = store
        self.stop_event = threading.Event()
        self.actions = threading.RLock()
        self.threads = []
        self.active_job = None
        self.active_kind = 'channel'
        self.cancel_event = threading.Event()

    def start(self):
        self.store.recover()
        for target in (self.scan_loop, self.download_loop):
            thread = threading.Thread(target=target, daemon=True, name=target.__name__)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=20)

    def spawn(self, args, merge=False):
        return subprocess.Popen(args, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge else subprocess.PIPE,
            encoding='utf-8', errors='replace', stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            start_new_session=os.name != 'nt')

    def scan_channel(self, channel, settings):
        with isolated_cookies(settings) as process_settings:
            return self._scan_channel(channel, process_settings)

    def inspect_video(self, url, settings):
        with isolated_cookies(settings) as process_settings:
            process = self.spawn(base_command(process_settings) + [
                '--dump-single-json', '--skip-download', '--no-playlist', '--', url])
            try:
                try:
                    stdout, stderr = process.communicate(timeout=120)
                except subprocess.TimeoutExpired:
                    raise RuntimeError('解析视频超过 2 分钟，请检查网络或代理')
                if process.returncode:
                    detail = stderr.strip()
                    prefix = 'The provided YouTube account cookies are no longer valid.\n' if cookies_rotated(detail) else ''
                    raise RuntimeError(prefix + (detail[-1800:] or '视频信息解析失败'))
                data = json.loads(stdout)
                if data.get('id') != url.rsplit('=', 1)[-1]:
                    raise RuntimeError('返回的视频信息与链接不一致')
                heights = sorted({f.get('height') for f in data.get('formats', [])
                    if isinstance(f, dict) and f.get('vcodec') not in (None, 'none')
                    and isinstance(f.get('height'), int) and 1 <= f['height'] <= 4320}, reverse=True)
                if not heights:
                    raise RuntimeError('未读取到可下载的视频画质，请确认视频可访问')
                return {'url': url, 'video_id': data['id'], 'title': data.get('title') or data['id'],
                    'uploader': data.get('uploader') or data.get('channel') or '',
                    'duration': data.get('duration'), 'qualities': heights}
            finally:
                kill_process(process)

    def _scan_channel(self, channel, settings):
        args = base_command(settings) + ['--flat-playlist', '--dump-single-json',
            '--skip-download', '--no-lazy-playlist', '--abort-on-error', '--',
            channel['url'] + '/' + channel['tab']]
        process = self.spawn(args)
        start = time.monotonic()
        try:
            while True:
                if self.stop_event.is_set():
                    raise Interrupted()
                if time.monotonic() - start > 900:
                    raise RuntimeError('频道扫描超过 15 分钟，请检查网络或代理')
                try:
                    stdout, stderr = process.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                detail = stderr.strip()
                prefix = 'The provided YouTube account cookies are no longer valid.\n' if cookies_rotated(detail) else ''
                raise RuntimeError(prefix + (detail[-1800:] or 'yt-dlp 扫描失败'))
            data = json.loads(stdout)
            entries = data.get('entries')
            if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
                raise RuntimeError('频道返回了不完整的视频列表，未保存此次扫描')
            # An empty response can be a transient tab/parser failure. Never
            # initialize an empty baseline and accidentally backfill next time.
            if not entries and not channel['initialized']:
                raise RuntimeError('此栏目暂无可读取视频；尚未建立基线，下次会重试')
            return entries, data.get('channel') or data.get('uploader') or ''
        finally:
            kill_process(process)

    def scan_loop(self):
        while not self.stop_event.is_set():
            channel = None
            try:
                with self.actions:
                    settings = self.store.settings()
                    if not settings['paused']:
                        channel = self.store.claim_scan()
                if channel:
                    entries, name = self.scan_channel(channel, settings)
                    with self.actions:
                        self.store.finish_scan(channel['id'], entries, name)
            except Interrupted:
                if channel:
                    self.store.scan_error(channel['id'], '服务已停止，稍后重试')
            except Exception as exc:
                log.warning('Channel scan failed: %s', self.safe_error(exc, settings))
                if channel:
                    self.store.scan_error(channel['id'], self.safe_error(exc, settings))
            self.stop_event.wait(1)

    def safe_error(self, exc, settings):
        value = str(exc)
        for field in ('proxy', 'cookies_file'):
            if settings.get(field):
                value = value.replace(settings[field], '<已隐藏配置>')
        hint = ''
        if cookies_rotated(value):
            return ('Cookies 已失效：YouTube 已轮换登录会话。请在 Chrome 无痕窗口登录 YouTube，'
                '在同一标签打开 youtube.com/robots.txt 后导出 youtube.com 的 cookies.txt，'
                '随即关闭无痕窗口并重新导入。导出后不要再次打开该无痕会话。')
        if 'confirm you' in value.lower() and 'bot' in value.lower():
            hint = 'YouTube 要求登录验证。请在偏好设置中填写有效的 Cookies 文件，检查网络后重试。\n'
        elif 'requested format is not available' in value.lower():
            hint = '该视频没有符合当前格式与分辨率上限的版本。可为后续视频调整频道设置；当前任务保留创建时的设置。\n'
        return hint + value[-1600:]

    def download(self, job, settings):
        with isolated_cookies(settings) as process_settings:
            return self._download(job, process_settings)

    def get_job(self, job):
        return self.store.manual_job(job['id']) if job.get('kind') == 'manual' else self.store.job(job['id'])

    def update_job(self, job, **fields):
        if job.get('kind') == 'manual':
            self.store.update_manual_job(job['id'], **fields)
        else:
            self.store.update_job(job['id'], **fields)

    def _download(self, job, settings):
        deps = diagnostics()
        if not deps['ffmpeg'] or not deps['ffprobe']:
            raise RuntimeError('未找到 FFmpeg / ffprobe，请安装后重启工具，再重试任务')
        if not deps['node'] or not deps['ejs']:
            raise RuntimeError('缺少 Node.js 或 yt-dlp-ejs，请运行安装脚本补齐依赖')
        process = self.spawn(download_command(job, settings), merge=True)
        lines = queue.Queue()
        tail = deque(maxlen=12)
        rotated_warning = False

        def reader():
            try:
                for line in process.stdout:
                    lines.put(line.rstrip())
            finally:
                lines.put(None)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        start = time.monotonic()
        updated = 0
        filepath = ''
        try:
            while True:
                current = self.get_job(job)
                if self.stop_event.is_set() or self.cancel_event.is_set() or not current or current['status'] == 'cancelled':
                    raise Interrupted()
                if time.monotonic() - start > 43200:
                    raise RuntimeError('下载超过 12 小时，任务已中断，可手动重试')
                try:
                    line = lines.get(timeout=0.5)
                except queue.Empty:
                    continue
                if line is None:
                    break
                if line.startswith('__FILE__'):
                    filepath = json.loads(line[len('__FILE__'):])
                elif line.startswith('__PROGRESS__'):
                    if time.monotonic() - updated < 0.5:
                        continue
                    updated = time.monotonic()
                    try:
                        p = json.loads(line[len('__PROGRESS__'):])
                        total = p.get('total_bytes') or p.get('total_bytes_estimate') or 0
                        progress = min(99, 100 * (p.get('downloaded_bytes') or 0) / total) if total else 0
                        speed = f"{p['speed'] / 1048576:.1f} MB/s" if p.get('speed') else ''
                        eta = f"{int(p['eta'])} 秒" if p.get('eta') is not None else ''
                        self.update_job(job, progress=progress, speed=speed, eta=eta,
                            stage='合并 / 转封装' if p.get('status') == 'finished' else '下载音视频分段')
                    except (ValueError, TypeError, KeyError):
                        pass
                else:
                    if cookies_rotated(line):
                        rotated_warning = True
                    tail.append(line)
            process.wait(timeout=30)
            if process.returncode:
                detail = '\n'.join(tail)[-1800:] or 'yt-dlp 下载失败'
                if rotated_warning:
                    detail = 'The provided YouTube account cookies are no longer valid.\n' + detail
                raise RuntimeError(detail)
            result = Path(filepath).resolve() if filepath else None
            root = job['output_dir'] if job.get('kind') == 'manual' else settings['output_dir']
            if not result or not result.is_file() or not result.is_relative_to(Path(root).resolve()):
                raise RuntimeError('下载进程结束，但未找到最终文件，请查看下载目录并重试')
            return str(result)
        finally:
            kill_process(process)
            thread.join(timeout=3)
            process.stdout.close()

    def download_loop(self):
        while not self.stop_event.is_set():
            job = None
            try:
                with self.actions:
                    settings = self.store.settings()
                    if not settings['paused']:
                        job = self.store.claim_manual_job() or self.store.claim_job()
                        if job:
                            job.setdefault('kind', 'channel')
                            self.active_job = job['id']
                            self.active_kind = job['kind']
                            self.cancel_event.clear()
                if job:
                    filepath = self.download(job, settings)
                    with self.actions:
                        current = self.get_job(job)
                        if current and current['status'] == 'downloading':
                            self.update_job(job, status='completed', progress=100,
                                stage='已完成', filepath=filepath, finished_at=time.time(), speed='', eta='')
            except Interrupted:
                with self.actions:
                    if job and (current := self.get_job(job)) and current['status'] == 'downloading':
                        self.update_job(job, status='queued', stage='等待恢复', progress=0,
                                              attempts=max(0, job['attempts'] - 1))
            except Exception as exc:
                log.warning('Download failed: %s', self.safe_error(exc, settings))
                with self.actions:
                    if job and (current := self.get_job(job)) and current['status'] == 'downloading':
                        error = self.safe_error(exc, settings)
                        invalid_cookies = error.startswith('Cookies 已失效')
                        retry = not invalid_cookies and job['attempts'] <= settings['retries']
                        self.update_job(job, status='retrying' if retry else 'failed',
                            stage='等待自动重试' if retry else 'Cookies 已失效，等待重新导入' if invalid_cookies else '下载失败', speed='', eta='',
                            available_at=time.time() + min(3600, 60 * 2 ** (job['attempts'] - 1)),
                            error=error)
            finally:
                with self.actions:
                    self.active_job = None
                    self.active_kind = 'channel'
            self.stop_event.wait(1)
