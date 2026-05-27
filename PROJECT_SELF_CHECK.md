# SynCanvas 项目安全整理与全量自查

日期：2026-05-27  
范围：源码、启动器、普通/智能画布、AI 生图、数字人、fallback 网页启动器。  
原则：只整理源码、缓存、构建产物、备份文件和可再生成文件；不移动、不删除运行环境、用户素材、历史数据、输出结果和日志。

## 安全边界

本轮明确保留，不做删除或迁移：

- `python/`
- `index-tts-2/`
- `heygem-win-fix/`
- `assets/`
- `data/`
- `output/`
- `API/.env`
- `data/service-logs/`
- `launcher/SynCanvasLauncher.exe`

## 已完成整理

- 清理可再生成缓存：`launcher/bin/`、`launcher/obj/`、`tools/**/__pycache__`。
- 归档备份文件：`static/digital-human.html.bak` -> `_archive/cleanup-20260527/digital-human.html.bak`。
- `.gitignore` 增加 `_archive/`，避免归档目录误提交。
- 增加只读乱码扫描脚本：`tools/check_mojibake.py`。
- 修复确认的用户可见乱码：
  - `app/services/digital_human_service.py`：数字人任务不存在。
  - `app/services/digital_human_service.py`：仅可取消尚未开始的排队任务。
  - `static/js/canvas/state.js`：下载失败。
- 数字人动作视频链路：
  - 移除正常渲染路径里的前端隐藏 `<video>` / canvas 批量抓帧。
  - 动作视频卡、人物封面、当前视频预览默认使用 `poster_url` 或中性占位。
  - 真实视频只在悬停预览时懒加载。
  - 移除人物卡重复 `data-action="add-video"` 上传入口，只保留“动作视频库”旁的“上传动作视频”。
  - 多视频上传继续走批量上传/批量入库，最后统一刷新人物库。
- 统一弹窗：
  - `static/js/theme.js` 新增 `StudioDialog.alert()`、`StudioDialog.confirm()`、`StudioDialog.toast()`。
  - 直接页面调用的原生 `alert()` / `confirm()` 已替换为统一弹窗。
  - 目前只在 `theme.js` 内部保留 `window.confirm` 作为极端 fallback。
- 定时器生命周期：
  - `static/zimage.html` 队列状态刷新：页面隐藏时跳过请求，离开页面清理定时器。
  - `static/angle.html` WebSocket ping：连接关闭和页面离开时清理。
  - `tools/launcher_server.py` fallback 启动器：启动期轮询保留，补充停止条件说明。
  - `static/js/canvas/state.js` 普通画布远程同步：保留现有停止函数，补充生命周期说明。
- fallback 网页启动器：
  - 操作失败不再弹原生 `alert()`，改为写入当前日志区域。
- 首页全局角标：
  - `static/index.html` 未再发现 `ONLINE / QUEUE`、`nano-monitor`、2 秒 `/api/queue_status` 轮询残留。

## 验证结果

已通过：

- `python -B tools\check_mojibake.py --json`
  - 结果：`count = 0`。
- `node --check static/js/**/*.js`
  - 结果：36 个 JS 文件通过。
- `python -B` AST 解析 `app/`、`tools/`
  - 结果：53 个 Python 源文件通过。
- `python -B tools\service_supervisor.py --status --json --no-gpu --quick`
  - 结果：命令通过。
  - 当前 main / TTS / HeyGem 均为中性 `idle/stopped`。
  - 当前端口 3000 / 7861 / 7860 / 8383 均空闲。
  - 仍有 2 个 warning：`heygem-win-fix/heygem-win/hf_download` 和 `tf_download` 不存在。
- `dotnet build launcher\SynCanvasLauncher.csproj`
  - 沙箱内因 Windows SDK 用户目录权限失败。
  - 提权后通过：0 warning / 0 error。
- 弹窗扫描：
  - 页面直接原生 `alert(`：0 处。
  - 页面直接原生 `confirm(`：0 处。
  - 剩余命中仅为 `static/js/theme.js` 的统一弹窗 API 和 fallback。
- 数字人旧抓帧链路扫描：
  - 未发现 `prepareVisibleVideoPosters`、`captureVideoPoster`、`waitForVideoEvent`、`waitForDecodedFrame`、`drawVideoPosterFrame` 残留引用。

## 问题清单

### P0 阻断

暂无确认的 P0 阻断。

### P1 高风险

1. 启动器入口 exe 可能仍是旧版本  
   `dotnet build` 已通过，但构建不会自动覆盖 `launcher/SynCanvasLauncher.exe`。若入口 exe 没有重新 publish/copy，用户双击时仍可能运行旧逻辑。  
   建议：关闭启动器后重新 publish/copy，再回归“一键停止不永久卡住”和“全部 ready 自动打开浏览器”。

