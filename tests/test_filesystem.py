from pathlib import Path

import pytest

from app.filesystem import list_directories


def test_lists_only_directories_with_cross_platform_paths(tmp_path):
    (tmp_path / '频道 A').mkdir()
    (tmp_path / '频道 B').mkdir()
    (tmp_path / 'video.txt').write_text('not a directory')
    result = list_directories(str(tmp_path))
    assert result['current'] == str(tmp_path.resolve())
    assert result['parent'] == str(tmp_path.parent.resolve())
    assert [item['name'] for item in result['directories']] == ['频道 A', '频道 B']
    assert result['platform'] in ('windows','linux')
    assert result['roots']


def test_rejects_missing_and_file_paths(tmp_path):
    with pytest.raises(ValueError,match='不存在'):
        list_directories(str(tmp_path / 'missing'))
    file = tmp_path / 'file.txt'; file.write_text('x')
    with pytest.raises(ValueError,match='不是目录'):
        list_directories(str(file))
