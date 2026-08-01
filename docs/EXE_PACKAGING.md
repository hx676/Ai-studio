# SynCanvas Windows EXE 封装方案

## 已确定的发布结构

正式版不把主程序、数字人和 GPL 节点引擎压成一个巨型 EXE，而是采用：

1. `SynCanvas-Setup-<version>-win-x64.exe`：只安装 SynCanvas 核心、内置 Python 和自包含 WPF 启动器。
2. 数字人 TTS、HeyGem：独立可选组件包，由程序内组件管理器安装。
3. Comfy 节点引擎：独立 GPL-3.0 组件包，保留许可证、固定源码版本、源码地址和 SHA-256。
4. 模型、画布、素材、API 密钥、运行记录和扩展：永远不进入安装包。

安装目录使用 `%LOCALAPPDATA%\Programs\SynCanvas`。当前程序把用户数据写在应用根目录，因此不能默认安装到只读的 `Program Files`。升级安装会先停止旧后端，只覆盖程序文件；`API/`、`assets/`、`components/`、`data/`、`output/`、`packages/components/` 和 `workflows/` 在卸载时也默认保留，避免误删用户内容。

## 封装顺序

### 1. 冻结版本和代码

- 更新 `VERSION`，格式为 `YYYY.MM.DD.N`。
- 完整测试、Python 编译、全部 JS 语法、i18n、乱码检查和 WPF 构建必须通过。
- 确认 `API/.env`、用户数据、模型、日志和本机组件没有进入 Git 或发布暂存目录。

### 2. 构建便携核心包

```powershell
python tools/release_preflight.py --root .
powershell -ExecutionPolicy Bypass -File tools/build_modular_release.ps1 -SkipComponents
```

发布脚本使用明确白名单，生成自包含 `win-x64` 单文件启动器，并从空数据目录启动暂存程序做冒烟测试。

### 3. 构建安装 EXE

安装 Inno Setup 6 后运行：

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows_installer.ps1
```

脚本会重新构建并预检核心暂存目录，再生成安装 EXE 和对应 `.sha256`。不要用 `-SkipVerify` 制作对外版本。

### 4. 签名与分发

- 使用受信任的代码签名证书签名安装 EXE；时间戳必须使用可信 TSA。
- 签名后重新计算 SHA-256，并在下载页同时发布版本、大小和哈希。
- 在一台没有 Python、Node.js、.NET SDK 和历史 SynCanvas 数据的 Windows 10/11 x64 机器上安装验收。

## 发布阻断条件

- 测试或干净目录冒烟失败。
- 安装包包含 `API/.env`、`data/` 内容、模型、素材、日志或本机扩展。
- 启动器不是自包含 x64 单文件，或 EXE 版本与 `VERSION` 不一致。
- 可下载组件缺少有效 SHA-256。
- 节点引擎缺少 GPL-3.0 许可证、精确源码版本或源码地址。
- 升级安装会删除用户目录，或安装到无写权限目录。
- 对外发布包未完成真实浏览器和全新 Windows 环境验收。

## 当前仍需发布方提供

- 正式下载域名或对象存储地址。
- 数字人两个组件和节点引擎组件的最终 SHA-256、大小与下载 URL。
- Windows 代码签名证书；没有证书也能生成 EXE，但首次下载会更容易触发 SmartScreen 警告。
