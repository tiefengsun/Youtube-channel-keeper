'use strict';
const $ = (q, root = document) => root.querySelector(q);
const $$ = (q, root = document) => [...root.querySelectorAll(q)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state, editId = null, deleteId = null, channelFilter = 'all', jobFilter = 'all', settingsDirty = false, authDirty = false, inspectedVideo = null, runtimeReport = null, batchSession = null;
let toastTimer, refreshBusy = false, restarting = false, lastChannels = '', lastJobs = '';
const pendingChannels = new Set();
const batchSelected = new Set();
const colors = [['#eaeedf','#748958'],['#f5e9df','#ae845f'],['#e6eef0','#72979b'],['#ede7f2','#9a81ac'],['#f0ebdb','#a48f57']];
const statuses = {queued:'排队中',downloading:'下载中',retrying:'等待重试',completed:'已完成',failed:'下载失败',cancelled:'已取消'};

function toast(message, error = false) {
  clearTimeout(toastTimer);
  const el = $('#toast'); el.textContent = message; el.classList.toggle('error', error); el.hidden = false;
  toastTimer = setTimeout(() => el.hidden = true, error ? 7000 : 3500);
}
async function api(path, method = 'GET', body, timeout = 20000) {
  const response = await fetch('/api' + path, {method, headers:{'Content-Type':'application/json','X-Local-Request':'1'}, body:body === undefined ? undefined : JSON.stringify(body), signal:AbortSignal.timeout(timeout)});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) { window.location.href = '/login'; throw new Error('请重新登录'); }
    let error = data.detail || '请求失败';
    if (response.status === 405 && path.startsWith('/manual/')) error = '后台仍在运行旧版本，请重启 Channel Keeper 服务并刷新页面后重试。';
    if (Array.isArray(error)) error = error.map(x => x.msg.replace(/^Value error, /, '')).join('；');
    throw new Error(error);
  }
  return data;
}
const timeText = value => value ? new Date(value * 1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '尚未扫描';
const intervalText = n => n < 60 ? `${n} 分钟` : n < 1440 ? `${n / 60} 小时` : `${n / 1440} 天`;
const quality = c => ['mp3','m4a'].includes(c.format) ? '仅音频' : c.resolution ? `最高 ${c.resolution}p` : '可用最高画质';
function channelVisual(id, name) {
  const [bg,fg] = colors[(Number(id) - 1) % colors.length];
  return {bg,fg,initial:String(name || '?').replace(/^@/,'').slice(0,1).toUpperCase()};
}
function namedVisual(name) {
  let hash = 0;
  for (const char of String(name || '')) hash = ((hash << 5) - hash + char.codePointAt(0)) | 0;
  const [bg,fg] = colors[Math.abs(hash) % colors.length];
  return {bg,fg,initial:String(name || '?').replace(/^@/,'').slice(0,1).toUpperCase()};
}
function showView(view) {
  $$('.page').forEach(el => el.hidden = el.id !== 'view-' + view);
  $$('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  $('#page-name').textContent = {channels:'频道订阅',manual:'单条视频',batch:'频道下载',downloads:'下载任务',settings:'偏好设置'}[view];
  location.hash = view;
}
function empty(title, description, action = '') {
  return `<div class="empty"><div class="empty-icon">▤</div><h2>${esc(title)}</h2><p>${esc(description)}</p>${action}</div>`;
}
function renderChannels(force = false) {
  const query = $('#channel-search').value.trim().toLowerCase();
  const signature = JSON.stringify([state.channels, state.settings.paused, channelFilter, query, [...pendingChannels]]);
  if (!force && signature === lastChannels) return;
  lastChannels = signature;
  const channels = state.channels.filter(c => (channelFilter === 'all' || (channelFilter === 'enabled' ? c.enabled : !c.enabled)) && `${c.name} ${c.url}`.toLowerCase().includes(query));
  $('#channel-total').textContent = state.channels.length;
  if (!channels.length) {
    $('#channel-grid').innerHTML = state.channels.length ? empty('没有找到匹配的频道','试试其他关键词，或切换上方的频道分类。') : empty('你的收藏，从一个频道开始','添加喜欢的 YouTube 创作者，设置好画质与扫描周期，新视频就会自动来到这里。','<button class="button primary" data-action="add">＋ 添加第一个频道</button>');
    return;
  }
  // Preserve expanded error panels while polling.
  const expanded = new Set($$('#channel-grid details[open]').map(el => el.dataset.error));
  $('#channel-grid').innerHTML = channels.map(c => {
    const {bg,fg,initial} = channelVisual(c.id, c.name);
    const paused = !c.enabled || state.settings.paused;
    const status = !c.enabled ? '已暂停' : state.settings.paused ? '全局已暂停' : c.scanning ? '正在扫描' : c.error ? '扫描异常' : c.initialized ? '自动监控中' : '等待首次扫描';
    const next = paused ? (c.scanning ? '本次扫描结束后暂停' : '恢复后继续扫描') : c.scanning ? '正在读取频道列表…' : c.error?.startsWith('Cookies 已失效') ? '等待重新导入 Cookies' : c.next_scan > Date.now() / 1000 ? `下次 ${timeText(c.next_scan)}` : '即将开始扫描';
    const monitorButton = `<button class="button monitor-button ${c.enabled?'':'resume'}" data-action="monitor" data-id="${c.id}" data-enabled="${c.enabled?'0':'1'}" aria-label="${c.enabled?'暂停':'开始'}监控 ${esc(c.name)}" title="${c.enabled?'暂停后不再启动此频道的新扫描和下载，已开始的任务会继续完成':'开始监控并安排一次扫描；全局暂停时需先恢复调度'}" ${pendingChannels.has(c.id)?'disabled':''}>${pendingChannels.has(c.id)?'正在更新…':c.enabled?'Ⅱ 暂停监控':'▶ 开始监控'}</button>`;
    return `<article class="channel-card"><div class="card-top"><div class="avatar" style="--avatar-bg:${bg};--avatar-fg:${fg}">${esc(initial)}</div><div class="card-identity"><h3 title="${esc(c.name)}">${esc(c.name)}</h3><a href="${esc(c.url)}" target="_blank" rel="noreferrer">${esc(c.url.replace('https://www.youtube.com/',''))} ↗</a></div><div class="card-actions"><button class="icon-button" data-action="edit" data-id="${c.id}" aria-label="编辑 ${esc(c.name)}" title="编辑频道">⋯</button><button class="icon-button" data-action="delete" data-id="${c.id}" aria-label="移除 ${esc(c.name)}" title="移除频道">×</button></div></div><div class="card-status"><span class="pill ${paused?'paused':c.error?'error':''}">● ${status}</span></div><div class="card-config"><span class="chip">${c.format.toUpperCase()}</span><span class="chip">${quality(c)}</span><span class="chip">每 ${intervalText(c.interval_minutes)}</span><span class="chip">${c.tab === 'shorts' ? 'Shorts' : 'Videos'}</span></div><div class="card-bottom"><div class="scan-time">${c.last_scan ? '上次 ' + timeText(c.last_scan) : '等待建立首次基线'}<br>${next}</div><button class="scan-button" data-action="scan" data-id="${c.id}" ${c.scanning||paused?'disabled':''}>↻ 立即扫描</button></div>${monitorButton}${c.error ? `<details class="card-error" data-error="${c.id}" ${expanded.has(String(c.id))?'open':''}><summary>查看扫描错误</summary><p>${esc(c.error)}</p></details>` : ''}</article>`;
  }).join('');
}
function renderJobs(force = false) {
  const signature = JSON.stringify([state.jobs, state.manual_jobs, jobFilter]);
  if (!force && signature === lastJobs) return;
  lastJobs = signature;
  const allJobs = [...state.jobs.map(j => ({...j,kind:'channel'})),...(state.manual_jobs||[]).map(j => ({...j,kind:'manual',channel_name:j.source_type==='channel_batch'?j.uploader:'单条视频'}))].sort((a,b) => (b.status==='downloading') - (a.status==='downloading') || b.created_at - a.created_at);
  const clearable = allJobs.filter(j => ['completed','failed','cancelled'].includes(j.status)).length;
  $('#clear-jobs').disabled = clearable === 0;
  $('#clear-jobs').textContent = clearable ? `清理记录（${clearable}）` : '清理记录';
  const jobs = allJobs.filter(j => jobFilter === 'all' || (jobFilter === 'active' ? ['queued','retrying','downloading'].includes(j.status) : jobFilter === 'failed' ? ['failed','cancelled'].includes(j.status) : j.status === jobFilter));
  const expanded = new Set($$('#job-list details[open]').map(el => el.dataset.error));
  $('#job-list').innerHTML = jobs.length ? jobs.map(j => {
    const active = ['queued','downloading','retrying'].includes(j.status);
    const path = j.kind === 'manual' ? `/manual/jobs/${j.id}` : `/jobs/${j.id}`;
    const source = j.kind === 'channel' ? channelVisual(j.channel_id, j.channel_name) : j.source_type === 'channel_batch' ? namedVisual(j.channel_name) : null;
    const sourceIcon = source ? `<div class="video-icon channel-source" style="--avatar-bg:${source.bg};--avatar-fg:${source.fg}">${esc(source.initial)}</div>` : `<div class="video-icon">${['mp3','m4a'].includes(j.format)?'♫':'▷'}</div>`;
    return `<article class="job-card">${sourceIcon}<div><a class="job-title" href="https://www.youtube.com/watch?v=${esc(j.video_id)}" target="_blank" rel="noreferrer">${esc(j.title)}</a><div class="job-meta"><span>${esc(j.channel_name)}</span><span>${j.format.toUpperCase()} · ${quality(j)}</span><span>${timeText(j.created_at)}</span><span>已尝试 ${j.attempts} 次</span></div>${j.status === 'downloading' ? `<div class="progress"><span style="width:${j.progress}%"></span></div><div class="progress-label">${esc(j.stage)} · ${j.progress.toFixed(1)}% ${esc(j.speed)} ${j.eta?'· 剩余 '+esc(j.eta):''}</div>` : ''}${j.status === 'retrying' ? `<div class="progress-label">计划重试：${timeText(j.available_at)}</div>` : ''}${j.filepath ? `<div class="filepath">${esc(j.filepath)}</div>`:''}${j.error?`<details class="card-error" data-error="${j.kind}:${j.id}" ${expanded.has(`${j.kind}:${j.id}`)?'open':''}><summary>查看失败原因</summary><p>${esc(j.error)}</p></details>`:''}</div><div class="job-controls"><span class="pill ${j.status==='failed'?'error':j.status==='cancelled'?'paused':''}">${statuses[j.status]||esc(j.status)}</span>${active?`<button class="button small" data-job-action="cancel" data-kind="${j.kind}" data-id="${j.id}">取消</button>`:''}${['failed','cancelled','retrying'].includes(j.status)?`<button class="button small" data-job-action="retry" data-kind="${j.kind}" data-id="${j.id}">重试</button>`:''}${j.status==='completed'?`<button class="button small" data-job-action="open-folder" data-kind="${j.kind}" data-id="${j.id}">打开文件夹 ↗</button><a class="button small" href="/api${path}/file">获取文件 ↓</a>`:''}</div></article>`;
  }).join('') : empty('这里暂时没有下载任务','频道发现新视频后会自动加入队列。默认首次扫描只建立记录；添加频道时也可以选择先下载最近几条。');
}
function renderSettings() {
  if (!settingsDirty) {
    for (const [key,value] of Object.entries(state.settings)) {
      const input = $('#settings-form').elements[key];
      if (input) input.value = value;
    }
  }
  const d = state.diagnostics;
  const rows = [['Python',d.python],['yt-dlp',d.yt_dlp],['FFmpeg',d.ffmpeg?'已就绪':null],['ffprobe',d.ffprobe?'已就绪':null],['Node.js',d.node?'已就绪':null],['YouTube EJS',d.ejs]];
  $('#diagnostics-list').innerHTML = rows.map(([name,value]) => `<div class="diagnostic-row"><span>${name}</span><b class="${value?'':'missing'}">${esc(value || '未安装')}</b></div>`).join('');
  const missing = rows.filter(([,value])=>!value).map(([name])=>name);
  $('#dependency-banner').hidden = !missing.length;
  $('#dependency-banner').textContent = '运行环境缺少：' + missing.join('、') + '。请按 README 安装后重启服务。';
  $('#pause-scheduler').textContent = state.settings.paused ? '▶ 恢复调度' : 'Ⅱ 暂停调度';
  $('#default-auth-banner').hidden = !state.auth?.must_change;
  if (!authDirty && state.auth) $('#auth-form').elements.username.value = state.auth.username;
  const oldService = !Array.isArray(state.manual_jobs) || !state.settings.manual_output_dir;
  $('#manual-service-warning').hidden = !oldService;
  $('#inspect-video').disabled = oldService;
  $('#manual-destination').textContent = state.settings.manual_output_dir ? '保存位置：' + state.settings.manual_output_dir : '';
  $('#batch-destination').textContent = state.settings.manual_output_dir ? '保存位置：' + state.settings.manual_output_dir + ' / 频道名' : '';
  renderRuntimeReport();
}
function renderRuntimeReport() {
  if (!runtimeReport) return;
  $('#diagnostics-list').innerHTML = runtimeReport.components.map(item => {
    const version = item.current || '未安装';
    const latest = item.update_available ? ` → ${item.latest}` : item.latest ? ' · 最新' : '';
    const status = item.update_available ? '可升级' : item.usable ? '可用' : '不可用';
    return `<div class="diagnostic-row runtime-row"><span>${esc(item.name)}<small>${esc(item.detail || '')}</small></span><b class="${item.usable?'':'missing'}">${esc(version + latest)}<small>${status}</small></b></div>`;
  }).join('');
  const checked = new Date(runtimeReport.checked_at * 1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});
  const errors = runtimeReport.lookup_errors?.length ? '；软件源查询未全部完成' : '';
  $('#runtime-check-result').textContent = runtimeReport.usable ? `环境可用 · ${checked}${errors}` : `需要处理：${runtimeReport.missing.join('、')} · ${checked}${errors}`;
  $('#runtime-check-result').classList.toggle('runtime-error', !runtimeReport.usable);
  $('#upgrade-runtime').hidden = !runtimeReport.updates.length;
  $('#upgrade-runtime').textContent = runtimeReport.updates.length ? `升级 ${runtimeReport.updates.length} 项` : '升级可更新组件';
}
async function refresh() {
  if (refreshBusy || restarting) return;
  refreshBusy = true;
  try {
    state = await api('/state');
    $('#connection-banner').hidden = true;
    $('#connection-dot').classList.remove('offline');
    $('#connection').textContent = state.settings.paused ? '调度已暂停' : '服务运行中';
    $('#restart-service').disabled = false;
    $('#restart-service').title = state.instance_id ? '正常重启后台服务；未完成任务会在重启后恢复' : '当前后台尚不支持网页重启，请先手动重启一次';
    for (const key of ['channels','completed','pending','failed']) $('#stat-' + key).textContent = state.stats[key];
    $('#nav-count').textContent = state.stats.channels;
    $('#queue-count').textContent = state.stats.pending;
    renderChannels(); renderJobs(); renderSettings();
  } catch (e) {
    $('#connection-banner').hidden = false;
    $('#connection-dot').classList.add('offline');
    $('#connection').textContent = '连接已断开';
    $('#restart-service').disabled = true;
  } finally { refreshBusy = false; }
}
$('#restart-service').addEventListener('click', async e => {
  if (!state?.instance_id) return toast('当前后台仍是旧版本，需要手动重启一次；之后就能使用此按钮。', true);
  if (restarting) return;
  const button = e.currentTarget, before = state.instance_id;
  restarting = true; button.disabled = true; button.textContent = '↻ 正在重启…';
  $('#connection').textContent = '正在重启服务';
  try {
    try { await api('/restart', 'POST'); }
    catch(error) { if (!['TypeError','TimeoutError'].includes(error.name)) throw error; }
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      try {
        const next = await api('/state');
        if (next.instance_id && next.instance_id !== before) {
          restarting = false; state = next; await refresh(); toast('服务已重新启动'); return;
        }
      } catch (_) { /* The socket closes briefly during restart. */ }
    }
    throw new Error('重启尚未完成，请检查服务日志或启动窗口');
  } catch(error) { toast(error.message, true); }
  finally { restarting = false; button.textContent = '↻ 重启服务'; await refresh(); }
});
$('#logout').addEventListener('click', async () => {
  try { await api('/auth/logout', 'POST'); }
  catch (_) { /* A stale session is already signed out. */ }
  location.href = '/login';
});
function openChannel(id = null) {
  editId = id;
  const form = $('#channel-form'); form.reset();
  $('#channel-error').hidden = true;
  ['url-field','tab-field','initial-field','name-optional'].forEach(key => $('#' + key).hidden = id !== null);
  $('#enabled-field').hidden = id === null;
  form.elements.urls.required = id === null;
  form.elements.name.required = id !== null;
  $('#dialog-title').textContent = id === null ? '添加喜欢的频道' : '编辑频道偏好';
  $('#save-channel').textContent = id === null ? '添加并开始监控' : '保存频道设置';
  $('#schedule-keep-option').hidden = id === null;
  form.elements.schedule_mode.value = id === null ? 'now' : 'keep';
  if (id !== null) {
    const c = state.channels.find(c => c.id === id);
    for (const key of ['name','interval_minutes','format','resolution']) form.elements[key].value = c[key];
    form.elements.enabled.checked = !!c.enabled;
  }
  updateResolution(); updateSchedule();
  $('#channel-dialog').showModal();
}
function updateResolution() {
  const f = $('#channel-form'); f.elements.resolution.disabled = ['mp3','m4a'].includes(f.elements.format.value);
}
function localDatetime(timestamp = Date.now() + 5 * 60 * 1000) {
  const date = new Date(timestamp - new Date(timestamp).getTimezoneOffset() * 60000);
  return date.toISOString().slice(0,16);
}
function updateSchedule() {
  const f = $('#channel-form'), scheduled = f.elements.schedule_mode.value === 'scheduled';
  $('#schedule-time-field').hidden = !scheduled;
  f.elements.start_at.required = scheduled;
  f.elements.start_at.min = localDatetime(Date.now() + 30000);
  if (scheduled && !f.elements.start_at.value) f.elements.start_at.value = localDatetime();
}
$('#channel-form').elements.format.addEventListener('change', updateResolution);
$('#channel-form').elements.schedule_mode.addEventListener('change', updateSchedule);
$('#add-channel').addEventListener('click', () => openChannel());
$$('[data-close]').forEach(b => b.addEventListener('click', () => $('#' + b.dataset.close).close()));
$$('[data-view]').forEach(b => b.addEventListener('click', () => showView(b.dataset.view)));
$$('[data-channel-filter]').forEach(b => b.addEventListener('click', () => {
  channelFilter = b.dataset.channelFilter;
  $$('[data-channel-filter]').forEach(x => x.classList.toggle('active', x === b));
  if (state) renderChannels(true);
}));
$$('[data-job-filter]').forEach(b => b.addEventListener('click', () => {
  jobFilter = b.dataset.jobFilter;
  $$('[data-job-filter]').forEach(x => x.classList.toggle('active', x === b));
  if (state) renderJobs(true);
}));
$('#channel-search').addEventListener('input', () => { if (state) renderChannels(true); });
$('#channel-form').addEventListener('submit', async e => {
  e.preventDefault();
  const f = e.currentTarget, button = $('#save-channel'); button.disabled = true;
  const scheduleMode = f.elements.schedule_mode.value;
  const startAt = scheduleMode === 'scheduled' ? new Date(f.elements.start_at.value).getTime() / 1000 : null;
  if (scheduleMode === 'scheduled' && (!Number.isFinite(startAt) || startAt * 1000 < Date.now() - 30000)) {
    $('#channel-error').textContent = '请选择当前时间之后的运行时间'; $('#channel-error').hidden = false; button.disabled = false; return;
  }
  const common = {name:f.elements.name.value.trim(),interval_minutes:Number(f.elements.interval_minutes.value),format:f.elements.format.value,resolution:Number(f.elements.resolution.value),schedule_mode:scheduleMode,start_at:startAt};
  try {
    if (editId !== null) {
      await api('/channels/' + editId, 'PUT', {...common,enabled:f.elements.enabled.checked});
    } else {
      const urls = f.elements.urls.value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
      if (!urls.length) throw new Error('请至少填写一个频道链接');
      await api('/channels', 'POST', {channels:urls.map(url=>({...common,url,tab:f.elements.tab.value,initial_count:Number(f.elements.initial_count.value)}))});
    }
    $('#channel-dialog').close();
    const scheduleText = scheduleMode === 'scheduled' ? `，将在 ${new Date(startAt * 1000).toLocaleString('zh-CN')} 首次运行` : scheduleMode === 'now' ? '，已安排立即扫描' : '';
    toast((editId === null ? '频道已添加' : '频道设置已保存') + scheduleText); await refresh();
  } catch (e) { $('#channel-error').textContent = e.message; $('#channel-error').hidden = false; }
  finally { button.disabled = false; }
});
$('#channel-grid').addEventListener('click', async e => {
  const b = e.target.closest('[data-action]'); if (!b) return;
  const id = Number(b.dataset.id);
  if (b.dataset.action === 'add') return openChannel();
  if (b.dataset.action === 'edit') return openChannel(id);
  if (b.dataset.action === 'delete') {
    deleteId = id; $('#confirm-message').textContent = `确定移除「${state.channels.find(c=>c.id===id).name}」？`;
    return $('#confirm-dialog').showModal();
  }
  if (b.dataset.action === 'monitor') {
    if (pendingChannels.has(id)) return;
    const enabled = b.dataset.enabled === '1';
    pendingChannels.add(id); renderChannels(true);
    try {
      const latest = await api('/state');
      const channel = latest.channels.find(c => c.id === id);
      if (!channel) throw new Error('频道已被移除，请刷新页面');
      const {name,interval_minutes,format,resolution} = channel;
      await api('/channels/' + id, 'PUT', {name,interval_minutes,format,resolution,enabled});
      state.channels = state.channels.map(c => c.id === id ? {...c,enabled} : c);
      state.settings.paused = latest.settings.paused;
      if (enabled && !latest.settings.paused) {
        try { await api('/channels/' + id + '/scan', 'POST'); }
        catch(e) { toast('监控已开启，但立即扫描未安排成功：' + e.message,true); return; }
      }
      toast(enabled ? (latest.settings.paused ? '频道已启用；全局调度仍暂停，请在下载任务中恢复调度' : '已开始监控，已安排扫描') : '已暂停此频道监控，正在进行的任务会继续完成');
    } catch(e) { toast(e.message,true); }
    finally { pendingChannels.delete(id); renderChannels(true); await refresh(); }
    return;
  }
  b.disabled = true;
  try { await api('/channels/' + id + '/scan','POST'); toast('已加入扫描计划'); await refresh(); }
  catch(e) { toast(e.message,true); }
  finally { b.disabled = false; }
});
$('#confirm-form').addEventListener('submit', async e => {
  e.preventDefault(); const button = $('button[type=submit]', e.currentTarget); button.disabled = true;
  try { await api('/channels/' + deleteId,'DELETE'); $('#confirm-dialog').close(); toast('频道已移除，本地文件已保留'); await refresh(); }
  catch(e) { toast(e.message,true); }
  finally { button.disabled = false; }
});
$('#scan-all').addEventListener('click', async e => {
  const b = e.currentTarget; b.disabled = true;
  try { await api('/scan','POST'); toast('已安排扫描所有启用的频道'); await refresh(); }
  catch(e) { toast(e.message,true); }
  finally { b.disabled = false; }
});
$('#job-list').addEventListener('click', async e => {
  const b = e.target.closest('[data-job-action]'); if (!b) return; b.disabled = true;
  try {
    const path = b.dataset.kind === 'manual' && b.dataset.jobAction !== 'open-folder' ? '/manual/jobs/' + b.dataset.id + '/action/' + b.dataset.jobAction : (b.dataset.kind === 'manual' ? '/manual/jobs/' : '/jobs/') + b.dataset.id + '/' + b.dataset.jobAction;
    await api(path,'POST');
    const messages = {retry:'任务已重新加入队列',cancel:'任务已取消','open-folder':'已打开文件所在目录'};
    toast(messages[b.dataset.jobAction]);
    if (b.dataset.jobAction !== 'open-folder') await refresh();
  }
  catch(e) { toast(e.message,true); }
  finally { b.disabled = false; }
});
$('#settings-form').addEventListener('input', e => { if (e.target.id !== 'cookie-file-picker') { settingsDirty = true; $('#settings-dirty').textContent = '有未保存的修改'; } });
$('#check-runtime').addEventListener('click', async e => {
  const button = e.currentTarget, result = $('#runtime-check-result');
  button.disabled = true; $('#upgrade-runtime').disabled = true; result.textContent = '正在检查组件版本与可用性…';
  try {
    runtimeReport = await api('/runtime/check', 'POST', undefined, 45000);
    renderRuntimeReport();
    toast(runtimeReport.usable ? '运行环境检查完成' : '检查完成，有组件需要处理', !runtimeReport.usable);
  } catch(error) { result.textContent = '检查失败：' + error.message; toast(error.message, true); }
  finally { button.disabled = false; $('#upgrade-runtime').disabled = false; }
});
$('#upgrade-runtime').addEventListener('click', async e => {
  const button = e.currentTarget, check = $('#check-runtime'), result = $('#runtime-check-result');
  button.disabled = true; check.disabled = true; result.textContent = '正在升级项目组件，完成前请勿关闭服务…';
  try {
    const response = await api('/runtime/upgrade', 'POST', undefined, 660000);
    runtimeReport = response.environment; renderRuntimeReport(); toast(response.message);
    await refresh();
  } catch(error) { result.textContent = '升级失败：' + error.message; toast(error.message, true); }
  finally { button.disabled = false; check.disabled = false; }
});
$('#import-cookies').addEventListener('click', () => $('#cookie-file-picker').click());
async function importCookieFile(file) {
  if (!file) return;
  const button = $('#import-cookies'), status = $('#cookie-import-status');
  const wasDirty = settingsDirty;
  button.disabled = true; status.textContent = '正在读取并检查文件…';
  try {
    if (!file.name.toLowerCase().endsWith('.txt')) throw new Error('请选择 .txt 格式的 Cookies 文件');
    if (file.size > 2_000_000) throw new Error('Cookies 文件不能超过 2 MB');
    const result = await api('/settings/cookies/import', 'POST', {filename:file.name,content:await file.text()});
    state.settings.cookies_file = result.cookies_file;
    $('#settings-form').elements.cookies_file.value = result.cookies_file;
    settingsDirty = wasDirty;
    const auth = result.auth_detected ? '检测到登录 Cookie 条目，实际有效性需通过视频解析确认' : '未识别常见登录项，可能无法访问需要登录的视频';
    status.textContent = `导入成功：保留 ${result.kept} 条，忽略 ${result.skipped} 条；${auth}`;
    toast(result.retried_jobs ? `Cookies 已启用，${result.retried_jobs} 个验证失败任务已重新排队` : 'Cookies 已导入并自动启用');
    await refresh();
  } catch(error) {
    status.textContent = '导入失败：' + error.message; toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}
$('#cookie-file-picker').addEventListener('change', async e => {
  await importCookieFile(e.currentTarget.files[0]); e.currentTarget.value = '';
});
const cookieZone = $('#cookie-drop-zone');
for (const eventName of ['dragenter','dragover']) cookieZone.addEventListener(eventName, e => {
  e.preventDefault(); e.stopPropagation(); cookieZone.classList.add('dragging');
});
for (const eventName of ['dragleave','dragend']) cookieZone.addEventListener(eventName, e => {
  e.preventDefault(); e.stopPropagation(); cookieZone.classList.remove('dragging');
});
cookieZone.addEventListener('drop', async e => {
  e.preventDefault(); e.stopPropagation(); cookieZone.classList.remove('dragging');
  const files = [...e.dataTransfer.files];
  if (files.length !== 1) return toast('请一次拖入一个 cookies.txt 文件', true);
  await importCookieFile(files[0]);
});

let directoryState = null, directoryTarget = 'output_dir';
async function loadDirectories(path) {
  const list = $('#directory-list'), error = $('#directory-error');
  list.innerHTML = '<div class="muted directory-loading">正在读取目录…</div>'; error.hidden = true;
  try {
    directoryState = await api('/filesystem/directories?path=' + encodeURIComponent(path || ''));
    $('#directory-current').value = directoryState.current;
    $('#directory-up').disabled = !directoryState.parent;
    $('#directory-roots').innerHTML = directoryState.roots.map(root => `<button type="button" class="button small" data-root="${esc(root)}">${esc(root)}</button>`).join('');
    list.innerHTML = directoryState.directories.length ? directoryState.directories.map(item => `<button type="button" class="directory-item" data-path="${esc(item.path)}"><span>▱</span>${esc(item.name)}</button>`).join('') : '<div class="muted directory-loading">这个目录下没有子目录</div>';
    if (directoryState.truncated) list.insertAdjacentHTML('beforeend','<div class="muted directory-loading">仅显示前 500 个目录</div>');
  } catch(e) {
    directoryState = null; list.innerHTML = ''; error.textContent = e.message; error.hidden = false;
  }
}
$('#browse-output').addEventListener('click', async () => {
  directoryTarget = 'output_dir';
  $('#directory-dialog').showModal(); await loadDirectories($('#settings-form').elements.output_dir.value);
});
$('#browse-manual-output').addEventListener('click', async () => {
  directoryTarget = 'manual_output_dir';
  $('#directory-dialog').showModal(); await loadDirectories($('#settings-form').elements.manual_output_dir.value);
});
$('#directory-up').addEventListener('click', () => { if (directoryState?.parent) loadDirectories(directoryState.parent); });
$('#directory-roots').addEventListener('click', e => { const b=e.target.closest('[data-root]'); if (b) loadDirectories(b.dataset.root); });
$('#directory-list').addEventListener('click', e => { const b=e.target.closest('[data-path]'); if (b) loadDirectories(b.dataset.path); });
$('#select-directory').addEventListener('click', () => {
  if (!directoryState) return;
  $('#settings-form').elements[directoryTarget].value = directoryState.current;
  settingsDirty = true; $('#settings-dirty').textContent = '已选择目录，请保存设置';
  $('#directory-dialog').close();
});
$('#settings-form').addEventListener('submit', async e => {
  e.preventDefault(); if (!state) return;
  const f = e.currentTarget, b = $('button[type=submit]',f); b.disabled = true;
  try {
    await api('/settings','PUT',{...state.settings,output_dir:f.elements.output_dir.value,manual_output_dir:f.elements.manual_output_dir.value,proxy:f.elements.proxy.value,cookies_file:f.elements.cookies_file.value,retries:Number(f.elements.retries.value),concurrent_downloads:Number(f.elements.concurrent_downloads.value),scan_depth:Number(f.elements.scan_depth.value)});
    settingsDirty = false; $('#settings-dirty').textContent = ''; toast('设置已保存'); await refresh();
  } catch(e) { toast(e.message,true); }
  finally { b.disabled = false; }
});
$('#auth-form').addEventListener('input', () => { authDirty = true; });
$('#auth-form').addEventListener('submit', async e => {
  e.preventDefault();
  const form = e.currentTarget, button = $('button[type=submit]', form), error = $('#auth-error');
  error.hidden = true;
  const newPassword = form.elements.new_password.value;
  if (newPassword !== form.elements.confirm_password.value) {
    error.textContent = '两次输入的新密码不一致'; error.hidden = false; return;
  }
  button.disabled = true;
  try {
    await api('/auth', 'PUT', {username:form.elements.username.value.trim(),
      current_password:form.elements.current_password.value, new_password:newPassword});
    authDirty = false;
    form.reset();
    toast('管理账号已更新，请用新账号重新登录');
    setTimeout(() => { location.href = '/login'; }, 800);
  } catch (e) {
    error.textContent = e.message; error.hidden = false;
  } finally { button.disabled = false; }
});
$('#pause-scheduler').addEventListener('click', async e => {
  if (!state) return; const b = e.currentTarget; b.disabled = true;
  try { await api('/settings','PUT',{...state.settings,paused:!state.settings.paused}); toast(state.settings.paused?'已恢复自动调度':'已暂停启动新任务，当前任务会继续完成'); await refresh(); }
  catch(e) { toast(e.message,true); }
  finally { b.disabled = false; }
});
window.addEventListener('beforeunload', e => { if (settingsDirty || authDirty) { e.preventDefault(); e.returnValue = ''; } });
$('#manual-inspect-form').addEventListener('submit', async e => {
  e.preventDefault(); const b = $('#inspect-video'); b.disabled = true; b.textContent = '正在解析…';
  $('#manual-error').hidden = true; $('#manual-preview').hidden = true; inspectedVideo = null;
  try {
    const result = await api('/manual/inspect','POST',{url:e.currentTarget.elements.url.value.trim()},150000);
    inspectedVideo = result;
    $('#manual-title').textContent = result.title;
    const duration = result.duration ? ` · ${Math.floor(result.duration/60)} 分 ${Math.round(result.duration%60)} 秒` : '';
    $('#manual-meta').textContent = `${result.uploader || 'YouTube 视频'}${duration} · ${result.qualities.length} 档画质`;
    $('#manual-download-form').elements.resolution.innerHTML = result.qualities.map(height => `<option value="${height}">${height}p${height>=2160?' · 4K':height>=1440?' · 2K':height>=1080?' · 全高清':height>=720?' · 高清':''}</option>`).join('');
    $('#manual-preview').hidden = false;
  } catch(error) { $('#manual-error').textContent = error.name === 'TimeoutError' ? '解析超时，请检查网络或代理' : error.message; $('#manual-error').hidden = false; }
  finally { b.disabled = false; b.textContent = '解析视频'; }
});
$('#manual-inspect-form').elements.url.addEventListener('input', () => { inspectedVideo = null; $('#manual-preview').hidden = true; });
$('#manual-download-form').addEventListener('submit', async e => {
  e.preventDefault(); if (!inspectedVideo) return;
  const f = e.currentTarget, b = $('button[type=submit]', f); b.disabled = true;
  try {
    await api('/manual/jobs','POST',{url:inspectedVideo.url,format:f.elements.format.value,resolution:Number(f.elements.resolution.value)});
    toast('视频已加入下载队列'); await refresh(); showView('downloads');
  } catch(error) { toast(error.message,true); }
  finally { b.disabled = false; }
});
$('#clear-jobs').addEventListener('click', () => $('#clear-jobs-dialog').showModal());
$('#clear-jobs-form').addEventListener('submit', async e => {
  e.preventDefault();
  const button = $('button[type=submit]', e.currentTarget); button.disabled = true;
  try {
    const result = await api('/jobs', 'DELETE');
    $('#clear-jobs-dialog').close();
    toast(result.total ? `已清理 ${result.total} 条任务记录，视频文件已保留` : '没有可清理的任务记录');
    await refresh();
  } catch(error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
function batchDate(entry) {
  if (entry.timestamp) return new Date(entry.timestamp * 1000).toLocaleDateString('zh-CN');
  return /^\d{8}$/.test(entry.upload_date || '') ? `${entry.upload_date.slice(0,4)}-${entry.upload_date.slice(4,6)}-${entry.upload_date.slice(6,8)}` : '日期未知';
}
function batchDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '时长未知';
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), secs = Math.floor(seconds % 60);
  return hours ? `${hours}:${String(minutes).padStart(2,'0')}:${String(secs).padStart(2,'0')}` : `${minutes}:${String(secs).padStart(2,'0')}`;
}
function renderBatchVideos() {
  if (!batchSession) return;
  const query = $('#batch-filter').value.trim().toLowerCase();
  const matches = batchSession.entries.filter(entry => entry.title.toLowerCase().includes(query));
  const visible = matches.slice(0, 200);
  $('#batch-channel-name').textContent = batchSession.channel_name;
  $('#batch-summary').textContent = `已加载 ${batchSession.loaded} 条 · 每次继续加载 50 条 · 结果保留 30 分钟`;
  $('#batch-selected-count').textContent = `已选 ${batchSelected.size} 条`;
  $('#enqueue-batch').disabled = !batchSelected.size;
  $('#batch-load-more').hidden = !batchSession.has_more;
  const displayNote = matches.length > 200 ? `；当前显示前 200 条，请用标题筛选其余 ${matches.length - 200} 条` : '';
  $('#batch-limit-note').textContent = (batchSession.loaded >= 5000 ? '已达到单次扫描上限 5000 条' : batchSession.has_more ? '只在需要时继续加载，避免大频道扫描过久' : '已读取到频道列表末尾') + displayNote;
  $('#batch-video-list').innerHTML = visible.length ? visible.map(entry => `<label class="batch-video-row"><input type="checkbox" value="${esc(entry.id)}" ${batchSelected.has(entry.id)?'checked':''}><span class="batch-video-mark">▷</span><span class="batch-video-content"><a href="https://www.youtube.com/watch?v=${esc(entry.id)}" target="_blank" rel="noreferrer">${esc(entry.title)}</a><small>${batchDate(entry)} · ${batchDuration(entry.duration)}</small></span></label>`).join('') : `<div class="batch-empty">${query?'没有匹配已加载视频的标题':'这一批没有可下载的公开视频'}</div>`;
}
$('#batch-scan-form').addEventListener('submit', async e => {
  e.preventDefault();
  const button = $('#scan-batch-channel'), error = $('#batch-error');
  button.disabled = true; button.textContent = '正在读取…'; error.hidden = true;
  $('#batch-results').hidden = true; batchSession = null; batchSelected.clear();
  try {
    batchSession = await api('/batch-channel/scan', 'POST', {url:e.currentTarget.elements.url.value.trim()}, 200000);
    $('#batch-filter').value = ''; $('#batch-results').hidden = false; renderBatchVideos();
    toast(`已读取 ${batchSession.loaded} 条频道视频`);
  } catch(errorValue) { error.textContent = errorValue.name === 'TimeoutError' ? '频道读取超时，请检查网络或代理' : errorValue.message; error.hidden = false; }
  finally { button.disabled = false; button.textContent = '扫描频道'; }
});
$('#batch-scan-form').elements.url.addEventListener('input', () => {
  batchSession = null; batchSelected.clear(); $('#batch-results').hidden = true;
});
$('#batch-load-more').addEventListener('click', async e => {
  if (!batchSession) return;
  const button = e.currentTarget; button.disabled = true; button.textContent = '正在继续读取…';
  try {
    const page = await api(`/batch-channel/${batchSession.token}/more`, 'POST', undefined, 200000);
    batchSession.entries.push(...page.entries); batchSession.loaded = page.loaded; batchSession.has_more = page.has_more; batchSession.expires_at = page.expires_at;
    renderBatchVideos(); toast(page.entries.length ? `又加载了 ${page.entries.length} 条视频` : '没有更多可下载的视频');
  } catch(error) { toast(error.name === 'TimeoutError' ? '继续读取超时，请稍后重试' : error.message, true); }
  finally { button.disabled = false; button.textContent = '继续加载 50 条'; }
});
$('#batch-video-list').addEventListener('change', e => {
  const checkbox = e.target.closest('input[type=checkbox]'); if (!checkbox) return;
  if (checkbox.checked) batchSelected.add(checkbox.value); else batchSelected.delete(checkbox.value);
  renderBatchVideos();
});
$('#batch-select-all').addEventListener('click', () => {
  if (!batchSession) return;
  batchSession.entries.forEach(entry => batchSelected.add(entry.id)); renderBatchVideos();
});
$('#batch-clear').addEventListener('click', () => { batchSelected.clear(); renderBatchVideos(); });
$('#batch-filter').addEventListener('input', renderBatchVideos);
$('#batch-download-form').elements.format.addEventListener('change', e => {
  $('#batch-download-form').elements.resolution.disabled = ['mp3','m4a'].includes(e.target.value);
});
$('#batch-download-form').addEventListener('submit', async e => {
  e.preventDefault(); if (!batchSession || !batchSelected.size) return;
  const form = e.currentTarget, button = $('#enqueue-batch'); button.disabled = true; button.textContent = '正在加入队列…';
  try {
    const result = await api(`/batch-channel/${batchSession.token}/jobs`, 'POST', {video_ids:[...batchSelected],format:form.elements.format.value,resolution:Number(form.elements.resolution.value)}, 60000);
    batchSelected.clear();
    const skipped = result.skipped ? `，${result.skipped} 条已在任务中并跳过` : '';
    toast(`已加入 ${result.added} 个下载任务${skipped}`); await refresh(); showView('downloads');
  } catch(error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = '下载所选视频 ↓'; }
});
window.addEventListener('hashchange', () => { const view = location.hash.slice(1); if (['channels','manual','batch','downloads','settings'].includes(view)) showView(view); });
showView(['channels','manual','batch','downloads','settings'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'channels');
refresh(); setInterval(refresh, 3000);
