"""Optional live integration probe; never creates subscriptions or downloads."""
import json
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
command = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--js-runtimes', 'node',
           '--encoding', 'utf-8',
           '--socket-timeout', '10', '--retries', '0', '--extractor-retries', '0',
           '--skip-download', '--dump-single-json', '--no-warnings', '--no-playlist',
           sys.argv[1] if len(sys.argv) > 1 else 'https://www.youtube.com/watch?v=jNQXAC9IVRw']
try:
    result = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace', timeout=65)
    if result.returncode:
        print(result.stderr[-3000:])
        sys.exit(result.returncode)
    data = json.loads(result.stdout)
    print(json.dumps({'id':data.get('id'),'title':data.get('title'),'formats':len(data.get('formats',[]))},ensure_ascii=True))
except subprocess.TimeoutExpired:
    print('YouTube connectivity check timed out after 65 seconds.')
    sys.exit(2)
