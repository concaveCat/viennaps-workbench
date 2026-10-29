# ViennaPS Workbench 单步耗时分解与优化记录（2026-10-29）

对象运行：`var/runs/d535f09583e1665b080fc68c`（3D fin，5×5 µm，gridDelta 0.03 µm，
单步 SiO₂ singleParticle 沉积，0.2 µm/s × 0.2 s，raysPerPoint 256，GPU_DISK/RTX 3060 Laptop）。
旧记录：worker 总耗时 14.570 s、`Process.apply()` 8.7048 s、manifest 15.116 s。

> 说明：日志里的 `Process time: … / 0.2000 s` 是模拟工艺时间，不是墙钟耗时。

## 1. 方法

两层测量，均不改动 ViennaPS 引擎本身：

1. **ViennaPS 原生计时**（`ps.Logger.setLogLevel(TIMING)`）：`Process <name>` /
   `Surface advection total time` / `Flux engine total time` 三行由
   `psFluxProcessStrategy.hpp::logProcessingTimes()` 输出。已确认该日志级别**不会**
   触发 `INTERMEDIATE` 的每步 VTP 导出（中间目录为空），因此不扭曲测量。
   注意 `fluxEngine_->resetTimer()` 在 `setupProcess()` 之后调用，所以
   **context 创建、kernel/JIT、ToDiskMesh、translation field 不计入 "Flux engine total time"**。
2. **独立分段探针** `worker/profile_segments.py`：用同一份 `input.json` 重放，分别计时
   context 创建、几何构建、快照写出、单次 `calculateFlux()`、完整 `apply()`，
   并用 `raysPerPoint` 扫描把"加速结构构建 + 数据搬运"与"射线追踪"分开
   （`T(rays) = fixed + rays × slope`）。

复现命令（每个模式一个独立进程以保证冷启动）：

```bash
python worker/profile_segments.py var/runs/<id>/input.json --mode context --repeat 2
python worker/profile_segments.py var/runs/<id>/input.json --mode flux --rays 256
python worker/profile_segments.py var/runs/<id>/input.json --mode apply --timing-log
python worker/profile_segments.py var/runs/<id>/input.json --mode apply --threads 8 --timing-log
python worker/profile_segments.py var/runs/<id>/input.json --mode snapshot
python worker/profile_segments.py var/runs/<id>/input.json --mode reuse     # 连续两个 GPU 步骤，不销毁 context
```

原始数据：`docs/perf-profile-20261029-evidence/*.json`。

## 2. `apply()` 内部拆分（6 个时间步，单线程）

同一输入、GPU 已预热（P8 210 MHz 起步也会在数秒内升到 1350 MHz）：

| 组成 | 耗时 | 占比 | 来源 |
|---|---:|---:|---|
| 射线追踪（256 rays/point × 6 步） | **≈3.57 s** | ~53% | 由 rays 扫描斜率外推 |
| 每步固定部分：disk mesh 上传/float 转换、OptiX 加速结构重建、结果下载+平滑 | **≈1.31 s**（6 步，0.218 s/步） | ~20% | rays→0 截距 |
| `setupProcess()`：context 创建、OptiX/模块加载与 JIT、ToDiskMesh、translation field | ≈0.70–0.85 s | ~11% | `processApply − native Process` |
| level-set 演化（Surface advection，6 步） | **≈0.28 s** | ~4% | `Surface advection total time` |
| 其余每步 CPU 工作（再次 ToDiskMesh、速度场、回调等） | ≈0.86 s | ~13% | `native Process − flux − advection` |

**结论：GPU 射线追踪 + 每步加速结构重建 ≈ 73%，level-set 演化只占 4%。**
优化方向必须是"少打射线"或"少重建结构"，而不是优化 C++ 的 level-set 演化。

### raysPerPoint 扫描（`Flux engine total time`，6 次通量计算）

| raysPerPoint | Flux engine 总计 | apply() 总计 | 批次 |
|---:|---:|---:|---|
| 8 | 1.418 s | 3.59 s | 同一批次（含 `--timing-log`） |
| 64 | 2.258 s | 4.23 s | 同一批次 |
| 256 | 4.872 s | 6.70 s | 同一批次（另一次 6.82 s） |
| 32 | — | 1.91 s | 早期批次，跨批次漂移，不参与拟合 |
| 1 | — | 2.13 s | 早期批次，跨批次漂移，不参与拟合 |

