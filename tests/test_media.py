"""Real yt-dlp + FFmpeg integration, using generated local media (no YouTube login)."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

import app.engine as engine_module
from app.engine import Engine
from app.models import ChannelCreate, Settings
from app.store import Store


@pytest.fixture(scope='module')
def media_server(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe') or not shutil.which('node'):
        pytest.skip('Requires FFmpeg, ffprobe and Node.js')
    folder = tmp_path_factory.mktemp('real-media')
    for fmt, codec, audio in [('mp4','libx264','aac'),('webm','libvpx-vp9','libopus')]:
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
            'testsrc=size=256x144:rate=10','-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
            '-t','1','-c:v',codec,'-pix_fmt','yuv420p','-c:a',audio,str(folder / ('sample.' + fmt))],check=True,timeout=30)

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass

    server = ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(folder)))
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        yield folder, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.parametrize('fmt', ['mp4','mkv','webm','mp3','m4a'])
def test_real_download_and_conversion(fmt, media_server, tmp_path, monkeypatch):
    folder, url = media_server
    info = {'id':'abcdefghijk','title':'本地真实媒体测试','extractor':'generic','extractor_key':'Generic',
            'webpage_url':url+'/sample.mp4','duration':1,
            'formats':[{'format_id':ext,'url':url+'/sample.'+ext,'ext':ext,'height':144,'width':256,
                'vcodec':vc,'acodec':ac,'protocol':'http'}
                for ext,vc,ac in [('mp4','h264','aac'),('webm','vp9','opus')]]}
    info_file = tmp_path / 'info.json'; info_file.write_text(json.dumps(info),encoding='utf-8')
    original_command = engine_module.download_command

    def local_command(job, settings):
        command = original_command(job,settings)
        # Keep the production format/progress/postprocess options, replacing only
        # YouTube metadata extraction with deterministic metadata for local media.
        return command[:-2] + ['--load-info-json',str(info_file)]

    monkeypatch.setattr(engine_module,'download_command',local_command)
    store = Store(tmp_path / 'test.db')
    cid = store.add_channels([ChannelCreate(url='@local-test',initial_count=1,format=fmt,resolution=360)])[0]
    store.finish_scan(cid,[{'id':'abcdefghijk','title':'本地真实媒体测试'}])
    job = store.claim_job()
    result = Path(Engine(store).download(job,Settings(output_dir=str(tmp_path / '真实下载')).model_dump()))
    assert result.parent.name == '@local-test'
    assert result.suffix == '.' + fmt
    assert result.stat().st_size > 100
    probe = subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type,height',
                            '-of','json',str(result)],capture_output=True,text=True,check=True,timeout=10)
    streams = json.loads(probe.stdout)['streams']
    assert any(s['codec_type'] == 'audio' for s in streams)
    if fmt in ('mp3','m4a'):
        assert all(s['codec_type'] == 'audio' for s in streams)
    else:
        assert any(s.get('height') == 144 for s in streams)


def test_real_manual_download_uses_independent_folder(media_server, tmp_path, monkeypatch):
    folder, url = media_server
    info = {'id':'abcdefghijk','title':'手动视频','extractor':'generic','extractor_key':'Generic',
            'webpage_url':url+'/sample.mp4','duration':1,
            'formats':[{'format_id':'mp4','url':url+'/sample.mp4','ext':'mp4','height':144,'width':256,
                'vcodec':'h264','acodec':'aac','protocol':'http'}]}
    info_file = tmp_path / 'manual-info.json'
    info_file.write_text(json.dumps(info), encoding='utf-8')
    original_command = engine_module.download_command
    monkeypatch.setattr(engine_module, 'download_command',
        lambda job, settings: original_command(job, settings)[:-2] + ['--load-info-json', str(info_file)])
    store = Store(tmp_path / 'manual.db')
    target = tmp_path / '手动保存'
    target.mkdir()
    job_id = store.add_manual_job('abcdefghijk', '手动视频', '作者', 'mp4', 144, str(target))
    job = store.claim_manual_job()
    assert job['id'] == job_id
    result = Path(Engine(store).download(job, Settings(output_dir=str(tmp_path / '频道保存')).model_dump()))
    assert result.parent == target
    assert '[144p]' in result.name
    assert result.stat().st_size > 100
