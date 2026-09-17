"""Create isolated, paused demo records for visual QA, never user subscriptions."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import ChannelCreate
from app.store import Store

folder = Path(__file__).resolve().parents[1] / 'artifacts' / 'ui-test'
store = Store(folder / 'keeper.sqlite3')
settings = store.settings()
settings.update(paused=True, output_dir=str(folder / 'downloads'))
store.save_settings(settings)
if not store.channels():
    examples = [('@sample-design','设计与日常','mp4',1080,60),
                ('@sample-nature','山野之间 · Nature','mkv',2160,360),
                ('@sample-tech','科技新鲜事','mp4',1080,30),
                ('@sample-audio','耳边的故事','mp3',0,1440)]
    for index, (url,name,fmt,res,interval) in enumerate(examples):
        cid = store.add_channels([ChannelCreate(url=url,name=name,format=fmt,resolution=res,interval_minutes=interval,initial_count=1)])[0]
        store.finish_scan(cid,[{'id':f'{index:011d}','title':['关于设计的一次安静观察','走进山林，遇见清晨的第一束光','本周科技：那些值得关注的新变化','慢下来，听听生活的声音'][index]}])
    for index,job in enumerate(store.jobs()):
        if index == 0:
            store.update_job(job['id'],status='failed',error='演示错误：网络连接超时，请检查代理。')
        elif index == 1:
            store.update_job(job['id'],status='completed',progress=100,finished_at=time.time())
print(folder)