> 跨批次绝对值会漂移（同一 raysPerPoint 的 apply 在 5.11–7.36 s 之间变化，见 §4），
> 所以线性拟合只用同一批次内的 8 / 64 / 256 三个点；32 / 1 两行仅作参考。
> 这也说明**用单次 apply 的墙钟做 A/B 对比是不可靠的**，必须同批次重复或看
> `Flux engine total time` 这类原生分段计时。

线性拟合：`slope ≈ 0.0139 s / (ray per point)`（即 256 rays 约 3.57 s），
`intercept ≈ 1.31 s`（6 步的固定开销）。64 rays 实测 2.258 s 与拟合 2.198 s 吻合，
说明在 8–256 区间内射线成本基本线性 —— **raysPerPoint 是唯一有效的一阶旋钮**。

### 单次通量计算（初始几何，45,373 点）

| 配置 | `calculateFlux()` |
|---|---:|
| rays = 1 | 0.533 s |
| rays = 256 | 1.356 s |

即初始几何上 256 rays 的射线成本约 0.82 s，其余 0.53 s 是 context + JIT + 加速结构 +
结果搬运。表面点从 45.7k 增长到 93.1k，所以 6 步的总射线量约 70 M 条。

## 3. context / OptiX 初始化

| 场景 | 耗时 |
|---|---:|
| `ps.gpu.Context.createContext()` 首次（磁盘 JIT/模块缓存冷） | 0.939 s |
| 同进程第二次（模块已缓存） | 0.256 s |

这就是旧记录里"每次新 worker 都要重新付一次"的固定成本，也是 §8 中 context 复用
能省下的量级。它明显**不能**解释旧记录的 8.7048 s。

## 4. 为什么旧记录是 8.70 s，现在是 5.11–6.70 s

同一 `input.json`、同一引擎、同一 GPU 的重复测量：

| 次数 | `Process.apply()` |
|---|---:|
| 1（GPU 刚从 P8 210 MHz 起步） | 6.64 s |
| 2 | 6.70 s |
| 3（预热后） | 5.11 s |

- 空转 60 s 让 GPU 回到 P8/210 MHz 后再跑，仍是 6.64 s → **不是 GPU 降频导致的**。
- 更可能的残余差异是**首次使用的 JIT/模块缓存**（context 冷/热差 0.68 s 已实测）
  与当时机器负载；现有记录无法再细分，故不把 8.70 s 与 5.11 s 的差值归给任何单一原因。
- 结论：**5–6.7 s 是该配置的当前真实水平**，旧记录的 8.70 s 不是稳定值。

## 5. CPU 线程数 1 / 4 / 8

| cpuThreads | apply() | Flux engine | advection |
|---:|---:|---:|---:|
| 1 | 6.70 s | 4.87 s | 0.28 s |
| 4 | 6.59 s | 4.91 s | 0.17 s |
| 8 | 6.59 s | 4.99 s | 0.15 s |

**加线程没有意义**：GPU 引擎的 `#pragma omp parallel for` 只覆盖点/法向量的 float 转换
（每步毫秒级），advection 从 0.28 s 降到 0.15 s 也是噪声量级。
`cpuThreads` 只对 CPU_DISK / CPU_TRIANGLE 引擎和多粒子 CPU 路径有意义。

## 6. 快照与输出（`save_snapshot`）

初始几何（45,717 点 / 90,760 三角形）：

| 环节 | 耗时 |
|---|---:|
| `getSurfaceMesh()` 提取 | 0.075 s |
| `mesh_report` 的 Python 层列表转换 | 0.195 s |
| `json.dumps` | 0.068 s |
| 写盘 | 0.080 s |
| `saveSurfaceMesh()`（**再次完整提取** + 序列化） | 0.377 s |
| 合计 | **≈0.79 s** |

整条 worker 链路（本机现测，`VIENNAPS_WORKBENCH_PROFILE=1`，VTK 开启以对齐旧记录）：

| 阶段 | 耗时 |
|---|---:|
| 进程启动 + import + CUDA 预检（残余） | ≈1.1 s |
| `initialDomainAndGeometry` | 0.066 s |
| `initialSnapshot`（JSON+VTK） | 0.841 s |
| `initialNativeState` | 0.006 s |
| **`Process.apply()`** | **5.111 s** |
| `stepSnapshot`（93k 点，JSON 7.4 MB + VTK 10.8 MB） | 1.987 s |
| `stepNativeState` | 0.010 s |
| **worker 墙钟总计** | **9.19 s** |

