import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.engine import Engine, Interrupted, base_command, download_command, isolated_cookies
from app.main import InstanceLock, create_app
from app.models import ChannelCreate, ChannelEdit, Settings, channel_url
from app.store import Store


def videos(*ids):
    return [{'id': f'{n:011d}', 'title': f'视频 {n}'} for n in ids]


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / '测试数据' / 'keeper.sqlite3')


def add(store, initial=0):
    return store.add_channels([ChannelCreate(url='@sample', initial_count=initial)])[0]


def test_first_scan_is_baseline_then_only_new_videos(store):
    channel = add(store)
    assert store.finish_scan(channel, videos(3, 2, 1), '测试频道') == 0
    assert store.jobs() == []
    assert store.channel(channel)['name'] == '测试频道'
    assert store.finish_scan(channel, videos(5, 4, 3, 2, 1)) == 2
    assert store.finish_scan(channel, videos(5, 4, 3, 2, 1)) == 0
    assert {j['video_id'] for j in store.jobs()} == {'00000000005', '00000000004'}


def test_initial_latest_count_and_no_repeat(store):
    channel = add(store, 2)
    assert store.finish_scan(channel, videos(3, 2, 1)) == 2
    assert len(store.jobs()) == 2
    assert store.finish_scan(channel, videos(3, 2, 1)) == 0


def test_failed_scan_rolls_back_entire_baseline(store):
    channel = add(store, 2)
    with pytest.raises(ValueError):
        store.finish_scan(channel, videos(3, 2) + [{'id': 'invalid'}])
    store.scan_error(channel, '网络中断')
    assert not store.channel(channel)['initialized']
    assert store.channels()[0]['video_count'] == 0
    assert store.jobs() == []
    assert store.finish_scan(channel, videos(4, 3, 2)) == 2


def test_live_video_not_seen_until_publish(store):
    channel = add(store)
    upcoming = {'id':'abcdefghijk', 'title':'即将发布', 'live_status':'is_upcoming'}
    store.finish_scan(channel, videos(1) + [upcoming])
    store.finish_scan(channel, [dict(upcoming, live_status='not_live')] + videos(1))
    assert [j['video_id'] for j in store.jobs()] == ['abcdefghijk']


def test_recovery_and_disabled_channel(store):
    channel = add(store, 1)
    store.claim_scan()
    store.finish_scan(channel, videos(1))
    job = store.claim_job()
    assert job['attempts'] == 1
    store.recover()
    assert store.job(job['id'])['status'] == 'queued'
    assert store.job(job['id'])['attempts'] == 0
    store.update_channel(channel, ChannelEdit(name='sample',interval_minutes=60,format='mkv',resolution=720,enabled=False))
    assert store.claim_job() is None
    assert store.claim_scan() is None


