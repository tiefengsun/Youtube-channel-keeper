import os
from pathlib import Path

import pytest

from app.file_actions import open_file_location


@pytest.mark.skipif(os.name != 'nt', reason='Windows Explorer behavior')
def test_windows_opens_actual_containing_directory(tmp_path, monkeypatch):
    video = tmp_path / '频道 名' / '视频 [abc].mp4'
    video.parent.mkdir()
    video.write_bytes(b'video')
    opened = []
    monkeypatch.setattr(os, 'startfile', opened.append)

    open_file_location(video)

    assert [Path(item) for item in opened] == [video.parent.resolve()]
