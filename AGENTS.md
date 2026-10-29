# Agent 约定（ViennaPS Workbench）

本文件适用于在本仓库内工作的自动化 agent。目标：多个 agent 并行时既不覆盖彼此
的改动，也不抢占同一份运行时资源。

## 1. 改动前先看状态

```bash
git status --short
git log --oneline -3
```

- 工作区干净、改动很小（单文件、文档、只读排查）：可以直接在主工作区进行。
- 主工作区有未提交改动，或任务涉及多个文件、需要启动服务：另开 worktree。
- 拿不准时按“开 worktree”处理。

## 2. 开 worktree

```bash
git worktree add -b task/<短名> /root/wb-<短名> main
cd /root/wb-<短名>
```

worktree 只包含 git 跟踪的源码与文档。下面四项不在 git 里，必须软链到主工作区，否则
`scripts/start.sh` 会因缺少前端产物而拒绝启动：

```bash
ln -s /root/viennaps-workbench-cpu-gpu/.venv .venv
ln -s /root/viennaps-workbench-cpu-gpu/frontend/node_modules frontend/node_modules
ln -s /root/viennaps-workbench-cpu-gpu/frontend/dist frontend/dist
ln -s /root/viennaps-workbench-cpu-gpu/runtime-patched runtime-patched
```

`docs/*-evidence/` 同样不在 git 中。依赖这些证据的验收脚本在 worktree 里会缺文件，
需要时从主工作区只读引用，不要复制后修改。

## 3. 运行时隔离

- 每个 worktree 固定一个端口，从 `8801` 起顺序分配，用 `WORKBENCH_PORT` 指定。
- 数据目录保持 worktree 自己的 `var/`，不要指向主工作区。
- 在主工作区跑测试同样要隔离数据目录（见 §4），因为正式服务占着 `var/workbench.lock`。
- 本机只有一张 GPU：同一时刻只允许一个 agent 跑 GPU 仿真，CPU 长任务也尽量串行。
- 启动前确认主工作区没有正在执行的任务。

## 4. 改完要做什么

```bash
# 1) 测试：必须用仓库内的独立数据目录，否则会和正在运行的服务抢 var/workbench.lock
VIENNAPS_WORKBENCH_DATA_DIR=$PWD/var/test-$(date +%s) WORKBENCH_PORT=8767 \
  .venv/bin/python -m unittest discover -s tests -t . -v

# 2) 前端类型检查（改了前端时）
cd frontend && npm run typecheck

# 3) 提交前
git diff --check
```

跑测试的三条硬约束（都是实际踩过的）：

| 约束 | 原因 |
|---|---|
| 数据目录必须**在仓库内** | `scripts/bounded_child.py` 要求 `--record` / `--log-path` 位于工作台目录内；放 `/tmp` 会直接失败并报 “cwd、record 和 log 必须位于正式工作台目录内” |
| **不要**让数据目录指向主工作区 `var/` | 正式服务（`127.0.0.1:8789`）持有 `var/workbench.lock`，测试 `lifespan` 里的 `flock` 会立刻抛 `another ViennaPS Workbench server already holds the data-directory lock`（约 4 ms 就失败） |
| `WORKBENCH_PORT` 不要用 `8789` / `8769` | 分别是当前正式服务与旧正式服务的端口 |

不要用“先停掉正式服务再跑测试”来绕过第二条：那会打断正在看结果的人，
而且停服务后 60 s 内端口仍处于 TIME_WAIT（见 `/root/.pi/agent/AGENTS.md`）。

已知环境缺失（不是代码回归）：`tests/test_stage9.py` 有一条断言依赖
`docs/stage9-evidence/gpu-coverage-audit/.../cpu-nan-debug/worker.guard.json`。
`docs/*-evidence/` 不在 git 中（见 §2、§5），新 checkout / worktree 缺该文件时这条会红。
处理方式是**记录并绕过**，不要改证据文件、也不要删断言来让它变绿；
要真正修复只能重跑生成该证据的实验。

当前基线的预期结果是 **102 项：99 通过 / 3 失败 / 4 跳过**。3 条已知红灯及原因：

| 用例 | 现象 | 根因（已定位） |
|---|---|---|
| `test_project_api_preview_and_bounded_archive` | 期望 413，实际 422 | `backend/main.py:118` 已按设计取消通用 JSON 请求体上限（注释原文 “General JSON request bodies have no fixed byte cap”），只有 GDS 64 MiB / continuation 512 MiB 还有 413；断言停留在旧契约 |
| `test_custom_registry_materials_replay_and_native_output_roundtrip` | `nativeMetadata` 产物字节不等 | 两套 writer 分叉：`worker/simulate.py:87` 紧凑 `separators=(",",":")`，参考实现 `tests/fixtures/stage6_material_output_reference.py:51` 用 `indent=2`，而断言比原始字节 |
| `test_patched_gpu_multiparticle_multi_ion_support_is_evidence_backed` | 缺 `worker.guard.json` | 证据只生成在 `/tmp` 的候选目录，未归档进 `docs/` |

跑完若这 3 条之外还有红，才是真正的回归。前两条要修需要产品决策（是否恢复请求体上限）
或统一 writer，不要在“只改文档”的改动里顺手改掉。

- 提交信息说明“为什么”，中英文均可，与改动文件的文档语言一致。
- 非平凡改动走分支 + PR，不要直接推 `main`。
- 推送前确认 `git status` 里没有误加的运行时文件。

## 5. 不要做的事

- 不要提交 `var/`、`.venv/`、`frontend/node_modules/`、`frontend/dist/`、
  `runtime-patched/`。
- 不要修改或删除 `docs/*-evidence/` 下的证据文件：它们不在 git 中，删除后无法恢复。
- 不要提交 token、密钥、内网地址或主机名；这是一个公开仓库。
- 不要对 `main` 执行 `git push --force`，不要删除其他 agent 的 worktree。

## 6. 收尾

```bash
rm -rf var/test-*                      # 清掉 §4 产生的测试数据目录
git worktree remove /root/wb-<短名>
```

任务放弃或合并后都要清理，避免 `git worktree list` 堆积。
