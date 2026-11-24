from pathlib import Path
import sys

import pytest

import app.engine as engine_module
from app.engine import Engine, Interrupted, download_command


MEDIA_403 = 'ERROR: unable to download video data: HTTP Error 403: Forbidden'


def test_403_refreshes_tokens_and_changes_client_without_changing_job(tmp_path, monkeypatch):
    cookies = tmp_path / 'cookies.txt'
    cookies.write_text('original cookie contents')
    settings = {'cookies_file': str(cookies), 'proxy': 'http://127.0.0.1:7890'}
    job = {'id': 7, 'video_id': 'abcdefghijk', 'resolution': 1440, 'format': 'mp4'}
    engine = Engine(None)
    calls, refreshed = [], []

    def download(current_job, current_settings, *, player_client=None):
        assert current_job == job
        assert current_settings['proxy'] == settings['proxy']
        assert Path(current_settings['cookies_file']).read_text() == 'original cookie contents'
        assert current_settings['cookies_file'] != str(cookies)
        calls.append((player_client, current_settings['cookies_file']))
        if len(calls) == 1:
            raise RuntimeError(MEDIA_403)
        return 'completed.mp4'

    monkeypatch.setattr(engine, '_download', download)
    monkeypatch.setattr(engine_module, 'refresh_provider_cache', lambda: refreshed.append(True) or True)
    assert engine.download(job, settings) == 'completed.mp4'
    assert [c[0] for c in calls] == [None, 'mweb']
    assert len(refreshed) == 1
    assert not Path(calls[0][1]).exists()
    assert cookies.read_text() == 'original cookie contents'


@pytest.mark.parametrize('message', [
    'Join this channel to get access to members-only content',
    'HTTP Error 403: Forbidden fetching webpage',
    'The provided YouTube account cookies are no longer valid. ' + MEDIA_403,
])
def test_auth_errors_do_not_trigger_media_recovery(monkeypatch, message):
    engine = Engine(None)
    def fail(*args, **kwargs):
        raise RuntimeError(message)
    monkeypatch.setattr(engine, '_download', fail)
    monkeypatch.setattr(engine_module, 'refresh_provider_cache', lambda: pytest.fail('unexpected refresh'))
    with pytest.raises(RuntimeError, match=message.replace('.', r'\.')):
        engine.download({'id': 1}, {'cookies_file': ''})


def test_recovery_is_bounded_when_both_clients_fail(monkeypatch):
    engine, attempts = Engine(None), []
    def fail(*args, **kwargs):
        attempts.append(kwargs.get('player_client'))
        raise RuntimeError(MEDIA_403)
    monkeypatch.setattr(engine, '_download', fail)
    monkeypatch.setattr(engine_module, 'refresh_provider_cache', lambda: False)
    with pytest.raises(RuntimeError, match='HTTP Error 403'):
        engine.download({'id': 1, 'video_id': 'abcdefghijk'}, {'cookies_file': ''})
    assert attempts == [None, 'mweb']


def test_stop_prevents_recovery(monkeypatch):
    engine = Engine(None)
    engine.stop_event.set()
    def fail(*args, **kwargs):
        raise RuntimeError(MEDIA_403)
    monkeypatch.setattr(engine, '_download', fail)
    with pytest.raises(Interrupted):
        engine.download({'id': 1}, {'cookies_file': ''})


def test_fallback_preserves_download_quality_and_authentication(tmp_path):
    job = {'kind': 'manual', 'output_dir': str(tmp_path), 'format': 'mp4',
           'resolution': 1440, 'video_id': 'abcdefghijk'}
    settings = {'proxy': 'http://127.0.0.1:7890', 'cookies_file': 'cookies.txt'}
    normal = download_command(job, settings)
    fallback = download_command(job, settings, player_client='mweb')
    assert '--extractor-args' not in normal
    assert fallback[fallback.index('--extractor-args') + 1] == 'youtube:player_client=mweb'
    for option in ('-f', '--cookies', '--proxy', '--merge-output-format', '--paths'):
        assert normal[normal.index(option) + 1] == fallback[fallback.index(option) + 1]


def test_challenge_failure_is_not_reported_as_missing_resolution():
    engine = Engine(None)
    raw = ('TypeError: a.addOnDisposeCallback is not a function\n'
           'WARNING: n challenge solving failed: Some formats may be missing.\n'
           'WARNING: Only images are available for download.\n'
           'ERROR: Requested format is not available.')
    message = engine.safe_error(RuntimeError(raw), {})
    assert 'JavaScript 校验失败' in message
    assert '这不表示源视频缺少所选分辨率' in message
    assert '没有符合当前格式' not in message
    assert 'addOnDisposeCallback' not in message
    real_format_error = engine.safe_error(RuntimeError('Requested format is not available'), {})
    assert '没有符合当前格式' in real_format_error


def test_challenge_warning_survives_long_subprocess_output(tmp_path, monkeypatch):
    script = tmp_path / 'failed_solver.py'
    script.write_text(
        "print('WARNING: n challenge solving failed: Some formats may be missing.')\n"
        "for i in range(30): print('later output ' + str(i))\n"
        "print('ERROR: Requested format is not available')\n"
        "raise SystemExit(1)\n", encoding='utf-8')
    monkeypatch.setattr(engine_module, 'diagnostics',
                        lambda: dict(ffmpeg=True, ffprobe=True, node=True, ejs=True))
    monkeypatch.setattr(engine_module, 'download_command',
                        lambda *args, **kwargs: [sys.executable, '-u', str(script)])
    engine = Engine(None)
    monkeypatch.setattr(engine, 'get_job', lambda job: {'status': 'downloading'})
    monkeypatch.setattr(engine_module, 'refresh_provider_cache', lambda: pytest.fail('unexpected refresh'))
    with pytest.raises(RuntimeError) as exc:
        engine.download({'id': 1, 'video_id': 'abcdefghijk'}, {'cookies_file': ''})
    assert 'JavaScript 校验失败' in engine.safe_error(exc.value, {})