关闭 VTK 时：`initialSnapshot` 0.345 s、`stepSnapshot` 0.896 s、worker 墙钟 7.84 s。
即 **snapshot/输出已经是第二大开销**（VTK 开启时占 31%），其中大头是 ASCII 序列化，
不是几何计算。

### 端到端（走正式服务、正式 supervisor/guard 链路）

用记录的 `normalizedProject` 通过 `POST http://127.0.0.1:8789/api/runs` 重跑
（runId `d860a3469cec324f59cfb0c7`）：

| 指标 | 旧记录 | 现在 |
|---|---:|---:|
| manifest `elapsedSeconds` | 15.116 s | **8.604 s** |
| `process.apply()` | 8.7048 s | 5.110 s |
| 初始快照点数 / 三角形 | 45,717 / 90,760 | 45,717 / 90,760 |
| 步骤快照点数 / 三角形 | 93,115 / 185,187 | 93,115 / 185,187 |
| 步骤快照几何（points/triangles/cellData/materials） | — | **与旧记录逐项相等** |

worker 墙钟 7.84 s、manifest 8.60 s，差额约 0.75 s 是 supervisor + guard 的启动开销。
同一输入下 worker 内部 `apply` 稳定在 5.11 s（服务链路里也是 5.11 s），
说明 §4 的 6.7 s 与 5.1 s 差异属于进程/GPU 状态波动而不是代码路径差异。

> **口径说明（避免误读）**：15.116 → 8.604 s 不是单一改动的功劳。旧记录同时包含
> (a) 紧凑 JSON 之前的 17.3 MB 步骤 JSON、(b) 默认关闭表面 VTK 之前的 10.8 MB VTK、
> (c) 本次修掉的重复快照写入与重复表面提取。若把 VTK 重新打开（§6 的 profile-vtk2
> 口径），worker 墙钟是 9.19 s。所以应当记住两个数：
> **当前默认设置 8.6 s / 开 VTK 9.2 s**，而不是把这 6.5 s 全记在某一次提交上。

## 7. 已实施的改动（`worker/simulate.py`）

1. **去掉初始快照的"写 → 整份读回 → 加一个键 → 再写"**
   `save_snapshot(..., extra=…)` / `extra_outputs=…`：附加字段在唯一一次
   `atomic_json` 之前合并进 payload。同样修掉了 `sliceReadback` 与
   `native_specialized_outputs` 的读改写循环。
   实测省下：初始快照 0.144 s，步骤快照 0.301 s（紧凑 JSON 后的大小）。
2. **VTK 复用已提取的表面网格**
   `ps.ls.VTKWriter(mesh, path).apply()` 替代 `domain.saveSurfaceMesh(...)`，
   省掉第二次 `ToSurfaceMesh`。**字节完全一致**：3D 与 2D 都逐字节相同，
   且与旧记录 `initial.vtk` 的 SHA-256 `a4e6766f…d69aab` 完全相同。
   实测省下 0.065–0.075 s/次（提取本身），ASCII 序列化仍需照付。
3. **可选的 stage 计时**
   `VIENNAPS_WORKBENCH_PROFILE=1` 时输出 `segment-timing` 事件（上表数据来源），
   默认关闭、零开销。
4. **GPU context 复用开关**
   `VIENNAPS_WORKBENCH_GPU_CONTEXT_REUSE=1` 时跳过步骤间的强制销毁，
   并记录 `gpu-context-reuse` 事件；默认仍保持原来的 reset 行为（见 §8）。

已验证：同一输入的 worker 输出与旧记录在几何上完全一致（points / triangles /
cellData / materials / surfaceBounds / domainReadback 全部相等，键集合相同）；
`unittest discover` 102 项中 99 项通过，剩余 3 项与本次改动无关（HTTP 413/422 顺序、
`nativeMetadata` 参考文件的 pretty-vs-compact 格式、stage9 缺证据文件）。

## 8. GPU context 复用：旧结论需要修正的情况

`worker/simulate.py` 原来在"下一步还是 GPU 步骤"时无条件
`reset_native_gpu_context()`，注释说 ViennaRay 更新 Domain 后第二次 disk geometry
构建会失败。实测（`--mode reuse`，同一进程、Domain 已被第一步改变、**不销毁 context**）：