def test_atomic_claim_prevents_duplicate_download(store):
    channel = add(store, 1)
    store.finish_scan(channel, videos(1))
    results = []
    threads = [threading.Thread(target=lambda: results.append(store.claim_job())) for _ in range(8)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sum(j is not None for j in results) == 1


def test_oldest_eligible_job_claimed_across_both_queues(store):
    channel = add(store, 1)
    store.finish_scan(channel, videos(1))
    manual_id = store.add_manual_job('abcdefghijk', '单条视频', '作者', 'mp4', 720, str(store.path.parent))
    with store.connect() as db:
        db.execute('UPDATE jobs SET created_at=100 WHERE channel_id=?', (channel,))
        db.execute('UPDATE manual_jobs SET created_at=200 WHERE id=?', (manual_id,))
    first = store.claim_next_job()
    second = store.claim_next_job()
    assert first['kind'] == 'channel'
    assert second['kind'] == 'manual'
    assert store.claim_next_job() is None


def test_batch_channel_jobs_use_manual_queue_and_skip_existing(store, tmp_path):
    entries = [{'id': 'abcdefghijk', 'title': '历史视频 1'},
               {'id': '12345678901', 'title': '历史视频 2'}]
    result = store.add_batch_channel_jobs(entries, '示例频道', 'mp4', 1080, str(tmp_path))
    assert result['added'] == 2
    jobs = store.manual_jobs()
    assert {job['source_type'] for job in jobs} == {'channel_batch'}
    assert {job['uploader'] for job in jobs} == {'示例频道'}
    duplicate = store.add_batch_channel_jobs(entries, '示例频道', 'mp4', 1080, str(tmp_path))
    assert duplicate == {'ids': [], 'added': 0, 'skipped': 2}


def test_scheduled_first_run_waits_then_can_be_changed_to_now(store):
    start_at = time.time() + 3600
    channel = store.add_channels([ChannelCreate(url='@scheduled',schedule_mode='scheduled',start_at=start_at)])[0]
    assert store.channel(channel)['next_scan'] == pytest.approx(start_at)
    assert store.claim_scan() is None
    store.update_channel(channel, ChannelEdit(name='scheduled',interval_minutes=60,format='mp4',
        resolution=1080,enabled=True,schedule_mode='now'))
    assert store.claim_scan()['id'] == channel


def test_edit_can_keep_or_replace_next_run(store):
    channel = add(store)
    with store.connect() as db:
        db.execute('UPDATE channels SET next_scan=? WHERE id=?',(time.time()+7200,channel))
    original = store.channel(channel)['next_scan']
    store.update_channel(channel, ChannelEdit(name='sample',interval_minutes=180,format='mkv',resolution=720,enabled=True))
    assert store.channel(channel)['next_scan'] == original
    scheduled = time.time() + 5400
    store.update_channel(channel, ChannelEdit(name='sample',interval_minutes=180,format='mkv',resolution=720,
        enabled=True,schedule_mode='scheduled',start_at=scheduled))
    assert store.channel(channel)['next_scan'] == pytest.approx(scheduled)


def test_rejects_invalid_scheduled_time():
    with pytest.raises(ValueError,match='请选择'):
        ChannelCreate(url='@sample',schedule_mode='scheduled')
    with pytest.raises(ValueError,match='不能早于'):
        ChannelCreate(url='@sample',schedule_mode='scheduled',start_at=time.time()-3600)


@pytest.mark.parametrize('bad', ['https://evil.com/@foo','http://youtube.com/@foo',
    'https://youtube.com/watch?v=abcdefghijk','https://youtube.com/playlist?list=x',
    'https://youtube.com.evil.com/@foo','https://user@youtube.com/@foo',
    'https://youtube.com/@foo%2F..%2Fwatch', 'file:///tmp/video', 'https://youtube.com:999/@foo'])
def test_reject_non_channel_urls(bad):
    with pytest.raises(ValueError): channel_url(bad)


@pytest.mark.parametrize('url', ['@sample','https://youtube.com/@sample/videos?view=0',
    'www.youtube.com/@sample', 'https://www.youtube.com/@sample/shorts'])
def test_normalize_channel_url(url):
    assert channel_url(url) == 'https://www.youtube.com/@sample'


@pytest.mark.parametrize('fmt', ['mp4','mkv','webm','mp3','m4a'])
def test_format_commands_and_strict_resolution(fmt, tmp_path):
    settings = Settings(output_dir=str(tmp_path / '中文目录')).model_dump()
    command = download_command({'channel_id':1,'format':fmt,'resolution':720,'video_id':'abcdefghijk'},settings)
    assert command[-1] == 'https://www.youtube.com/watch?v=abcdefghijk'
    selector = command[command.index('-f') + 1]
    if fmt in ('mp3','m4a'):
        assert command[command.index('--audio-format') + 1] == fmt
    else:
        assert selector == 'bestvideo[height<=720]+bestaudio/best[height<=720]'
        assert '--format-sort-force' in command
        sort = command[command.index('-S') + 1]
        assert sort.split(',')[0] == 'res'
        if fmt == 'mp4':
            assert 'ext:mp4:m4a' in sort
        elif fmt == 'webm':
            assert 'ext:webm:webm' in sort
        assert command[command.index('--remux-video') + 1] == fmt


def test_unlimited_resolution_has_no_height_filter(tmp_path):
    settings = Settings(output_dir=str(tmp_path)).model_dump()
    command = download_command(
        {'channel_id': 1, 'format': 'mp4', 'resolution': 0, 'video_id': 'abcdefghijk'}, settings)
    assert command[command.index('-f') + 1] == 'bestvideo+bestaudio/best'


def test_batch_audio_filename_does_not_claim_video_resolution(tmp_path):
    settings = Settings(manual_output_dir=str(tmp_path)).model_dump()
    command = download_command({'kind':'manual', 'output_dir':str(tmp_path), 'format':'mp3',
        'resolution':1080, 'video_id':'abcdefghijk'}, settings)
    output = command[command.index('--output') + 1]
    assert '[1080p]' not in output
    assert '[%(id)s]' in output


def test_channel_page_scan_requests_one_extra_entry_for_pagination(store, monkeypatch):
    captured = []
    payload = {'channel':'示例频道', 'entries':[
        {'id':f'{index:011d}', 'title':f'视频 {index}', 'duration':index}
        for index in range(51)]}

    class Process:
        returncode = 0
        def communicate(self, timeout=None):
            return json.dumps(payload), ''
        def poll(self):
            return 0

    engine = Engine(store)
    monkeypatch.setattr(engine, 'spawn', lambda args: captured.append(args) or Process())
    page = engine.inspect_channel_page('https://www.youtube.com/@sample', store.settings())
    assert captured[0][captured[0].index('--playlist-items') + 1] == '1:51'
    assert len(page['entries']) == 50
    assert page['has_more'] is True
    assert page['next_start'] == 52


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / 'api', run_engine=False)
    with TestClient(app, base_url='http://127.0.0.1:8765',
                    headers={'X-Local-Request':'1'}) as client:
        assert client.post('/api/auth/login', json={'username':'keeper','password':'keeper'}).status_code == 200
        yield client


