from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit
import re
import time

from pydantic import BaseModel, Field, field_validator, model_validator


Format = Literal['mp4', 'mkv', 'webm', 'mp3', 'm4a']
Resolution = Literal[0, 360, 480, 720, 1080, 1440, 2160]


def channel_url(value: str) -> str:
    value = value.strip()
    if value.startswith('@'):
        value = 'https://www.youtube.com/' + value
    elif value.startswith(('youtube.com/', 'www.youtube.com/')):
        value = 'https://' + value
    parts = urlsplit(value)
    if (parts.scheme != 'https' or parts.hostname not in ('youtube.com', 'www.youtube.com')
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise ValueError('请填写 https://www.youtube.com/@频道名 或频道链接')
    path = unquote(parts.path).rstrip('/')
    path = re.sub(r'/(videos|shorts|streams|featured)$', '', path)
    if not re.fullmatch(r'/(@[^/\s?#\\%]+|channel/UC[\w-]{22}|c/[\w.\-]+|user/[\w.\-]+)', path):
        raise ValueError('只支持 YouTube 频道链接或 @频道名，不支持单个视频和播放列表')
    return 'https://www.youtube.com' + path


class ChannelCreate(BaseModel):
    url: str
    name: str = Field(default='', max_length=120)
    interval_minutes: int = Field(default=60, ge=5, le=10080)
    format: Format = 'mp4'
    resolution: Resolution = 1080
    tab: Literal['videos', 'shorts'] = 'videos'
    initial_count: int = Field(default=0, ge=0, le=500)
    schedule_mode: Literal['now', 'scheduled'] = 'now'
    start_at: float | None = None

    _url = field_validator('url')(channel_url)

    @model_validator(mode='after')
    def schedule(self):
        validate_schedule(self.schedule_mode, self.start_at)
        return self


class ChannelEdit(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    interval_minutes: int = Field(ge=5, le=10080)
    format: Format
    resolution: Resolution
    enabled: bool
    schedule_mode: Literal['keep', 'now', 'scheduled'] = 'keep'
    start_at: float | None = None

    @model_validator(mode='after')
    def schedule(self):
        validate_schedule(self.schedule_mode, self.start_at)
        return self


def validate_schedule(mode, start_at):
    if mode == 'scheduled':
        if start_at is None:
            raise ValueError('请选择首次或下次运行时间')
        if start_at < time.time() - 30:
            raise ValueError('指定运行时间不能早于当前时间')
        if start_at > time.time() + 366 * 24 * 3600:
            raise ValueError('指定运行时间不能超过一年')


class CookieImport(BaseModel):
    filename: str = Field(default='cookies.txt', max_length=255)
    content: str = Field(min_length=1, max_length=2_000_000)


class Settings(BaseModel):
    output_dir: str = str(Path(__file__).resolve().parents[1] / 'downloads')
    proxy: str = ''
    cookies_file: str = ''
    retries: int = Field(default=3, ge=0, le=10)
    paused: bool = False

    @field_validator('output_dir')
    @classmethod
    def output_path(cls, value):
        if not value.strip() or '\x00' in value:
            raise ValueError('下载目录不能为空')
        return str(Path(value.strip()).expanduser().resolve())

    @field_validator('proxy')
    @classmethod
    def proxy_url(cls, value):
        value = value.strip()
        if value:
            p = urlsplit(value)
            if p.scheme not in ('http', 'https', 'socks5', 'socks5h') or not p.hostname:
                raise ValueError('代理示例：http://127.0.0.1:7890')
            _ = p.port
        return value

    @field_validator('cookies_file')
    @classmethod
    def cookie_path(cls, value):
        value = value.strip()
        if value and not Path(value).expanduser().is_file():
            raise ValueError('Cookies 文件不存在，请提供 Netscape 格式文件的完整路径')
        return str(Path(value).expanduser().resolve()) if value else ''
