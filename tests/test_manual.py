import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.engine import download_command
from app.main import create_app
from app.models import Settings, video_url
from app.store import Store


@pytest.mark.parametrize('url', [
    'https://youtu.be/abcdefghijk?t=5',
    'https://www.youtube.com/watch?v=abcdefghijk&list=PL123',
    'https://m.youtube.com/shorts/abcdefghijk',
])
def test_video_url_canonicalization(url):
    assert video_url(url) == 'https://www.youtube.com/watch?v=abcdefghijk'


@pytest.mark.parametrize('url', [
    'http://youtube.com/watch?v=abcdefghijk',
    'https://evil.example/watch?v=abcdefghijk',
    'https://youtube.com/playlist?list=abcdefghijk',
    'https://youtube.com/watch?v=short',
])
def test_video_url_rejects_non_video(url):
    with pytest.raises(ValueError):
        video_url(url)


def test_existing_settings_gain_manual_directory(tmp_path):
    store = Store(tmp_path / 'keeper.db')
    with store.connect() as db:
        db.execute("UPDATE settings SET value='{" + '"output_dir":"' + str(tmp_path).replace('\\', '\\\\') + '"}' + "' WHERE id=1")
    assert store.settings()['manual_output_dir'] == Settings().manual_output_dir


def test_manual_inspect_queue_and_download_target(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'data', run_engine=False)
    info = {'url':'https://www.youtube.com/watch?v=abcdefghijk', 'video_id':'abcdefghijk',
            'title':'测试视频', 'uploader':'测试频道', 'duration':62, 'qualities':[1080, 720, 480]}
    monkeypatch.setattr(app.state.engine, 'inspect_video', lambda url, settings: info)
    with TestClient(app, base_url='http://127.0.0.1:8765',
                    headers={'X-Local-Request':'1'}) as client:
        assert client.post('/api/auth/login', json={'username':'keeper','password':'keeper'}).status_code == 200
        settings = client.get('/api/state').json()['settings']
        target = tmp_path / '单条下载'
        settings['manual_output_dir'] = str(target)
        assert client.put('/api/settings', json=settings).status_code == 200
        url = info['url']
        assert client.post('/api/manual/jobs',json={'url':url,'format':'mp4','resolution':720}).status_code == 409
        assert client.post('/api/manual/inspect',json={'url':url}).json()['qualities'] == [1080,720,480]
        assert client.post('/api/manual/jobs',json={'url':url,'format':'mp4','resolution':360}).status_code == 400
        response = client.post('/api/manual/jobs',json={'url':url,'format':'mp4','resolution':720})
        assert response.status_code == 201
        job_id = response.json()['id']
        store = app.state.store
        assert store.channels() == []
        assert len(client.get('/api/state').json()['manual_jobs']) == 1
        job = store.claim_manual_job()
        assert job['id'] == job_id and job['output_dir'] == str(target)
        command = download_command(job, settings)
        assert command[command.index('--paths')+1] == str(target)
        assert '720p' in command[command.index('--output')+1]
        assert command[command.index('-f')+1].startswith('bestvideo[height<=720]')
        store.recover()
        assert store.manual_job(job_id)['status'] == 'queued'
        assert client.post(f'/api/manual/jobs/{job_id}/action/cancel').status_code == 200
        assert client.post(f'/api/manual/jobs/{job_id}/action/retry').status_code == 200
        assert store.manual_job(job_id)['status'] == 'queued'


def test_inspect_reads_available_video_heights(tmp_path, monkeypatch):
    store = Store(tmp_path / 'keeper.db')
    from app.engine import Engine
    engine = Engine(store)

    class Process:
        returncode = 0
        def communicate(self, timeout):
            return json.dumps({'id':'abcdefghijk','title':'样例','uploader':'作者','duration':61,
                'formats':[{'height':1080,'vcodec':'av01'},{'height':720,'vcodec':'vp9'},
                    {'height':1080,'vcodec':'avc1'},{'height':None,'vcodec':'none'}]}), ''
        def poll(self): return 0

    monkeypatch.setattr(engine, 'spawn', lambda args: Process())
    info = engine.inspect_video('https://www.youtube.com/watch?v=abcdefghijk', store.settings())
    assert info['qualities'] == [1080, 720]
    assert info['duration'] == 61
