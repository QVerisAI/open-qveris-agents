# TOOLS.md - 环境配置

## 文件路径

- Workspace 根目录：`/root/.openclaw/workspace`
- 自定义 Skill 目录：`/root/.openclaw/workspace/skills/`
- 内置 Skill 目录：`/app/skills/`（不要用这个路径读自定义 skill）

**重要：** 当你需要读取 skill 文件时，使用 `/root/.openclaw/workspace/skills/` 路径，不要用 `/app/skills/`。

例如：
- 正确：`/root/.openclaw/workspace/skills/portfolio-health-check/SKILL.md`
- 错误：`/app/skills/portfolio-health-check/SKILL.md`

## QVeris

- API Token 已通过环境变量 `QVERIS_TOKEN` 配置，不需要手动设置
- 不要尝试检查、读取或修改 QVERIS_TOKEN
