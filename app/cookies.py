"""Validate and minimize Netscape cookies exported for YouTube."""
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile


ALLOWED_DOMAINS = ('youtube.com', 'google.com', 'googlevideo.com', 'youtube-nocookie.com')
AUTH_COOKIE_NAMES = {
    'SID', 'HSID', 'SSID', 'APISID', 'SAPISID', 'LOGIN_INFO',
    '__Secure-1PSID', '__Secure-3PSID', '__Secure-1PAPISID', '__Secure-3PAPISID',
}


def _relevant_domain(value):
    domain = value.removeprefix('#HttpOnly_').lstrip('.').lower()
    return any(domain == allowed or domain.endswith('.' + allowed) for allowed in ALLOWED_DOMAINS)


def prepare_youtube_cookies(content):
    if not content or len(content.encode('utf-8')) > 2_000_000:
        raise ValueError('Cookies 文件为空或超过 2 MB')
    content = content.lstrip('\ufeff')
    kept, skipped, names = [], 0, set()
    now = int(datetime.now(timezone.utc).timestamp())
    for number, raw in enumerate(content.splitlines(), 1):
        line = raw.rstrip('\r')
        if not line or (line.startswith('#') and not line.startswith('#HttpOnly_')):
            continue
        fields = line.split('\t', 6)
        if len(fields) != 7:
            raise ValueError(f'第 {number} 行不是有效的 Netscape Cookies 格式')
        domain, include_subdomains, path, secure, expires, name, value = fields
        if include_subdomains not in ('TRUE', 'FALSE') or secure not in ('TRUE', 'FALSE'):
            raise ValueError(f'第 {number} 行包含无效的 TRUE/FALSE 字段')
        if not path.startswith('/') or not expires.isdigit() or not name or '\x00' in value:
            raise ValueError(f'第 {number} 行包含无效的 Cookie 字段')
        if not _relevant_domain(domain) or (int(expires) != 0 and int(expires) <= now):
            skipped += 1
            continue
        kept.append('\t'.join(fields))
        names.add(name)
    if not kept:
        raise ValueError('文件中没有找到有效的 YouTube/Google Cookies，请在已登录 YouTube 的页面重新导出')
    output = '# Netscape HTTP Cookie File\n# Managed by Channel Keeper; do not edit while the service is running.\n' + '\n'.join(kept) + '\n'
    return output, {'kept': len(kept), 'skipped': skipped, 'auth_detected': bool(names & AUTH_COOKIE_NAMES)}


def save_managed_cookies(data_dir, content):
    target_dir = Path(data_dir) / 'secrets'
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / 'youtube-cookies.txt'
    fd, temporary = tempfile.mkstemp(prefix='.cookies-', suffix='.tmp', dir=target_dir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target.resolve()