2. 数字人动作视频需要真实大文件回归  
   代码层已移除前端批量抓帧，但仍需用 3-5 个首帧黑色视频实测：上传不卡顿、卡片不出现大黑块、后端 `poster_url` 能正常补齐。  
   建议：用真实 `.mp4/.mov` 多选上传，观察浏览器主线程卡顿和封面生成速度。

3. 数字人队列需要端到端资源回归  
   已实现独立队列接口和前端队列列表，但需要真实 TTS/HeyGem 连续提交 3 条任务验证串行执行、失败恢复和结果切换。  
   建议：服务 ready 后连续提交多条文案，确认同一时间只有一个任务进入 TTS/HeyGem。

### P2 体验/维护问题

1. 后端主入口仍有大文件  
   - `app/legacy.py`：约 3744 行。
   - `tools/service_supervisor_parts/cli.py`：约 1745 行。  
   建议：优先把仍在用的 API 迁到 `app/api/*` 和 `app/services/*`，`legacy.py` 最终只保留兼容壳。

2. 画布状态文件仍过大  
   - `static/js/canvas/state.js`：约 7884 行。
   - `static/js/smart-canvas/state.js`：约 4830 行。  
   建议：按“节点类型/连接规则、媒体上传、Output 预览、生成执行、画布事件”拆分，只搬运边界，不顺手重写行为。

3. 启动器状态机和 UI 逻辑仍耦合  
   `launcher/MainWindow.xaml.cs` 约 1496 行，启动/停止、诊断、控制台、设置保存、浏览器打开都混在同一个文件。  
   建议拆出：启动停止状态机、诊断渲染、控制台会话、设置保存、浏览器打开。

4. 定时器仍需长期登记维护  
   当前 `setInterval(` 仍有 7 处，已补关键生命周期，但建议后续建立固定表格：启动条件、停止条件、隐藏页行为、失败处理。  
   已知用途：AI 生图队列角标、Angle WebSocket ping、登录页时钟、fallback 启动期轮询、普通画布 Output 计时、普通画布远程同步、智能画布运行计时。

5. HeyGem 缓存目录 warning  
   `hf_download` 和 `tf_download` 缺失，但状态检查可通过。  
   建议确认这是正常包结构还是离线模型缓存缺失；如果是可选目录，诊断等级应从 warning 调整为 info/可选。

6. Provider 同步和普通画布音频节点需要浏览器实测  
   代码层已覆盖服务商启用过滤、节点迁移、生成前校验、音频节点上传和 Output 有声预览。  
   建议用真实浏览器验证禁用服务商、旧节点迁移、音频连视频、独立音频输出和有声视频预览。

### P3 清洁度问题

1. Git 全局 ignore 权限告警  
   多次 `git status` 出现：`C:\Users\Administrator/.config/git/ignore` permission denied。  
   不影响当前仓库提交，但会污染命令输出。建议修复用户级 Git 配置权限或删除不可读的全局 ignore 配置。

2. 旧 `/api/queue_status` 仍需确认用途  
   首页 `ONLINE / QUEUE` 角标已移除；`zimage.html` 仍有队列状态角标刷新，后端接口仍被 AI 生图页使用。  
   建议后续确认它是否只服务 AI 生图队列，避免再次变成全局噪音。

## 下一阶段整改顺序

1. 关闭启动器，重新 publish/copy `launcher/SynCanvasLauncher.exe`，回归一键停止和自动打开浏览器。
2. 用真实视频回归数字人动作视频上传、封面、队列生成。
3. 拆 `static/js/canvas/state.js`，先搬出 Output 预览/下载和媒体上传。
4. 拆 `app/legacy.py`，把当前仍使用的 API 按模块迁移到 `app/api/*`。
5. 建立功能测试清单：启动器、服务商同步、普通画布音频、数字人队列、视频封面、统一弹窗。
6. 建立定时器/轮询登记表，后续新增轮询必须写启动和停止条件。

## 当前 Git 状态说明

本轮整理后工作区有源码变更，主要包括：

- `.gitignore`
- `PROJECT_SELF_CHECK.md`
- `tools/check_mojibake.py`
- `tools/launcher_server.py`
- `static/js/theme.js`
- `static/js/canvas/state.js`
- `static/js/digital-human/*`
- `static/js/smart-canvas/state.js`
- `static/*` 多页面弹窗替换
- `app/services/digital_human_service.py`

归档目录 `_archive/` 已被忽略，不会进入 Git。启动器构建产物、exe/pdb、运行环境、用户素材、日志、输出仍保持忽略。
