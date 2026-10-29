# 工作台文档入口

当前正式发布与启动说明见项目根目录 `README.md`。阶段 1–9 的实现范围、安装版 ViennaPS 原生调用、参考对照、自动化及浏览器验收见对应的 `ACCEPTANCE_STAGE*.md`。

- `ACCEPTANCE_STAGE9.md`：GPU_DISK 精确能力矩阵、分辨率扩展和整体回归。
- `stage9-evidence/gpu-capability-matrix.json`：逐模型签名与产品/direct-reference/guard 证据索引。
- `cpu-thread-evidence/`：本机 CPU 动态上限、schema 7 默认多粒子 GPU_DISK 浏览器运行/重放及发布页 smoke 证据。
- `schema-project-union.json`、`schema-project-v*.json`、`schema-replay-v*.json`：项目和重放格式；旧格式兼容规则在 API contract 与阶段报告中。
- `implementation-status.md`：早期基线历史快照，不代表当前服务端口或当前验收状态。