def test_api_add_update_scan_remove_and_atomic_duplicates(client):
    response = client.post('/api/channels',json={'channels':[{'url':'@sample'}]})
    assert response.status_code == 201
    channel_id = response.json()['ids'][0]
    response = client.post('/api/channels',json={'channels':[{'url':'@other'},{'url':'@sample'}]})
    assert response.status_code == 409
    assert len(client.get('/api/state').json()['channels']) == 1
    response = client.put(f'/api/channels/{channel_id}',json={'name':'新备注','interval_minutes':30,'format':'mkv','resolution':2160,'enabled':True})
    assert response.status_code == 200
    assert client.post(f'/api/channels/{channel_id}/scan').status_code == 200
    assert client.delete(f'/api/channels/{channel_id}').status_code == 200
    assert client.get('/api/state').json()['channels'] == []


def test_batch_channel_scan_pagination_and_enqueue(client, tmp_path, monkeypatch):
    pages = iter([
        {'channel_name': '测试频道', 'entries': [{'id':'abcdefghijk','title':'第一条','duration':60,'timestamp':1,'upload_date':''}], 'next_start':52, 'has_more':True},
        {'channel_name': '测试频道', 'entries': [{'id':'12345678901','title':'第二条','duration':90,'timestamp':2,'upload_date':''}], 'next_start':103, 'has_more':False},
    ])
    monkeypatch.setattr(client.app.state.engine, 'inspect_channel_page', lambda *args, **kwargs: next(pages))
    settings = client.app.state.store.settings()
    settings['manual_output_dir'] = str(tmp_path / '手动下载')
    client.app.state.store.save_settings(settings)

    first = client.post('/api/batch-channel/scan', json={'url':'@sample'})
    assert first.status_code == 200
    token = first.json()['token']
    assert first.json()['loaded'] == 1 and first.json()['has_more'] is True
    more = client.post(f'/api/batch-channel/{token}/more')
    assert more.status_code == 200
    assert more.json()['loaded'] == 2 and more.json()['has_more'] is False
    queued = client.post(f'/api/batch-channel/{token}/jobs', json={
        'video_ids':['abcdefghijk','12345678901'], 'format':'mp4', 'resolution':1080})
    assert queued.status_code == 201
    assert queued.json()['added'] == 2
    assert Path(queued.json()['output_dir']).name == '测试频道'
    jobs = client.get('/api/state').json()['manual_jobs']
    assert {job['source_type'] for job in jobs} == {'channel_batch'}


