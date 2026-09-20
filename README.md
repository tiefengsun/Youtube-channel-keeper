# Youtube-channel-keeper

一个可运行在 Windows 本机或 Ubuntu 24.04 服务器的 YouTube 频道自动下载工具。使用中文网页管理多个频道，后台按周期扫描更新，通过 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 下载，FFmpeg 负责音视频合并与格式转换。无需 YouTube API Key。

## Windows 安装与部署

需要：

- [Python](https://www.python.org/downloads/windows/) 3.10+，安装时添加到 PATH。
- [Node.js](https://nodejs.org/) 22+，供 YouTube JavaScript 提取使用。
- [FFmpeg](https://ffmpeg.org/download.html) 与 ffprobe，将所在 `bin` 目录加入 PATH。
- [Git](https://git-scm.com/downloads/win)，用于克隆项目；如果下载源码 ZIP，则不需要 Git。

先克隆项目（或从 GitHub 下载源码 ZIP 并解压）：

```powershell
git clone https://github.com/tiefengsun/Youtube-channel-keeper.git
cd Youtube-channel-keeper
```

双击 `start.cmd`，会自动创建 `.venv`、安装 Python 依赖并启动服务。首次安装依赖时会暂时显示 CMD 进度；服务启动成功后窗口自动关闭，后台运行不会在桌面驻留 CMD。或者在项目目录以前台方式运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

安装/更新依赖后重启服务。YouTube 提取失效时可以先运行 `update.cmd` 升级 yt-dlp。`requirements-lock.txt` 记录本次测试环境的精确版本；需要复现时用它安装，但日常使用建议保持 yt-dlp 更新。

## Ubuntu 24.04 服务器安装与部署

以普通用户安装 Python 3.12、`python3-venv`、Git、FFmpeg/ffprobe，以及 [Node.js](https://nodejs.org/) 20+。Ubuntu 软件源中的 Node.js 版本可能低于要求，运行 `node --version` 确认版本。安装脚本要求项目位于运行用户的 **`~/channel-keeper`**，不要以 root 运行：

```bash
sudo apt update
sudo apt install -y python3 python3-venv git ffmpeg
git clone https://github.com/tiefengsun/Youtube-channel-keeper.git ~/channel-keeper
cd ~/channel-keeper
node --version
bash scripts/install-ubuntu.sh
```

脚本会创建 `.venv`、安装依赖，并启用 `systemd --user` 服务。为了退出 SSH 后仍继续运行，执行 `sudo loginctl enable-linger "$USER"`。查看状态和日志：

```bash
systemctl --user status channel-keeper
journalctl --user -u channel-keeper -f
```

默认服务只监听服务器的 `127.0.0.1:8765`。如需在可信局域网内直接访问，运行 `python3 scripts/configure-lan-ubuntu.py <服务器局域网IP>`，再运行 `systemctl --user restart channel-keeper`。随后打开 `http://<服务器局域网IP>:8765`，用默认账号 `keeper / keeper` 登录并立即在偏好设置中修改。如启用了防火墙，还需按实际局域网网段放行 TCP 8765。服务仅绑定指定的局域网 IP；包括本机地址在内的管理页面和 API 均需要登录。

局域网 HTTP 不提供传输加密，导入 Cookies 等敏感操作建议通过 SSH 隧道进行。若本机已有 8765 端口实例，可使用：

```text
ssh -N -L 8766:<服务器局域网IP>:8765 <用户名>@<服务器IP>
```

然后在本机浏览器打开 `http://127.0.0.1:8766`。服务器中的默认保存目录为 `~/channel-keeper/downloads`；可在偏好设置中改成服务器上其他可写的绝对路径。通过隧道访问时，“打开文件夹”只会尝试在服务器桌面打开目录；没有图形桌面的服务会提示复制服务器路径，浏览器也可使用“获取文件”下载到本机。

## 启动与使用

Windows 安装后双击 **`start.cmd`**，服务会在后台无窗口运行，启动成功后浏览器打开 **http://127.0.0.1:8765**。Ubuntu 安装后由 `systemd --user` 自动启动服务；使用服务器本机地址、局域网地址或上文的 SSH 隧道地址访问。

访问工具时会进入独立登录页，不再使用浏览器的 HTTP Basic 弹窗。首次登录的用户名和密码均为 **`keeper`**。页面会持续提示修改默认密码；在「偏好设置 → 管理账号」填写当前密码，即可修改用户名和密码。新密码至少 8 位，修改后所有旧会话失效，需使用新账号重新登录。账号和会话记录保存在 `data/keeper.sqlite3` 中，密码只以加盐摘要保存，会话凭证只以摘要保存；登录会话有效期为 24 小时。侧栏底部可退出登录。

1. 点击「添加频道」，粘贴频道链接或 `@频道名`，多个频道每行一个。
2. 设置扫描周期、首次运行时间、格式、分辨率，以及首次是否下载最近几条视频。
3. 默认「只下载今后的新视频」：首次成功扫描记录现有视频，之后发现的更新自动入队。首次扫描未成功前，不会开始计算“今后”。
4. 在「偏好设置」里选择保存目录，按需填写代理和 Cookies 文件。
5. 在「下载任务」中查看状态、重试、取消、打开文件所在目录，或获取已完成的文件。

「偏好设置 → 运行环境」可以检查 Python、Node.js、FFmpeg、ffprobe、yt-dlp 与 YouTube EJS 是否可用。页面会从 PyPI 查询项目组件的新版本，并可直接升级 yt-dlp 与 YouTube EJS 后重新检查；Python、Node.js 和 FFmpeg 属于系统组件，请通过 Windows 安装程序或 Ubuntu 包管理器升级。下载任务左侧会沿用所属频道的首字与颜色，方便在多频道队列中快速辨认来源；手动添加的单条视频继续使用独立的视频或音频标识。

也可以打开「单条视频」，粘贴 `youtube.com/watch`、Shorts 或 `youtu.be` 的单条视频链接。工具先解析标题、创作者、时长和实际可用分辨率，再让你选择画质与 MP4 / MKV / WebM 格式。提交后在「下载任务」查看进度；文件直接保存在「偏好设置 → 单条视频保存目录」。解析结果保留 30 分钟，过期后重新解析即可。所选分辨率是上限，如果下载时源画质发生变化，会选择不超过该值的最高可用画质。频道与单条视频共用下载并发额度，按入队时间领取；同时最多解析 2 条单条视频。

「频道下载」用于手动挑选频道历史视频，不会创建自动订阅。粘贴频道链接后先读取最近 50 条，可按需继续加载，每次增加 50 条；扫描会话保留 30 分钟，单次最多缓存 5000 条。页面支持标题筛选、逐条勾选、全选已加载内容，并将所选视频按统一格式和分辨率加入下载队列。为避免大型频道拖慢浏览器，列表一次最多渲染 200 条，其他已加载内容仍可通过标题筛选找到。文件保存在手动下载目录下以频道名建立的文件夹中，重复加入且仍在队列或已经完成的视频会自动跳过。

每张频道卡片底部都有「暂停监控 / 开始监控」按钮。暂停后停止启动该频道的新扫描和下载，已运行的任务继续完成；再次开始会立即安排一次扫描。如果全局调度已暂停，还需要在「下载任务」中恢复调度。

添加频道时可以选择「立即开始」或指定首次运行的日期时间。编辑现有频道时可以保持当前计划、立即扫描，或指定下次运行时间。指定时间只决定下一次扫描；该次完成后继续按照频道的扫描周期运行。时间以打开管理页面设备的本地时区为准。

关闭浏览器不会停止后台服务。Windows 后台服务使用 **`stop.cmd`** 停止；下次启动会恢复未完成任务。重复双击 `start.cmd` 只会打开已有服务，不会再启动一个实例。首次安装依赖时如果提前关闭 CMD，会中断安装；看到浏览器打开且 CMD 自动关闭后，服务已经独立在后台运行。若以自定义端口运行，可向该端口的 `/api/shutdown` 发送带 `X-Local-Request: 1` 的 POST。

侧栏底部的「重启服务」按钮会等待当前服务关闭、释放端口和数据锁，再重新加载 Python 代码；页面会等待新实例恢复并提示结果。正在下载的任务在重启后重新入队。Windows 本机和按本项目脚本安装的 Ubuntu systemd 服务均可使用。第一次升级到带此按钮的版本时，旧进程还不认识重启接口，需要手动重启一次；之后可直接使用按钮。

## Cookies 与网络配置

如果本机需要代理访问 YouTube，在「偏好设置 → 网络代理」填写例如 `http://127.0.0.1:7890`，端口以你的代理软件为准；留空由 yt-dlp 使用环境/系统代理。

YouTube 有时提示 **Sign in to confirm you're not a bot**。此时可导入你自己的有效 **Netscape 格式** Cookies 文件后重试。不要把 Cookies 发给别人；它可能包含登录凭据。

工具不会读取浏览器账户。每次扫描和下载会临时复制指定的 Cookies 文件，操作结束后清理副本，不修改原文件。文件过期后需要自行更新。Cookies 不保证消除所有站点限制，会员/地区/不可用视频仍受对应访问条件影响。

在 Chrome 中使用 Get cookies.txt 等扩展导出 Netscape 格式的 `cookies.txt`，然后打开「偏好设置 → Cookies 文件」，点击「导入…」，或者直接把 `.txt` 文件拖到**Cookies 路径输入框**。也可以手动填写服务所在电脑上已有文件的绝对路径。服务端会解析 Netscape 文件，只保留 YouTube、Google 和视频传输相关域名的未过期记录，保存到 `data/secrets/youtube-cookies.txt` 并自动启用。Cookie 值不会显示在页面或写入日志。因机器人验证失败而等待重试的任务会自动重新排队。

**Cookies 文件的到期时间还没到，不代表登录会话仍有效。** YouTube 会轮换正在使用的浏览器会话 Cookies；此前从普通 Chrome 窗口导出的文件可能在几小时后失效。若任务提示「Cookies 已失效」，请按 [yt-dlp 官方导出方法](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)：新建 Chrome 无痕窗口登录 YouTube，在同一标签打开 `https://www.youtube.com/robots.txt`，用扩展导出 `youtube.com` 的 Cookies，立即关闭整个无痕窗口，并避免再次使用该会话。随后在偏好设置重新导入文件。工具会暂停这类任务的无效自动重试；导入新文件后自动重新排队并恢复频道扫描。静态导出文件无法由本工具自动刷新。如果换新 Cookies 后仍报登录验证，还需要检查当前网络出口 IP、YouTube 对该账户的限制以及 yt-dlp 版本。

Google API Key 不能替代登录 Cookies。远程使用 Ubuntu 服务时，建议通过 SSH 隧道导入，避免 Cookies 经未加密的局域网 HTTP 传输。视频保存目录可以手动输入或点击“选择路径”浏览服务端目录；在 Windows 中显示盘符，在 Ubuntu 中可从 `/`、用户主目录以及 `/mnt` 等挂载位置选择。通过远程浏览器管理 Ubuntu 服务时，选择器显示的是 Ubuntu 服务器目录。

官方说明：[Cookies 使用说明](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)、[YouTube Cookies 导出说明](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)。只下载你有权保存的内容。

## 功能与使用注意事项

| 功能 | 说明 |
|---|---|
| 多频道订阅 | 支持 @handle、`/channel/UC…`、旧 `/c/…` 和 `/user/…` 链接；批量添加事务化，重复项会提示且整批不添加 |
| 单条视频下载 | 粘贴链接，先解析实际可用画质再选择格式与分辨率；使用独立保存目录 |
| 定时扫描 | 每频道独立设置，界面提供 5 分钟至每周；API 支持范围内的任意分钟数；可手动触发；最多两个频道同时扫描 |
| 视频栏目 | Videos 普通视频或 Shorts，可将同一频道的两个栏目分别添加；不包含直播栏目 |
| 视频格式 | MP4 / MKV / WebM，按可用源编码下载和合并，不做昂贵的视频重编码 |
| 音频格式 | MP3 / M4A，由 FFmpeg 提取或转换 |
| 分辨率 | 360p / 480p / 720p / 1080p / 1440p / 2160p / 可用最高画质 |
| 首次策略 | 仅追更，或下载最近 1、5、10、30、100 条后追更 |
| 队列 | 可在偏好设置选择 1–5 个同时下载任务；扫描线程独立运行，持久化去重、分段进度、单任务取消与重试 |
| 失败重试 | 默认额外重试 3 次，1 分钟起指数退避，最长 1 小时；耗尽后显示失败原因 |
| 重启恢复 | 正在下载的任务重新入队，保留 `.part` 文件；支持时由 yt-dlp 续传 |
| 本机诊断 | 显示 Python、yt-dlp、FFmpeg、ffprobe、Node.js、EJS 状态 |

注意这些具体约定：

- 分辨率是**上限**，系统会自动下载不超过该值的最高可用画质。比如设置 1080p：源视频最高只有 720p 时下载 720p；源视频有 4K 时下载 1080p；如果没有恰好 1080p，则选择 1080p 以下最高的一档。选择“可用最高画质”则不设上限。MP4 是容器，不代表视频一定是 H.264；4K/新编码的播放兼容性取决于播放器。
- 频道设置在新视频入队时保存到任务中，修改不会改变已有任务的格式。代理、Cookies、下载目录在每次任务开始时读取；已运行任务保持原配置。
- 默认枚举整个所选栏目以避免“只看最新几条”漏检；大频道首次及后续扫描可能较慢。单次扫描超时 15 分钟，失败保留旧基线，并在不超过 15 分钟后重试。
- 空栏目首次未能读到视频时不会建立基线。正在直播、预告、等待处理的直播视频先跳过，后续可用时再处理。
- 视频 ID 在每条订阅内部唯一。不同别名地址可能指向同一频道，建议统一使用同一个 @handle，避免重复订阅。
- 取消任务不会删除临时下载文件；重试时可继续使用。移除频道会删除该订阅及任务记录，**已下载的文件不删除**。
- 暂停全局调度或禁用频道只停止启动新任务，已经开始的任务继续运行。需要中断当前下载，请使用任务的「取消」。
- 调低并发数只影响之后启动的任务，不会打断已经开始的下载。较高并发会增加网络、磁盘和 YouTube 请求负载。
- 「偏好设置 → 每次扫描视频数」默认读取完整栏目。可改为最近 50、100、300、1000 或 5000 条，缩短大频道的扫描时间；超出范围的旧视频不会被发现，频道在两次扫描间更新超过上限时也可能漏检。不能漏检时保持默认完整栏目。
- 下载列表最多显示 300 条，当前下载优先，其余按最新记录排列。完整历史保留在 SQLite 数据库中。
- 运行服务的电脑休眠或关机后无法扫描；恢复运行后会扫描逾期频道。Ubuntu 配置 `loginctl enable-linger` 后，退出 SSH 不会停止用户服务。

## 文件与数据

```text
app/
  main.py          本机 API、访问保护、单实例锁
  models.py        配置及输入校验
  store.py         SQLite 事务、视频去重、下载队列
  engine.py        定时扫描、yt-dlp 子进程、重试和进度
  static/          中文管理界面（无需 npm 构建）
data/
  keeper.sqlite3   频道、设置、视频记录、下载任务
  keeper.log       轮转日志
downloads/
  BLUE TV/        以频道名命名的独立目录
tests/             核心测试与实际媒体转换测试
```

备份时先停止服务，再复制整个 `data` 目录和实际下载目录。不要仅在服务运行中复制 SQLite 主文件，避免遗漏 WAL 中的数据。

新下载的视频保存在「下载路径 / 频道名 / 视频文件」。频道名使用界面显示的名称（包括自定义备注）；Windows 不允许的字符会替换成下划线，同名频道或已有非本工具目录会追加频道编号。目录内的 `.channel-keeper.json` 用于识别归属，请保留。修改频道名后，新任务使用新名称创建目录；已下载的文件保留原路径。

默认只绑定 `127.0.0.1`；配置局域网地址后只绑定该私有 IP。有管理账号、Host、Origin 与写入请求标记校验。默认密码必须及时修改；局域网 HTTP 未加密，不要直接暴露到互联网。运行账户可以在设置中选择其有权限写入的目录。

## 可选：Windows 登录自启

完成首次安装后，手动运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\autostart.ps1
```

脚本仅在当前用户启动文件夹创建快捷方式，不需要管理员权限。下次登录后后台启动，不弹出浏览器；自行打开 http://127.0.0.1:8765 即可。

移除自启：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\autostart.ps1 -Remove
```

移除自启不会停止当前实例，使用 `stop.cmd` 停止。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
node --check app/static/app.js
.\.venv\Scripts\python.exe -m pip check
```

`tests/test_core.py` 覆盖首次基线、增量去重、原子回滚、并发领取、重启恢复、暂停、重试退避、URL 校验、本机请求保护、单实例锁和完整后台调度链。

`tests/test_media.py` 使用 FFmpeg 生成短测试片段，启动临时本机 HTTP 服务，用**真实 yt-dlp 子进程和 FFmpeg**验证 MP4/MKV/WebM/MP3/M4A 下载与转换，并通过 ffprobe 检查音视频流。此测试不依赖 YouTube 登录；缺少媒体依赖时明确跳过。

可选联网检查（只提取信息，不下载、不添加订阅）：

```powershell
.\.venv\Scripts\python.exe scripts/network_check.py
```

UI 测试数据生成脚本为 `scripts/seed_ui_test.py`，仅写入 `artifacts/ui-test` 并保持全局暂停，与正式 `data` 目录隔离。开发设计与实施计划见 `docs/plans/2026-09-17-channel-keeper-design.md`。
