# Infinite Canvas 上游同步记录（2026-07-28）

## 同步基线

- 上游仓库：`https://github.com/hero8152/Infinite-Canvas.git`
- 上游分支：`upstream/main`
- 上游提交：`96c00854a7785edda52a52ebd58e1371d6177f2b`
- 上游版本：`2026.07.28.1`
- SynCanvas 版本：`2026.07.28.4`

两个仓库没有共同 Git 祖先，且 SynCanvas 已经增加启动器、数字人、ComfyUI、Agent/Skill、双画布和模块化后端。因此本次没有执行普通 `git merge`，而是按功能和路由手工迁移。

## 合并策略

1. SynCanvas 已有接口和页面优先，禁止上游覆盖本地业务。
2. `app/upstream_runtime.py` 保存上游最新版 Python 运行时，`app/upstream_bridge.py` 只注册本地尚未拥有的 API。
3. 素材库接口使用上游新版实现，因为其响应同时提供新版 `libraries` 与旧版 `categories`，可兼容现有画布。
4. 上游更新器接口不注册，避免把 SynCanvas 当作原版项目覆盖更新。
5. 画布文件不整页替换，只迁移明确功能，继续保留 Agent/Skill、音频、独立视频输入、4K、数字人和当前 Provider 逻辑。

## 已迁入

- 105 个非冲突上游 API，7 个素材库接口升级为兼容实现。
- 素材管理器、项目工作台、提示词库、工作流导入导出、日志安全清理等后端能力。
- Grok、即梦 CLI、Codex CLI、Gemini CLI 及即梦新模型配置。
- 上游 `2026.07.28.1` 的废弃异步生图端点保护，以及 APIMART Gemini 鉴权、模型分辨率和计费回退修复。
- `#projects` 项目工作台和 `#assets` 素材管理主路由。
- 画布项目字段：`project`、`board_x`、`board_y`、`owner`、`color`、`pinned`。
- 无限画布循环节点视频输入、视频批次、图片/视频联合步长和并发上下文修复。
- 双画布提示词模板入口、工作流 JSON/ZIP 导入导出。
- 无限画布素材库入口；智能画布工作流素材不再显示占位文案，可双击导入。
- 上游独立资源、i18n、RunningHub 数据、Chrome 本地素材扩展、Photoshop 连接器和 CLI 工具。

## 保留的 SynCanvas 定制

- WPF 启动器、主应用自适应端口和服务监督。
- 数字人按需组件、TTS/HeyGem、声音库离线显示和 GPU 释放策略。
- ComfyUI 外部实例、工作流配置和试运行。
- 17 个 Agent、11 个 Skill、异步运行器及双画布节点。
- 无限画布音频输入、独立视频输入、4K 分辨率和现有并发任务机制。
- 统一设置中心与 `#settings/api`、`#settings/agents`、`#settings/comfyui` 路由。

## 明确未迁入

- 上游 `workflows/*.json` 示例文件。用户已要求删除，且旧文件存在空壳和误导风险。
- 上游自动更新接口。当前仓库结构不同，直接覆盖不安全。
- 上游 `API/.env`、用户素材和用户画布数据。
- 上游整份 `canvas.js`、`smart-canvas.js`。两者会覆盖 SynCanvas 的 Agent/Skill 和媒体节点定制。

## 验证

- `python -m pytest tests -q`：35 passed，17 subtests passed。
- 55 个 `static/**/*.js` 全部通过 `node --check`。
- 27 段静态 HTML 内联脚本通过 `vm.Script` / `vm.SourceTextModule` 解析。
- `dotnet build launcher/SynCanvasLauncher.csproj -c Release --no-restore`：0 警告，0 错误。
- 真实浏览器验证 `#projects`、`#assets`、三个设置子路由以及前进/刷新后的页面加载。
- 无限画布验证图片、视频、音频、Agent、Skill、4K、模板、工作流、素材入口，以及 `videoInput -> loop -> video` 持久化连线。
- 智能画布验证 Agent/Skill 创建、提示词模板、工作流导入导出和素材库工作流分类。
- 1440x900 与 390x844 视口下，智能画布顶部入口无重叠，移动端工作流面板保持在视口内；设置中心无横向溢出。
- `/api/upstream-sync` 可查看当前同步提交、安装路由和替换路由

以后同步上游时，先更新 `upstream` 远端并比较新提交，再按本文件的边界继续手工迁移；不要对当前工作区执行无共同祖先的普通合并。
