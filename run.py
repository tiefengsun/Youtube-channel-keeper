"""Local entry point. Single uvicorn worker is required for the scheduler."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import threading
import time
import sys
import urllib.request
import webbrowser

import uvicorn

from app.main import create_app


def main():
    parser = argparse.ArgumentParser(description='频道收藏站 · 本地 YouTube 自动下载工具')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent / 'data')
    args = parser.parse_args()
    lan_ip = os.environ.get('CHANNEL_KEEPER_LAN_IP', '').strip()
    bind_host = lan_ip or '127.0.0.1'
    args.data_dir.mkdir(parents=True, exist_ok=True)
    # pythonw.exe has no console streams; uvicorn still expects file-like ones.
    if sys.stdout is None:
        sys.stdout = open(args.data_dir / 'background.log', 'a', encoding='utf-8')
    if sys.stderr is None:
        sys.stderr = sys.stdout
    handler = RotatingFileHandler(args.data_dir / 'keeper.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()],
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    url = f'http://{bind_host}:{args.port}'
    # The public health endpoint lets a second launch identify this instance
    # without exposing channels or bypassing authentication for management.
    try:
        with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
            existing = json.load(response)
        if existing.get('app_id') == 'channel-keeper':
            if not args.no_browser:
                webbrowser.open(url)
            print(f'Channel Keeper is already running: {url}')
            return
    except (OSError, ValueError):
        pass

    def open_when_ready():
        for _ in range(60):
            try:
                with urllib.request.urlopen(url + '/api/health', timeout=1):
                    webbrowser.open(url)
                    return
            except OSError:
                time.sleep(0.5)

    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    print(f'Channel Keeper: {url}\nKeep this process running. Ctrl+C to stop.')
    app = create_app(args.data_dir)
    server = uvicorn.Server(uvicorn.Config(app, host=bind_host, port=args.port, log_level='info', access_log=False))
    app.state.request_shutdown = lambda: setattr(server, 'should_exit', True)
    server.run()


if __name__ == '__main__':
    main()
