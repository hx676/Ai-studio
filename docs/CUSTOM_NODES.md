# SynCanvas 扩展节点开发规范

SynCanvas 从项目根目录的 `custom_nodes/` 扫描扩展。目录体验与 ComfyUI 类似：每个一级子目录是一个扩展包，Python 代码在主进程中导入，共享当前 Python 环境。

## 创建扩展

```powershell
python tools/create_node_extension.py acme.text-tools --name "ACME Text Tools"
python tools/create_node_extension.py --validate
```

扩展目录至少包含：

```text
custom_nodes/acme_text_tools/
  node.json
  __init__.py
  requirements.txt
  web/
    index.js
    styles.css
```

`node.json` 是包和节点元数据的唯一来源，格式由 `custom_nodes/node-extension.schema.json` 定义。扩展 ID 使用小写字母、数字、点、下划线或短横线；最终节点类型固定为 `<扩展ID>/<节点ID>`。

面向用户的名称和说明应提供中文本地化字段。包使用 `name_zh`、`description_zh`，节点使用 `display_name_zh`、`description_zh`，端口使用 `name_zh`。基础英文名称字段仍为必填；中文界面优先显示 `_zh` 字段，缺失时自动回退到基础字段。

## Python 契约

`__init__.py` 必须导出 `NODE_CLASS_MAPPINGS`，可选导出 `NODE_DISPLAY_NAME_MAPPINGS` 与 `WEB_DIRECTORY`：

```python
class MyNode:
    STATE_MIGRATIONS = {
        1: lambda state: {**state, "newField": state.get("oldField", "")}
    }

    async def execute(self, context, state, inputs):
        context.progress(0.5, "Working")
        return {
            "outputs": {
                "text": {"kind": "text", "value": "done"}
            }
        }

NODE_CLASS_MAPPINGS = {"my-node": MyNode}
NODE_DISPLAY_NAME_MAPPINGS = {"my-node": "My Node"}
WEB_DIRECTORY = "./web"
```

- 导入阶段不得发起网络请求、加载大型模型、启动线程或写入用户数据。
- `execute` 可以是同步或异步函数，输入、状态和返回值必须可 JSON 序列化。
- 标准值类型为 `text`、`json`、`image`、`audio`、`video`；自定义类型必须加扩展 ID 前缀。
- 图片、音频和视频保存可信本地 URL 或 HTTP URL，不要把 Data URL、二进制内容或密钥写进节点状态。
- 长任务必须响应任务取消，并通过 `context.progress()` 报告进度。
- 不得从 `app.legacy` 或画布实现文件导入私有函数。内置迁移包可临时使用兼容服务，第三方节点只能依赖公开 API。

## 前端契约

`web/index.js` 是 ES Module，导出 `register(api)`：

```javascript
export function register(api) {
    api.registerNode('my-node', {
        render({node, escapeHtml}) {
            return `<button data-extension-run>Run</button>`;
        },
        bind({root, node, update, run}) {
            // Return a cleanup function when listeners are registered manually.
        }
    });
}
```

- 扩展只操作传入的节点根元素，不查询或修改画布全局 DOM。
- 使用 `data-extension-state="字段名"` 可让核心自动更新 `node.data`。
- 使用 `data-extension-run` 调用统一运行 API。
- 手动注册的监听器、定时器和观察器必须由 `bind` 返回的清理函数释放。
- 节点标题、端口、绿色运行框、错误状态、复制粘贴和序列化由核心统一处理。

## 兼容与版本

- 端口 ID 发布后不得改变；需要替换时新增端口并保留旧端口迁移。
- 状态结构变化必须提高节点 `version`，并在节点类的 `STATE_MIGRATIONS` 中提供逐版本迁移。键 `1` 表示从 v1 迁移到 v2；运行器会依次执行每一步，缺少任一步时拒绝运行。
- 内置旧节点可通过 `legacy_types.classic` 和 `legacy_types.smart` 声明旧类型别名。
- 扩展缺失或禁用时，画布保留原始节点和连接并显示缺失状态；重新启用后自动恢复。

## 安装和重载

设置页的“扩展节点”可扫描、启停和安装 `requirements.txt`。依赖安装到项目共享 Python 环境，因此必须固定版本并在安装前检查来源。Python 扩展无法可靠地在进程内卸载，启停、升级或安装依赖后通过“一键应用”重启后端。

扩展属于本地可信代码，不是安全沙箱。不要启用来源不明的扩展。

## 最低测试要求

- Manifest 校验和重复 ID 检查。
- Python 节点成功、失败和取消路径。
- 普通画布与智能画布的创建、连线、保存、复制粘贴和重新加载。
- 缺失扩展占位与重新启用恢复。
- 亮色、暗色下节点内容无溢出，浏览器控制台无新增错误。
