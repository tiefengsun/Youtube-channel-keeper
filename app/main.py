from contextlib import asynccontextmanager
import base64
import binascii
import ipaddress
import os
from pathlib import Path
import sqlite3
import time
import uuid
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import Engine, diagnostics
from .file_actions import open_file_location
from .filesystem import list_directories
from .cookies import prepare_youtube_cookies, save_managed_cookies
from .models import ChannelCreate, ChannelEdit, CookieImport, Settings, ManualInspect, ManualDownload
from .store import Store

ROOT = Path(__file__).resolve().parents[1]


class InstanceLock:
    def __init__(self, path):
        self.path = path
        self.file = None

    def acquire(self):
        self.file = open(self.path, 'a+b')
        try:
            if os.fstat(self.file.fileno()).st_size == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError('该数据目录已有工具实例运行，请打开已有页面')

    def release(self):
        if self.file:
            self.file.close()
            self.file = None


class BatchChannels(BaseModel):
    channels: list[ChannelCreate] = Field(min_length=1, max_length=100)


class CredentialChange(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r'^[A-Za-z0-9_.-]+$')
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(default='', max_length=256)


def create_app(data_dir=None, run_engine=True):
    lan_ip = os.environ.get('CHANNEL_KEEPER_LAN_IP', '').strip()
    if lan_ip:
        try:
            address = ipaddress.IPv4Address(lan_ip)
        except ipaddress.AddressValueError as exc:
            raise RuntimeError('局域网 IP 配置无效') from exc
        if not address.is_private or address.is_loopback:
            raise RuntimeError('局域网模式仅支持私有 IPv4 地址')
    folder = Path(data_dir or os.environ.get('CHANNEL_KEEPER_DATA', ROOT / 'data'))
    store = Store(folder / 'keeper.sqlite3')
    engine = Engine(store)
    inspected_videos = {}
    lock = InstanceLock(folder / 'instance.lock')

    @asynccontextmanager
    async def lifespan(app):
        if run_engine:
            lock.acquire()
            engine.start()
        try:
            yield
        finally:
            if run_engine:
                engine.stop()
                lock.release()

    app = FastAPI(title='频道收藏站', lifespan=lifespan, docs_url=None, redoc_url=None)
    instance_id = uuid.uuid4().hex
    app.state.store = store
    app.state.engine = engine
    allowed_hosts = ['127.0.0.1', 'localhost', '[::1]', 'testserver']
    if lan_ip:
        allowed_hosts.append(lan_ip)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware('http')
    async def protect_requests(request: Request, call_next):
        if request.url.path != '/api/health':
            authorization = request.headers.get('authorization', '')
            authenticated = False
            if authorization.lower().startswith('basic ') and len(authorization) <= 4096:
                try:
                    supplied = base64.b64decode(authorization[6:].strip(), validate=True).decode('utf-8')
                    username, password = supplied.split(':', 1)
                    authenticated = store.verify_credentials(username, password)
                except (ValueError, UnicodeDecodeError, binascii.Error):
                    pass
            if not authenticated:
                return JSONResponse({'detail': '需要登录管理页面'}, status_code=401,
                                    headers={'WWW-Authenticate': 'Basic realm="Channel Keeper", charset="UTF-8"',
                                             'Cache-Control': 'no-store'})
        origin = request.headers.get('origin')
        if origin and (urlsplit(origin).netloc != request.headers.get('host') or urlsplit(origin).scheme != 'http'):
            return JSONResponse({'detail': '拒绝跨站请求'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '拒绝跨站请求'}, status_code=403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('x-local-request') != '1':
            return JSONResponse({'detail': '缺少本机请求标记'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers['Cache-Control'] = 'no-store'
        return response

    def require_channel(channel_id):
        c = store.channel(channel_id)
        if not c:
            raise HTTPException(404, '频道不存在')
        return c

    @app.get('/api/health')
    def health():
        return {'app_id': 'channel-keeper', 'instance_id': instance_id}

    @app.get('/api/state')
    def state():
        return {'app_id': 'channel-keeper', 'instance_id': instance_id,
                'channels': store.channels(), 'jobs': store.jobs(),
                'manual_jobs': store.manual_jobs(), 'stats': store.stats(),
                'settings': store.settings(), 'diagnostics': diagnostics(), 'auth': store.auth_info()}

    @app.put('/api/auth')
    def change_auth(data: CredentialChange):
        if data.new_password and len(data.new_password) < 8:
            raise HTTPException(400, '新密码至少需要 8 位')
        try:
            changed = store.change_credentials(data.current_password, data.username, data.new_password)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if not changed:
            raise HTTPException(403, '当前密码不正确')
        return {'ok': True, 'auth': store.auth_info()}

    @app.get('/api/filesystem/directories')
    def browse_directories(path: str = ''):
        try:
            return list_directories(path)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post('/api/shutdown')
    def shutdown():
        callback = getattr(app.state, 'request_shutdown', None)
        if callback is None:
            raise HTTPException(503, '当前运行方式不支持网页停止，请在服务窗口按 Ctrl+C')
        callback()
        return {'ok': True}

    @app.post('/api/restart')
    def restart():
        callback = getattr(app.state, 'request_restart', None)
        if callback is None:
            raise HTTPException(503, '当前运行方式不支持网页重启')
        callback()
        return {'ok': True}

    @app.post('/api/channels', status_code=201)
    def add_channels(batch: BatchChannels):
        try:
            return {'ids': store.add_channels(batch.channels)}
        except sqlite3.IntegrityError:
            raise HTTPException(409, '列表中存在重复频道或已订阅的栏目，本次未添加')

    @app.put('/api/channels/{channel_id}')
    def edit_channel(channel_id: int, data: ChannelEdit):
        with engine.actions:
            channel = require_channel(channel_id)
            if channel['scanning'] and data.schedule_mode != 'keep':
                raise HTTPException(409, '频道正在扫描，请等待完成后再修改下次运行时间')
            store.update_channel(channel_id, data)
        return {'ok': True}

    @app.delete('/api/channels/{channel_id}')
    def delete_channel(channel_id: int):
        with engine.actions:
            c = require_channel(channel_id)
            with store.connect() as db:
                active = db.execute("SELECT 1 FROM jobs WHERE channel_id=? AND status='downloading'", (channel_id,)).fetchone()
            running = store.job(engine.active_job) if engine.active_job is not None and engine.active_kind == 'channel' else None
            if c['scanning'] or active or (running and running['channel_id'] == channel_id):
                raise HTTPException(409, '请等待频道扫描结束，并取消正在下载的任务后再移除')
            store.remove_channel(channel_id)
        return {'ok': True}

    @app.post('/api/channels/{channel_id}/scan')
    def scan_channel(channel_id: int):
        c = require_channel(channel_id)
        if not c['enabled'] or store.settings()['paused']:
            raise HTTPException(409, '请先启用频道并恢复调度')
        store.request_scan(channel_id)
        return {'ok': True}

    @app.post('/api/scan')
    def scan_all():
        if store.settings()['paused']:
            raise HTTPException(409, '请先恢复调度')
        store.request_scan()
        return {'ok': True}

    @app.put('/api/settings')
    def settings(data: Settings):
        try:
            import tempfile
            for value in (data.output_dir, data.manual_output_dir):
                path = Path(value)
                path.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryFile(dir=path):
                    pass
        except OSError as exc:
            raise HTTPException(400, f'下载目录无法写入：{exc}')
        store.save_settings(data.model_dump())
        return {'ok': True}

    @app.post('/api/manual/inspect')
    def inspect_manual_video(data: ManualInspect):
        try:
            info = engine.inspect_video(data.url, store.settings())
        except Exception as exc:
            raise HTTPException(400, engine.safe_error(exc, store.settings()))
        with engine.actions:
            inspected_videos[info['video_id']] = (time.time(), info)
            for video_id, (created, _) in list(inspected_videos.items()):
                if created < time.time() - 1800:
                    inspected_videos.pop(video_id, None)
        return info

    @app.post('/api/manual/jobs', status_code=201)
    def add_manual_job(data: ManualDownload):
        video_id = data.url.rsplit('=', 1)[-1]
        with engine.actions:
            cached = inspected_videos.get(video_id)
            if not cached or cached[0] < time.time() - 1800:
                raise HTTPException(409, '解析结果已过期，请重新解析视频')
            info = cached[1]
            if data.resolution not in info['qualities']:
                raise HTTPException(400, '所选画质不在该视频的可用画质中，请重新解析')
            output_dir = store.settings()['manual_output_dir']
            try:
                Path(output_dir).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(400, f'手动下载目录无法写入：{exc}')
            job_id = store.add_manual_job(video_id, info['title'], info['uploader'],
                data.format, data.resolution, output_dir)
        return {'id': job_id}

    @app.post('/api/manual/jobs/{job_id}/action/{action}')
    def manual_job_action(job_id: int, action: str):
        with engine.actions:
            job = store.manual_job(job_id)
            if not job:
                raise HTTPException(404, '任务不存在')
            if action == 'retry' and job['status'] in ('failed', 'cancelled', 'retrying'):
                if engine.active_job == job_id and engine.active_kind == 'manual':
                    raise HTTPException(409, '下载进程正在停止，请稍候几秒再重试')
                store.update_manual_job(job_id, status='queued', attempts=0, available_at=0,
                    error='', progress=0, stage='等待下载', speed='', eta='')
            elif action == 'cancel' and job['status'] in ('queued', 'retrying', 'downloading'):
                if engine.active_job == job_id and engine.active_kind == 'manual':
                    engine.cancel_event.set()
                store.update_manual_job(job_id, status='cancelled', stage='已取消', speed='', eta='')
            else:
                raise HTTPException(409, '当前任务状态不支持此操作')
        return {'ok': True}

    @app.post('/api/manual/jobs/{job_id}/open-folder')
    def open_manual_job_folder(job_id: int):
        job = store.manual_job(job_id)
        if not job or job['status'] != 'completed' or not job['filepath']:
            raise HTTPException(404, '文件不存在或任务尚未完成')
        try:
            open_file_location(job['filepath'])
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, str(exc))
        return {'ok': True}

    @app.get('/api/manual/jobs/{job_id}/file')
    def manual_job_file(job_id: int):
        job = store.manual_job(job_id)
        if not job or job['status'] != 'completed' or not job['filepath'] or not Path(job['filepath']).is_file():
            raise HTTPException(404, '文件不存在或已被移动')
        return FileResponse(job['filepath'], filename=Path(job['filepath']).name)

    @app.post('/api/settings/cookies/import')
    def import_cookies(data: CookieImport):
        try:
            cleaned, details = prepare_youtube_cookies(data.content)
            target = save_managed_cookies(folder, cleaned)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc))
        current = store.settings()
        current['cookies_file'] = str(target)
        store.save_settings(current)
        details['retried_jobs'] = store.retry_auth_failures()
        details['filename'] = data.filename
        details['cookies_file'] = str(target)
        return details

    @app.post('/api/jobs/{job_id}/open-folder')
    def open_job_folder(job_id: int):
        job = store.job(job_id)
        if not job or job['status'] != 'completed' or not job['filepath']:
            raise HTTPException(404, '文件不存在或任务尚未完成')
        try:
            open_file_location(job['filepath'])
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, str(exc))
        return {'ok': True}

    @app.post('/api/jobs/{job_id}/{action}')
    def job_action(job_id: int, action: str):
        with engine.actions:
            job = store.job(job_id)
            if not job:
                raise HTTPException(404, '任务不存在')
            if action == 'retry' and job['status'] in ('failed', 'cancelled', 'retrying'):
                if engine.active_job == job_id and engine.active_kind == 'channel':
                    raise HTTPException(409, '下载进程正在停止，请稍候几秒再重试')
                store.update_job(job_id, status='queued', attempts=0, available_at=0,
                                 error='', progress=0, stage='等待下载', speed='', eta='')
            elif action == 'cancel' and job['status'] in ('queued', 'retrying', 'downloading'):
                if engine.active_job == job_id and engine.active_kind == 'channel':
                    engine.cancel_event.set()
                store.update_job(job_id, status='cancelled', stage='已取消', speed='', eta='')
            else:
                raise HTTPException(409, '当前任务状态不支持此操作')
        return {'ok': True}

    @app.get('/api/jobs/{job_id}/file')
    def job_file(job_id: int):
        job = store.job(job_id)
        if not job or job['status'] != 'completed' or not job['filepath'] or not Path(job['filepath']).is_file():
            raise HTTPException(404, '文件不存在或已被移动')
        return FileResponse(job['filepath'], filename=Path(job['filepath']).name)

    app.mount('/', StaticFiles(directory=ROOT / 'app' / 'static', html=True), name='static')
    return app
