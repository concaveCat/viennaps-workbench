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
- 本机只有一张 GPU：同一时刻只允许一个 agent 跑 GPU 仿真，CPU 长任务也尽量串行。
- 启动前确认主工作区没有正在执行的任务。

## 4. 改完要做什么

```bash
.venv/bin/python -m unittest discover -s tests -v   # 至少跑受影响的模块
cd frontend && npm run typecheck                    # 改了前端时
git diff --check
```

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
git worktree remove /root/wb-<短名>
```

任务放弃或合并后都要清理，避免 `git worktree list` 堆积。
