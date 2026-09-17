import os
from pathlib import Path
import shutil
import subprocess
import sys


def open_file_location(filepath):
    """Open the containing folder, selecting the file where supported."""
    path = Path(filepath).resolve()
    if not path.is_file():
        raise ValueError('文件不存在或已被移动')

    quiet = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if os.name == 'nt':
        # explorer.exe's /select argument is parsed inconsistently when the
        # filename contains spaces, CJK characters or punctuation. Opening the
        # resolved parent directory directly always lands in the file's actual
        # channel folder.
        os.startfile(str(path.parent))
        return

    if sys.platform == 'darwin':
        opener = shutil.which('open')
        if not opener:
            raise RuntimeError('系统缺少 open 命令，无法打开文件位置')
        subprocess.Popen([opener, '-R', str(path)], **quiet)
        return

    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise RuntimeError('服务器当前没有图形桌面会话，请复制页面显示的文件路径手动访问')
    opener = shutil.which('xdg-open')
    if not opener:
        raise RuntimeError('系统缺少 xdg-open，请安装 xdg-utils 后重试')
    subprocess.Popen([opener, str(path.parent)], **quiet)
