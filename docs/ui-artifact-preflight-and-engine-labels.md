# 前端：运行产物预估与运行引擎标签

本文件说明两处只读 UI 的取值来源与行为，便于后续排查“界面数字和后端不一致”一类问题。

## 运行产物预估

- 数据来源：`POST /api/projects/validate` 的附加字段 `artifactEstimate`（见
  `docs/artifact-preflight-contract.md`）。前端不新增接口，也不自行计算体积。
- 位置：
  - 设置活动 → “运行产物预估”卡片（求解器与输出设置之后）：预估运行目录 /
    512 MiB 上限、进度条、`risk` 徽标、导出 ZIP 预估与 `archiveRisk`、置信度、
    占用最多的前三项 `contributors`、`suggestions`。
  - 右侧属性检查器底部“参数检查”卡片中的一行摘要与 `warnings`。
- 行为：
  - `risk = "reject"` → 卡片明确提示会在工作进程启动前被拒绝，“运行工艺”按钮
    置灰并给出 `title` 说明；`startRun` 仍会短路提示。结构预览不受影响，因为
    预检按 `simulate` 规模估算。后端 422
    （`detail.code = "artifact-budget-preflight"`）仍然最终生效。
  - `risk = "warning"` → 非阻塞提示，运行按钮保持可用；
    `rejectionDowngraded` 时会说明低置信度估算只警告不拒绝。
  - 提交遇到 422 时，错误 `detail.artifactEstimate` 会回填卡片。
  - 校验失败或工程尚未通过校验时卡片显示等待/不可用文案。

## 主机 GPU 能力 vs 本次运行引擎

- “主机 GPU 能力”（左侧活动栏 `主机 GPU`、状态栏、检查器）来自
  `/api/gpu-devices` / capabilities，只表示本机能否运行 CUDA 通量。
- “本次运行引擎”来自所选运行的 `RunInfo.actualExecution`：取所有步骤
  `actualEngine` 中非 `not-applicable` 的唯一值，显示 `CPU_DISK`、`GPU_DISK`、
  `混合`，没有值时显示 `未执行/无粒子步骤`。选中步骤时同时显示该步的引擎。
- 两处文案都带 tooltip 说明区别，避免把“主机可选 GPU”误读成“当前运行使用了
  GPU”。

## 已知限制

- 预估值是保守估算，不是实际目录测量；校准数据见后端文档。
- 运行中的 `actualExecution` 随轮询更新，刚提交的运行会短暂显示
  `未执行/无粒子步骤`。
