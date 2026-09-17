from pathlib import Path

import pytest

from app.models import ChannelCreate
from app.storage import channel_directory, safe_channel_name
from app.store import Store


def test_channel_names_flow_from_queue_to_directory(tmp_path):
    store = Store(tmp_path / 'test.db')
    cid = store.add_channels([ChannelCreate(url='@sample',name='测试频道 BLUE TV',initial_count=1)])[0]
    store.finish_scan(cid,[{'id':'abcdefghijk','title':'视频'}])
    job = store.claim_job()
    folder = channel_directory(tmp_path / 'downloads',job['channel_name'],job['channel_id'])
    assert folder.name == '测试频道 BLUE TV'
    (folder / 'video.mp4').write_bytes(b'video')
    assert channel_directory(tmp_path / 'downloads',job['channel_name'],cid) == folder
    assert (folder / 'video.mp4').read_bytes() == b'video'


@pytest.mark.parametrize('name', ['../../outside', 'CON', 'NUL.mp4', 'COM1', ' . ', 'a:b/c\\d*e?f', '频道' * 100])
def test_windows_names_stay_inside_root(tmp_path, name):
    folder = channel_directory(tmp_path, name, 1)
    assert folder.parent == tmp_path
    assert len(folder.name) <= 71
    assert folder.name not in ('CON', 'NUL.mp4', 'COM1', '.', '..')
    assert not any(c in folder.name for c in '<>:"/\\|?*')


def test_same_name_and_unowned_directory_are_not_merged(tmp_path):
    first = channel_directory(tmp_path, '同名频道', 1)
    second = channel_directory(tmp_path, '同名频道', 2)
    assert first.name == '同名频道'
    assert second.name == '同名频道 [2]'
    assert channel_directory(tmp_path, '同名频道', 2) == second
    (tmp_path / '已有文件夹').mkdir()
    assert channel_directory(tmp_path, '已有文件夹', 3).name == '已有文件夹 [3]'