| 步骤 | 耗时 | 结果 |
|---|---:|---|
| 第 1 步 apply（初始 fin） | 5.35 s | 成功 |
| 第 2 步 apply（新 Process，已沉积表面） | 5.83 s | **成功，未报错，level set 数 2→3** |

即：**当前的引擎构建下，"第二次 disk geometry 构建失败"没有复现**。
限制说明：
- 只覆盖了本配置（3D fin + SiO₂ singleParticle + GPU_DISK）。
- 该 reset 只在"同一次运行里后面还有 GPU 步骤"时触发，单步运行从来不受影响，
  所以它不是当前 15 s / 9 s 耗时的原因；它能省的只是后续每个 GPU 步骤
  0.3–0.9 s（§3 的 context 创建 + JIT）。
- 因此默认行为保持不变，复用需显式开启；要改成默认，应先把完整引擎矩阵
  （多材料/多 level set/TRIANGLE 引擎）都跑一遍 `--mode reuse` 再决定。

## 9. 改进优先级（按收益/风险排序）

| 优先级 | 措施 | 预期收益 | 风险 |
|---|---|---|---|
| P0 | 降低 `raysPerPoint`（256→64） | apply 4.87→2.26 s（flux 部分），整体约 −2.6 s | 统计噪声变大，需用同一 seed 对比厚度/粗糙度收敛 |
| P0 | 缩小计算域或增大 gridDelta（5×5 µm @ 0.03 µm → 45–93k 表面点） | 线性减少点数与射线量 | 物理保真度下降，需用户确认 |
| P1 | 快照输出改二进制 VTK / 流式 JSON（当前 ASCII 序列化占 1.0–2.0 s/步） | 每步省 0.5–1.5 s | 前端/回放契约要同步改（`initial.vtk` SHA 会变） |
| P1 | 相同初始 Domain/快照缓存（按 geometry+domain+material 目录+引擎指纹做 key，命中则拷贝 initial.json / state-initial.vpsd） | 每运行省 0.35–0.85 s | 缓存失效判定必须严格；不建议缓存 step 结果 |
| P2 | GPU context 复用（§8）→ 常驻 GPU worker | 每个后续 GPU 步骤 0.3–0.9 s；常驻还能省 1.1 s 进程启动+import | 需要引擎矩阵验证，且要处理 context 泄漏 |
| P2 | `Process` 内复用 disk mesh（避免每步 ToDiskMesh 重建，0.218 s/步中的一部分） | 0.2–0.5 s | 需改 ViennaPS/ViennaRay 源码 |
| — | 提高 `cpuThreads` | **无收益**（§5） | — |

## 10. 关于"商业软件 0.5 秒"的对照

在本机上，**同模型、同精度**的 singleParticle + 256 rays 单步不可能做到 0.5 s：

- level-set 演化只有 0.28 s，已经接近 0.5 s 的量级；
- 剩下的 4.9 s 是射线追踪 + 每步加速结构重建 + GPU 初始化，属于该模型的定义成本；
- 即使把 raysPerPoint 降到 8（每点每步 8 条射线），apply 仍是 3.6 s，且统计噪声显著上升。

Sentaurus Process Explorer / SEMulator3D 的"0.5 s/步"通常来自**解析/半解析的
isotropic 或 level-set 速率模型**（无随机射线输运、无遮挡/黏附采样），
问题维度与网格也往往更粗。Workbench 已经支持 `isotropic` 模型，
它与 `singleParticle` 的物理含义不同（没有输运/遮挡/黏附概率），
**不能当作等价替换**，但可以作为"快速工艺预览"模式单独标注使用。

若目标是"预览级 0.5 s"：建议做 isotropic 预览路径，并注意本机实测的标定值——
同一 3D fin、同域同网格、同 0.2 s 工艺时长，**完全不用射线**的 isotropic 单步
`apply()` 仍要 **1.207 s**（88,048 表面点，无 GPU context）。也就是说：
射线成本能省掉（5.11 → 1.21 s），但"0.5 s"还需要同时放宽网格（0.03 µm → 更粗）
或缩小计算域。建议在 UI 上明确区分"预览（isotropic，秒级）"与
"精确（singleParticle/rays，数秒）"两种模式，而不是承诺单步 0.5 s。