def test_batch_channel_rejects_video_outside_scan(client, monkeypatch):
    monkeypatch.setattr(client.app.state.engine, 'inspect_channel_page', lambda *args, **kwargs: {
        'channel_name':'测试频道', 'entries':[{'id':'abcdefghijk','title':'第一条'}],
        'next_start':52, 'has_more':False})
    token = client.post('/api/batch-channel/scan', json={'url':'@sample'}).json()['token']
    response = client.post(f'/api/batch-channel/{token}/jobs', json={
        'video_ids':['12345678901'], 'format':'mp4', 'resolution':1080})
    assert response.status_code == 400


def test_local_request_protection(client):
    assert client.post('/api/scan',headers={'X-Local-Request':''}).status_code == 403
    assert client.post('/api/scan',headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.get('/api/state',headers={'Host':'evil.example'}).status_code == 400
    assert client.get('/api/state',headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
    assert client.post('/api/scan',headers={'Origin':'http://127.0.0.1:8765'}).status_code == 200


def test_lan_mode_requires_authentication_on_pages_and_api(tmp_path, monkeypatch):
    monkeypatch.setenv('CHANNEL_KEEPER_LAN_IP', '10.168.165.219')
    app = create_app(tmp_path / 'lan', run_engine=False)
    with TestClient(app, base_url='http://10.168.165.219:8765', headers={'X-Local-Request':'1'}) as lan_client:
        assert lan_client.get('/api/health').json()['app_id'] == 'channel-keeper'
        assert lan_client.get('/', follow_redirects=False).status_code == 303
        assert lan_client.get('/login').status_code == 200
        assert lan_client.get('/api/state').status_code == 401
        assert 'WWW-Authenticate' not in lan_client.get('/api/state').headers
        assert lan_client.get('/api/state', headers={'Authorization': 'Basic invalid'}).status_code == 401
        assert lan_client.post('/api/auth/login', json={'username':'keeper','password':'wrong'}).status_code == 401
        assert lan_client.post('/api/auth/login', json={'username':'keeper','password':'keeper'}).status_code == 200
        assert lan_client.get('/').status_code == 200
        assert lan_client.get('/api/state').json()['auth'] == {'username': 'keeper', 'must_change': True}
        assert lan_client.post('/api/scan').status_code == 200
        assert lan_client.post('/api/scan', headers={'X-Local-Request':''}).status_code == 403
        assert lan_client.put('/api/auth', json={'username': 'keeper', 'current_password': 'bad',
            'new_password': 'new-password-123'}).status_code == 403
        assert lan_client.put('/api/auth', json={'username': 'new-admin', 'current_password': 'keeper',
            'new_password': 'short'}).status_code == 400
        changed = lan_client.put('/api/auth', json={'username': 'new-admin',
            'current_password': 'keeper', 'new_password': 'new-password-123'})
        assert changed.status_code == 200
        assert changed.json()['auth'] == {'username': 'new-admin', 'must_change': False}
        assert lan_client.get('/api/state').status_code == 401
        assert lan_client.post('/api/auth/login', json={'username':'new-admin','password':'new-password-123'}).status_code == 200
        assert lan_client.get('/api/state').status_code == 200
    restarted = create_app(tmp_path / 'lan', run_engine=False)
    with TestClient(restarted, base_url='http://10.168.165.219:8765', headers={'X-Local-Request':'1'}) as lan_client:
        assert lan_client.post('/api/auth/login', json={'username':'new-admin','password':'new-password-123'}).status_code == 200
        assert lan_client.get('/api/state').json()['auth']['must_change'] is False


def test_session_persists_across_app_restart_and_logout(client):
    token = client.cookies.get('keeper_session')
    assert token and client.app.state.store.session_valid(token)
    restarted = create_app(client.app.state.store.path.parent, run_engine=False)
    with TestClient(restarted, base_url='http://127.0.0.1:8765',
                    headers={'X-Local-Request':'1'}, cookies={'keeper_session':token}) as resumed:
        assert resumed.get('/api/state').status_code == 200
        assert resumed.post('/api/auth/logout').status_code == 200
        assert resumed.get('/api/state').status_code == 401
    assert not client.app.state.store.session_valid(token)


def test_login_rate_limit_and_cookie_flags(tmp_path):
    app = create_app(tmp_path / 'login', run_engine=False)
    with TestClient(app, base_url='http://127.0.0.1:8765', headers={'X-Local-Request':'1'}) as browser:
        assert browser.post('/api/auth/login', json={'username':'keeper','password':'keeper'},
                            headers={'X-Local-Request':''}).status_code == 403
        for _ in range(5):
            assert browser.post('/api/auth/login', json={'username':'keeper','password':'wrong'}).status_code == 401
        assert browser.post('/api/auth/login', json={'username':'keeper','password':'keeper'}).status_code == 429
    second = create_app(tmp_path / 'second-login', run_engine=False)
    with TestClient(second, base_url='http://127.0.0.1:8765', headers={'X-Local-Request':'1'}) as browser:
        response = browser.post('/api/auth/login', json={'username':'keeper','password':'keeper'})
        assert response.status_code == 200
        cookie = response.headers['set-cookie'].lower()
        assert 'httponly' in cookie and 'samesite=lax' in cookie


def test_settings_validation_and_persistence(client, tmp_path):
    settings = client.get('/api/state').json()['settings']
    settings.update(output_dir=str(tmp_path / '下载'),proxy='http://127.0.0.1:7890')
    assert client.put('/api/settings',json=settings).status_code == 200
    assert client.get('/api/state').json()['settings']['proxy'] == settings['proxy']
    assert client.put('/api/settings',json={**settings,'proxy':'file:///etc/passwd'}).status_code == 422
    assert client.put('/api/settings',json={**settings,'cookies_file':str(tmp_path / 'missing.txt')}).status_code == 422
    assert client.put('/api/settings',json={**settings,'scan_depth':5001}).status_code == 422


def test_cookie_import_enables_managed_file_and_retries_auth_failure(client):
    store = client.app.state.store
    channel = add(store, 1)
    store.finish_scan(channel, videos(1))
    job = store.claim_job()
    store.update_job(job['id'],status='failed',error="Sign in to confirm you're not a bot")
    content = '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-3PSID\tsecret\n.example.com\tTRUE\t/\tTRUE\t0\tSID\tleak\n'
    response = client.post('/api/settings/cookies/import',json={'filename':'cookies.txt','content':content})
    assert response.status_code == 200
    assert response.json()['kept'] == 1
    assert response.json()['retried_jobs'] == 1
    target = Path(store.settings()['cookies_file'])
    assert response.json()['cookies_file'] == str(target)
    assert target.name == 'youtube-cookies.txt' and target.is_file()
    assert 'secret' in target.read_text(encoding='utf-8')
    assert 'leak' not in target.read_text(encoding='utf-8')
    assert store.job(job['id'])['status'] == 'queued'


def test_rotated_cookies_stop_channel_scan_and_resume_after_import(store):
    channel = add(store, 1)
    store.finish_scan(channel, videos(1))
    job = store.claim_job()
    raw = "WARNING: The provided YouTube account cookies are no longer valid. They have likely been rotated in the browser."
    engine = Engine(store)
    message = engine.safe_error(RuntimeError(raw), store.settings())
    assert message.startswith('Cookies 已失效')
    assert '--no-warnings' not in base_command(store.settings())
    store.scan_error(channel, message)
    store.update_job(job['id'], status='failed', error=message)
    manual_id = store.add_manual_job('abcdefghijk', '标题', '作者', 'mp4', 720, str(store.path.parent))
    store.update_manual_job(manual_id, status='failed', error=message)
    assert store.channel(channel)['next_scan'] > time.time() + 3600 * 24 * 365
    assert store.claim_scan() is None
    assert store.retry_auth_failures() == 2
    assert store.job(job['id'])['status'] == 'queued'
    assert store.manual_job(manual_id)['status'] == 'queued'
    assert store.channel(channel)['next_scan'] == 0


def test_cancel_retry_and_active_process_race(client):
    store = client.app.state.store
    channel = add(store, 1)
    store.finish_scan(channel, videos(1))
    job = store.claim_job()
    engine = client.app.state.engine
    engine.active_jobs[('channel', job['id'])] = threading.Event()
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    assert engine.active_jobs[('channel', job['id'])].is_set()
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409
    assert client.delete(f'/api/channels/{channel}').status_code == 409
    engine.active_jobs.clear()
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 200
    assert store.job(job['id'])['attempts'] == 0
    assert store.job(job['id'])['status'] == 'queued'


def test_cancel_one_active_job_does_not_cancel_another(client):
    store = client.app.state.store
    channel = add(store, 2)
    store.finish_scan(channel, videos(2, 1))
    jobs = store.jobs()
    engine = client.app.state.engine
    first, second = jobs[0]['id'], jobs[1]['id']
    engine.active_jobs[('channel', first)] = threading.Event()
    engine.active_jobs[('channel', second)] = threading.Event()
    assert client.post(f'/api/jobs/{first}/cancel').status_code == 200
    assert engine.active_jobs[('channel', first)].is_set()
    assert not engine.active_jobs[('channel', second)].is_set()


def test_open_completed_job_folder_uses_stored_filepath(client, tmp_path, monkeypatch):
    store = client.app.state.store
    channel = add(store, 1)
    store.finish_scan(channel, videos(1))
    job = store.claim_job()
    downloaded = tmp_path / '频道' / '视频.mp4'
    downloaded.parent.mkdir()
    downloaded.write_bytes(b'video')
    store.update_job(job['id'], status='completed', filepath=str(downloaded))
    opened = []
    monkeypatch.setattr('app.main.open_file_location', lambda path: opened.append(path))

    response = client.post(f"/api/jobs/{job['id']}/open-folder")

    assert response.status_code == 200
    assert opened == [str(downloaded)]


def test_open_job_folder_rejects_missing_file(client, tmp_path):
    store = client.app.state.store
    channel = add(store, 1)
    store.finish_scan(channel, videos(1))
    job = store.claim_job()
    store.update_job(job['id'], status='completed', filepath=str(tmp_path / 'missing.mp4'))
    assert client.post(f"/api/jobs/{job['id']}/open-folder").status_code == 404


def test_cannot_delete_scanning_channel(client):
    store = client.app.state.store
    channel = add(store)
    store.claim_scan()
    assert client.delete(f'/api/channels/{channel}').status_code == 409


def test_instance_lock(tmp_path):
    first = InstanceLock(tmp_path / 'instance.lock')
    second = InstanceLock(tmp_path / 'instance.lock')
    first.acquire()
    try:
        with pytest.raises(RuntimeError): second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_real_subprocess_progress_and_final_file(store, tmp_path, monkeypatch):
    channel = add(store, 1)
    store.finish_scan(channel, videos(1))
    job = store.claim_job()
    output = tmp_path / '视频.mp4'
    script = tmp_path / 'fake_downloader.py'
    script.write_text('import json\nfrom pathlib import Path\n'
        + f'path = {str(output)!r}\n'
        + 'print(\'__PROGRESS__\' + json.dumps({"total_bytes":100,"downloaded_bytes":50,"speed":1024,"eta":1}))\n'
        + 'Path(path).write_bytes(b"test video")\n'
        + 'print("__FILE__" + json.dumps(path))\n',encoding='utf-8')
    monkeypatch.setattr('app.engine.diagnostics',lambda:{'ffmpeg':True,'ffprobe':True,'node':True,'ejs':True})
    monkeypatch.setattr('app.engine.download_command',lambda *_:[sys.executable,'-u',str(script)])
    result = Engine(store).download(job,Settings(output_dir=str(tmp_path)).model_dump())
    assert Path(result) == output
    assert store.job(job['id'])['progress'] == 50


def test_scanner_rejects_partial_response(store, tmp_path, monkeypatch):
    channel_id = add(store)
    channel = store.channel(channel_id)
    engine = Engine(store)
    script = tmp_path / 'scan.py'
    script.write_text('print(\'{"entries":[null]}\')',encoding='utf-8')
    original_spawn = engine.spawn
    monkeypatch.setattr(engine,'spawn',lambda args:original_spawn([sys.executable,str(script)]))
    with pytest.raises(RuntimeError,match='不完整'):
        engine.scan_channel(channel,store.settings())
    assert not store.channel(channel_id)['initialized']


def test_scan_depth_passes_playlist_limit(store, monkeypatch):
    engine = Engine(store)
    channel = store.channel(add(store))
    captured = []
    def fail_spawn(args):
        captured.extend(args)
        raise RuntimeError('测试命令')
    monkeypatch.setattr(engine, 'spawn', fail_spawn)
    with pytest.raises(RuntimeError, match='测试命令'):
        engine.scan_channel(channel, {**store.settings(), 'scan_depth':300})
    assert captured[captured.index('--playlist-items') + 1] == '1:300'


@pytest.mark.parametrize('loop', ['scan', 'download'])
def test_transient_settings_error_does_not_kill_worker(store, monkeypatch, loop):
    engine = Engine(store)
    original = store.settings
    attempts = 0
    def flaky_settings():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError('数据库暂时不可用')
        engine.stop_event.set()
        return original()
    monkeypatch.setattr(store, 'settings', flaky_settings)
    if loop == 'scan':
        engine.scan_loop()
    else:
        engine.download_loop()
    assert attempts == 2


def test_worker_failure_backoff_and_retry_exhaustion(store, monkeypatch):
    channel = add(store, 1)
    store.finish_scan(channel,videos(1))
    engine = Engine(store)
    settings = store.settings(); settings['retries'] = 1; store.save_settings(settings)
    def fail(*args):
        engine.stop_event.set()
        raise RuntimeError('模拟网络故障')
    monkeypatch.setattr(engine,'download',fail)
    engine.download_loop()
    job = store.jobs()[0]
    assert job['status'] == 'retrying'
    assert job['available_at'] > time.time()
    store.update_job(job['id'],available_at=0)
    engine.stop_event.clear()
    engine.download_loop()
    assert store.job(job['id'])['status'] == 'failed'


def test_worker_does_not_retry_rotated_cookies(store, monkeypatch):
    channel = add(store, 1)
    store.finish_scan(channel, videos(1))
    engine = Engine(store)
    def fail(*args):
        engine.stop_event.set()
        raise RuntimeError('The provided YouTube account cookies are no longer valid. They have likely been rotated in the browser.')
    monkeypatch.setattr(engine, 'download', fail)
    engine.download_loop()
    job = store.jobs()[0]
    assert job['status'] == 'failed'
    assert job['stage'] == 'Cookies 已失效，等待重新导入'
    assert job['error'].startswith('Cookies 已失效')


def test_download_concurrency_limit(store, tmp_path, monkeypatch):
    channel = add(store, 3)
    store.finish_scan(channel, videos(3, 2, 1))
    settings = store.settings()
    settings['concurrent_downloads'] = 2
    store.save_settings(settings)
    engine = Engine(store)
    current = 0
    highest = 0
    lock = threading.Lock()
    def fake_download(job, _settings):
        nonlocal current, highest
        with lock:
            current += 1
            highest = max(highest, current)
        time.sleep(0.2)
        with lock:
            current -= 1
        path = tmp_path / f"{job['video_id']}.mp4"
        path.write_bytes(b'video')
        return str(path)
    monkeypatch.setattr(engine, 'download', fake_download)
    engine.start()
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and store.stats()['completed'] < 3:
            time.sleep(0.05)
        assert store.stats()['completed'] == 3
        assert highest == 2
    finally:
        engine.stop()


def test_scheduler_end_to_end_with_fake_youtube(store, tmp_path, monkeypatch):
    channel = add(store)
    engine = Engine(store)
    snapshot = videos(1)
    monkeypatch.setattr(engine,'scan_channel',lambda *_:(snapshot.copy(),'自动化频道'))
    def fake_download(job, settings):
        path = tmp_path / (job['video_id'] + '.mp4'); path.write_bytes(b'video'); return str(path)
    monkeypatch.setattr(engine,'download',fake_download)
    def wait_for(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.05)
        pytest.fail('Timed out waiting for scheduler')
    engine.start()
    try:
        wait_for(lambda: store.channel(channel)['initialized'])
        assert store.jobs() == []
        snapshot.insert(0,videos(2)[0]); store.request_scan(channel)
        wait_for(lambda: store.stats()['completed'] == 1)
        store.request_scan(channel)
        wait_for(lambda: store.channel(channel)['next_scan'] > time.time())
        assert len(store.jobs()) == 1
    finally:
        engine.stop()


def test_page_and_assets(client):
    assert '频道收藏站' in client.get('/').text
    for path in ['/app.js','/style.css','/favicon.svg']:
        assert client.get(path).status_code == 200
    assert client.get('/').headers['content-security-policy'].startswith("default-src 'self'")


def test_runtime_check_and_upgrade_endpoints(client, monkeypatch):
    report = {'checked_at': 1, 'usable': True, 'missing': [], 'updates': [],
              'components': [], 'lookup_errors': []}
    monkeypatch.setattr('app.main.check_runtime_environment', lambda: report)
    monkeypatch.setattr('app.main.upgrade_runtime_components',
                        lambda: {'changed': False, 'message': '已是最新版本', 'environment': report})
    assert client.post('/api/runtime/check').json() == report
    response = client.post('/api/runtime/upgrade')
    assert response.status_code == 200
    assert response.json()['environment'] == report


def test_cookie_source_is_never_modified(tmp_path):
    source = tmp_path / 'cookies.txt'; source.write_text('# Netscape HTTP Cookie File\n')
    settings = Settings(cookies_file=str(source)).model_dump()
    with isolated_cookies(settings) as copied:
        copied_path = Path(copied['cookies_file'])
        assert copied_path != source
        copied_path.write_text('mutated by downloader')
    assert source.read_text() == '# Netscape HTTP Cookie File\n'
    assert not copied_path.exists()


def test_shutdown_callback(client):
    called = []
    client.app.state.request_shutdown = lambda: called.append(True)
    assert client.post('/api/shutdown').status_code == 200
    assert called == [True]


def test_shutdown_allows_marked_loopback_request_without_login(tmp_path):
    app = create_app(tmp_path / 'local-shutdown', run_engine=False)
    called = []
    app.state.request_shutdown = lambda: called.append(True)
    with TestClient(app, base_url='http://127.0.0.1:8765',
                    client=('127.0.0.1', 50000)) as local_client:
        assert local_client.post('/api/shutdown').status_code == 401
        assert local_client.post('/api/shutdown',
                                 headers={'X-Local-Request': '1'}).status_code == 200
    assert called == [True]


def test_shutdown_still_requires_login_for_non_loopback_client(tmp_path):
    app = create_app(tmp_path / 'remote-shutdown', run_engine=False)
    app.state.request_shutdown = lambda: None
    with TestClient(app, base_url='http://127.0.0.1:8765',
                    client=('192.168.1.20', 50000),
                    headers={'X-Local-Request': '1'}) as remote_client:
        assert remote_client.post('/api/shutdown').status_code == 401


def test_restart_callback_and_instance_id(client):
    called = []
    before = client.get('/api/state').json()['instance_id']
    assert client.get('/api/health').json()['instance_id'] == before
    assert client.post('/api/restart').status_code == 503
    client.app.state.request_restart = lambda: called.append(True)
    assert client.post('/api/restart').status_code == 200
    assert called == [True]
