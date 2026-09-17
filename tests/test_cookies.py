import time

import pytest

from app.cookies import prepare_youtube_cookies, save_managed_cookies


def row(domain, name, value='secret', expires='0'):
    return f'{domain}\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}'


def test_filters_domains_expired_records_and_detects_login(tmp_path):
    source = '\ufeff# Netscape HTTP Cookie File\n' + '\n'.join([
        row('.youtube.com','__Secure-3PSID'),
        row('#HttpOnly_.youtube.com','LOGIN_INFO'),
        row('.googlevideo.com','VISITOR_INFO1_LIVE'),
        row('.example.com','PRIVATE'),
        row('.youtube.com','OLD',expires=str(int(time.time()) - 10)),
    ])
    cleaned, details = prepare_youtube_cookies(source)
    assert details == {'kept':3,'skipped':2,'auth_detected':True}
    assert 'example.com' not in cleaned and '\tOLD\t' not in cleaned
    target = save_managed_cookies(tmp_path,cleaned)
    assert target == (tmp_path / 'secrets' / 'youtube-cookies.txt').resolve()
    assert '__Secure-3PSID' in target.read_text(encoding='utf-8')


@pytest.mark.parametrize('content', ['', 'invalid', row('.example.com','SID')])
def test_rejects_empty_invalid_or_unrelated_files(content):
    with pytest.raises(ValueError): prepare_youtube_cookies(content)
