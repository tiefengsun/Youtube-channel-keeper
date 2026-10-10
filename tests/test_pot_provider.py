from pathlib import Path

import app.pot_provider as pot_provider


def test_provider_status_reports_unavailable(monkeypatch):
    monkeypatch.setattr(pot_provider, 'urlopen', lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    result = pot_provider.provider_status()
    assert result['ready'] is False
    assert isinstance(result['installed'], bool)


def test_provider_reuses_existing_loopback_service(tmp_path, monkeypatch):
    monkeypatch.setattr(pot_provider, 'provider_status',
                        lambda timeout=0.35: {'ready': True, 'version': '2.0.2', 'installed': True})
    provider = pot_provider.PotProvider(tmp_path)
    assert provider.start() is True
    assert provider.process is None
