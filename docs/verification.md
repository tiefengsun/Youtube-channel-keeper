# 验证记录

验证日期：2026-09-17，Windows，Python 3.12.7、Node.js 22.19.0、yt-dlp 2026.8.19。

## 已通过

- `python -m pytest -q`：**66 passed**。覆盖核心调度/API、定时运行、Cookies 导入、跨平台目录浏览、目录命名、管理账号默认登录与修改，以及 5 个真实 yt-dlp + FFmpeg 媒体下载/转换测试。
- `node --check app/static/app.js`：JavaScript 语法检查通过。
- `python -m compileall -q app run.py`：Python 编译检查通过。
- `python -m pip check`：No broken requirements found。
- 浏览器人工交互：空状态、非法 URL 错误、编辑名称/格式/分辨率、任务重试、任务取消、环境诊断。
- 正常桌面视口与 390 × 844 窄屏检查：布局可用，无横向页面溢出；窄屏设置栏改为单列。
- 测试订阅和任务位于 `artifacts/ui-test`，正式数据目录不包含演示订阅。
- 最终服务烟雾测试：重复启动识别已有实例，`/api/shutdown` 触发正常生命周期关闭，重新启动后状态接口和页面正常。
- Ubuntu 24.04 服务器：systemd 用户服务持续运行，局域网地址 `10.168.165.219:8765` 可连接；未认证请求返回 401，认证后页面/API 返回 200。服务器上的 Node.js、yt-dlp/EJS、FFmpeg/ffprobe 均已就绪。

真实媒体测试使用本机 FFmpeg 生成 1 秒带声音的片段，经临时 HTTP 服务提供给 yt-dlp。保留生产中的格式选择、进度解析、下载及后处理参数，仅用本地信息文件替代 YouTube 提取。实际生成 MP4、MKV、WebM、MP3、M4A，通过 ffprobe 验证音视频流与画质。

## 外部条件限制

真实 YouTube 测试未完成下载。旧 yt-dlp 测试视频 `BaW_jenozKc` 返回 unavailable；公开短视频 `jNQXAC9IVRw` 返回 `Sign in to confirm you’re not a bot`。没有读取用户浏览器 Cookies 或登录账户，没有尝试绕过验证。可由用户在工具设置中提供自己的有效 Cookies 文件后再测试。

未实际启用 Windows 登录自启，也未验证重启 Windows 后的自启行为；交付了可选创建/移除登录启动快捷方式脚本。

测试依赖输出了两条上游弃用警告（Starlette TestClient 的 httpx 兼容层、AnyIO 别名），没有测试失败，不影响运行服务。
