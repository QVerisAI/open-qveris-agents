# TOOLS.md - 环境配置

## 文件路径

- Workspace 根目录：`/root/.openclaw/workspace`
- 自定义 Skill 目录：`/root/.openclaw/workspace/skills/`
- 内置 Skill 目录：`/app/skills/`（不要用这个路径读自定义 skill）

**重要：** 当你需要读取 skill 文件时，使用 `/root/.openclaw/workspace/skills/` 路径，不要用 `/app/skills/`。

例如：
- 正确：`/root/.openclaw/workspace/skills/event-intelligence/SKILL.md`
- 错误：`/app/skills/event-intelligence/SKILL.md`

## QVeris

- API Token 已通过环境变量 `QVERIS_TOKEN` 配置，不需要手动设置
- 不要尝试检查、读取或修改 `QVERIS_TOKEN`
- 事件数据通过 Qveris 平台调用 deepseekdata 语义事件检索工具，你不需要持有独立的 deepseekdata API Key

## Python 运行

- 容器内默认 `python3` 可用，`requests` 库不是必须的（skill 的 `qveris_client.py` 只依赖 stdlib）
- 如果调 skill 里的脚本遇到 import 错误，先 `cd` 到 skill 目录再执行（skill 用相对路径 import）
