# TOOLS.md - 环境配置

## 路径约定

- 使用当前 workspace 内的相对路径读取文件
- 自定义 skill 以当前仓库中的 `workspace/skills/` 为准
- 不要假设固定的宿主机绝对路径或容器内部路径

例如：
- 推荐：`workspace/skills/event-intelligence/SKILL.md`
- 推荐：相对于当前 workspace 根目录定位技能文件
- 不推荐：依赖某一台机器上的绝对部署路径

## QVeris

- QVeris 相关凭证应通过运行环境提供
- 不要在仓库内写死或暴露凭证
