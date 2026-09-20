from types import SimpleNamespace

import app.runtime_updates as runtime_updates


def test_environment_check_finds_managed_updates_and_validates_tools(monkeypatch):
    monkeypatch.setattr(runtime_updates, 'diagnostics', lambda: {
        'python': '3.12.7', 'yt_dlp': '2026.8.19', 'ejs': '0.8.0',
        'ffmpeg': 'ffmpeg', 'ffprobe': 'ffprobe', 'node': 'node'})
    versions = {
        'node': 'v22.18.0',
        'ffmpeg': 'ffmpeg version 7.1.1 Copyright',
        'ffprobe': 'ffprobe version 7.1.1 Copyright',
    }
    monkeypatch.setattr(runtime_updates, '_run_version', lambda command: versions[command[0]])

    report = runtime_updates.check_runtime_environment(
        lambda package: {'yt-dlp': '2026.9.20', 'yt-dlp-ejs': '0.8.0'}[package])

    assert report['usable'] is True
    assert report['updates'] == ['yt_dlp']
    assert next(item for item in report['components'] if item['key'] == 'node')['current'] == '22.18.0'
    assert next(item for item in report['components'] if item['key'] == 'ffmpeg')['current'] == '7.1.1'


def test_environment_check_survives_offline_package_index(monkeypatch):
    monkeypatch.setattr(runtime_updates, 'diagnostics', lambda: {
        'python': '3.12.7', 'yt_dlp': '2026.8.19', 'ejs': '0.8.0',
        'ffmpeg': None, 'ffprobe': None, 'node': None})

    def offline(_):
        raise OSError('offline')

    report = runtime_updates.check_runtime_environment(offline)
    assert report['usable'] is False
    assert report['updates'] == []
    assert report['lookup_errors']
    assert report['missing'] == ['FFmpeg', 'ffprobe', 'Node.js']


def test_upgrade_uses_fixed_project_package_allowlist():
    reports = iter([
        {'updates': ['yt_dlp', 'ejs']},
        {'updates': [], 'usable': True},
    ])
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')

    result = runtime_updates.upgrade_runtime_components(lambda: next(reports), runner)
    assert result['changed'] is True
    assert calls[0][0][-2:] == ['yt-dlp[default]', 'yt-dlp-ejs']
    assert calls[0][0][:4] == [runtime_updates.sys.executable, '-m', 'pip', 'install']
    assert calls[0][1]['timeout'] == 600
