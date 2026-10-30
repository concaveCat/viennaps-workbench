# 粒子形状分布合同（schemaVersion 7 / `particleShape`）

本合同描述安装版本 ViennaPS 4.6.2 + ViennaLS 5.8.5 中
`BoxDistribution`、`SphereDistribution`、`CustomSphereDistribution` 的实际行为，
以及工作台为什么只暴露其中一部分。所有结论都来自**独立子进程**探测
（`scripts/bounded_child.py` + `worker/probe_particle_distributions.py`），
没有接触正式服务、正式数据目录或 `docs/*-evidence/`。

| 项目 | 值 |
| --- | --- |
| 引擎 | viennaps 4.6.2 / viennals 5.8.5 |
| Python | 3.12.14 |
| 工作台 | project schema 7，分支 `task/engine-particles`，基线 `d2d81a0` |
| 探测日期 | 2026-10-30 |
| 方法 | 每个用例一个 `bounded_child.py` 子进程，40–60 s 超时、2–4 GiB RSS 阈值、日志上限 1 MiB；平面/沟槽/叠层小域，`gridDelta=0.05`、`setNumThreads(1)` |

## 1. 结论

| 原生类 | 处理 | 原因 |
| --- | --- | --- |
| `BoxDistribution`（无掩膜） | **暴露**，schema 7 几何步骤 `particleShape` + `shape: box` | 2D/3D 都只改变几何，位移等于半轴的最大范数（L∞），可复现 |
| `SphereDistribution`（无掩膜） | **暴露**，`particleShape` + `shape: sphere` | 2D/3D 位移等于半径，可复现 |
| 带掩膜的 `Box`/`Sphere` | **不暴露** | 所有正位移（沉积）掩膜路径在 2D/3D 均 `SIGSEGV`（returnCode `-11`）；带掩膜的负位移会静默不动 |
| `CustomSphereDistribution` | **不暴露** | 无掩膜即 `std::terminate`（`-6`）：它按表面点 id 索引半径，JSON 无法安全表达 |

因此 A4 的落地是一个**无掩膜的几何偏移步骤**。掩膜不是“暂时没做”，而是安装版本
不可用；合同、校验错误和运行记录都明确写出这一点。

## 2. 它们到底是什么

`viennaps.BoxDistribution` / `SphereDistribution` 不是通量或速度模型，而是
`ProcessModelCPU` 对 `viennals::GeometricAdvect` 的封装：

- `BoxDistribution(halfAxes)` 用 L∞（切比雪夫）距离推进表面；
- `SphereDistribution(radius)` 用欧氏距离推进表面；
- 正参数 = 沉积（沿外法线生长），负参数 = 刻蚀（沿内法线收缩）；
- `ps.Process(domain, model, duration)` 的 `duration` **完全不参与计算**：
  `GeometricProcessStrategy` 直接调用 `GeometricAdvect(surface, dist, mask)`。
  探测中 `duration=0.0`、`0.05`、`1.0`、`5.0` 的最终坐标逐位相同；
- 沉积只推进**顶层/暴露** level set：叠层中只有最上层界面移动，下层材料界面保持原位；
- 刻蚀后 `GeometricProcessStrategy` 会把所有下层 level set 与新的表面对齐
  （`INTERSECT`），因此刻蚀会真实消耗材料；
- 盒分布在 2D 只使用前两个半轴；原生绑定仍要求固定长度 3 的数组，
  工作台把第三个位置填 `0.0`（原生在 2D 不读取它）。

> 审计文档 `engine-capability-matrix.md` 曾记录“无掩膜时探测没有表面变化”。
> 本轮重新探测（平面与沟槽、2D/3D、正/负位移）都能稳定测到位移；旧结论不再成立。
> `docs/engine-capability-matrix.md` 的 A4 行已同步更新。

## 3. 探测命令

探测脚本只依赖 `viennaps`，每个用例单独进程；崩溃不会带走父进程。

```bash
cd /root/wb-engine-particles
mkdir -p var/probe-a4

# 一个用例：3D、球、半径 0.2、无掩膜
printf '%s' '{"dimension":3,"shape":"sphere","magnitude":0.2,"mask":"none"}' \
  > var/probe-a4/sphere-3d.case.json
.venv/bin/python scripts/bounded_child.py --cwd "$PWD" \
  --record var/probe-a4/sphere-3d.guard.json \
  --log-path var/probe-a4/sphere-3d.log \
  --timeout-seconds 60 --max-rss-mib 4096 --max-log-bytes 1048576 -- \
  .venv/bin/python worker/probe_particle_distributions.py \
  var/probe-a4/sphere-3d.case.json var/probe-a4/sphere-3d.out.json
echo "rc=$?"; cat var/probe-a4/sphere-3d.guard.json
```

`case.json` 的字段：`dimension`（2/3）、`shape`（`box`/`sphere`/`customSphere`）、
`magnitude`（带符号）、`halfAxes`、`radii`、`mask`
（`none`/`material`/`raw-material`/`surface`）、`geometry`
（`plane`/`trench`/`stack`）、`duration`、`boundary`（`reflective`/`periodic`）。
`raw-material` 用 `domain.getMaterialLevelSet(Mask)` 得到**本项目自己的**
`viennals` Domain 作为掩膜，不引入第二套几何。

## 4. 探测结果（原始记录）

`guard.returnCode` 就是子进程退出码：`0` 正常，`-11` = SIGSEGV，`-6` = SIGABRT。

| 用例 | 维度 | 结果 |
| --- | --- | --- |
| `BoxDistribution` +0.2，无掩膜，平面 | 2D / 3D | `0`；z：`0 → +0.2`（2D 取 x/y 最大半轴，3D 取最大半轴） |
| `SphereDistribution` +0.2，无掩膜，平面 | 2D / 3D | `0`；z：`0 → +0.2` |
| 负参数 −0.2，无掩膜，平面 | 2D / 3D | `0`；z：`0 → −0.2` |
| 无掩膜，沟槽（`MakeTrench`） | 2D / 3D | `0`；顶面 0.2 → 0.3，沟底 −0.425 → −0.35（球，0.1） |
| 无掩膜，三层叠层 | 2D / 3D | `0`；只有顶层界面 0.5 → 0.65，下面两个界面不动 |
| `duration` 0 / 0.05 / 1 / 5 | 2D / 3D | 结果逐位相同 → 原生忽略持续时间 |
| 半径/半轴 2.0（上界） | 3D | `0`，约 0.5–0.6 s |
| 半轴/半径 = 0 | 2D / 3D | `-11`；日志先出现 `One half-axis ... smaller than the grid Delta!` |
| 半轴 `< gridDelta`（0.01） | 2D / 3D | `0`，但原生日志警告分布可能被破坏 → 工作台在校验阶段拒绝 |
| 混合符号 `[0.1,−0.2,0.3]` | 2D | `0` 但表面被清空（快照无点），属于不可用结果 |
| 混合符号 `[0.1,−0.2,0.3]` | 3D | `-11` |
| 掩膜：`addMaskMaterial(Mask)`，正位移 | 2D / 3D | `-11`（盒、球都一样） |
| 掩膜：`getMaterialLevelSet(Mask)` 原始 `viennals` Domain，正位移 | 2D / 3D | `-11` |
| 掩膜：`applyToSingleMaterial(Si)`，正位移 | 2D / 3D | `-11` |
| 掩膜：负位移（刻蚀） | 2D / 3D | `0`，但表面**完全不动**（静默 no-op） |
| `CustomSphereDistribution([0.1,0.2,0.3])`，无掩膜 | 2D / 3D | `-6`；`terminate called after throwing an instance of 'std::runtime_error'` / `what(): Point ID 3 is out of bounds for CustomSphereDistribution with 3 radii!` |
| `CustomSphereDistribution` + 掩膜 | 2D / 3D | `-6`；`Point ID 12 is out of bounds ...` |
| 周期性边界，平面 | 2D / 3D | `0`、平面位移正确；但 ViennaLS 的 `lsGeometricAdvect.hpp` 明确标注 periodic 仍是 TODO（“need to add support for periodic boundary conditions”），带图形时的正确性没有证据 → 工作台拒绝 |

崩溃日志的关键片段（`var/probe-a4/*.log`，不提交）：

```text
# 掩膜沉积
    Applying geometric model...
# 随后进程被 SIGSEGV 结束（guard.returnCode = -11）

# CustomSphereDistribution
    Applying geometric model...
terminate called after throwing an instance of 'std::runtime_error'
  what():  Point ID 3 is out of bounds for CustomSphereDistribution with 3 radii!
# guard.returnCode = -6
```

## 5. JSON 合同

`particleShape` 是**几何步骤**，不是 `Process` 步骤：没有 `duration`、没有速率、
没有随机种子/通量引擎，也不写 `actualRate`。它只出现在 schema 7 的步骤联合类型里。
不需要新端点和上传资产：项目继续通过既有的 `/api/projects`、`/api/runs`、
`/api/runs/{runId}/replay-project` 与 `/api/runs/replay` 传递；旧项目与该字段缺席时
的请求哈希完全不变。

```json
{
  "id": "step_shape_0001",
  "name": "表面圆化",
  "enabled": true,
  "operation": "deposit",
  "model": "particleShape",
  "shape": "sphere",
  "radius": 0.5
}
```

```json
{
  "id": "step_shape_0002",
  "name": "盒形收缩",
  "enabled": true,
  "operation": "etch",
  "model": "particleShape",
  "shape": "box",
  "halfAxes": [0.5, 0.25, 0.75]
}
```

字段与校验：

| 字段 | 规则 |
| --- | --- |
| `operation` | `deposit`（正位移）或 `etch`（负位移）；`halfAxes`/`radius` 永远是正数 |
| `shape` | `sphere` 或 `box` |
| `radius` | `shape=sphere` 必填，`> 0` 且 `≤ 2 µm`，并且 `≥ domain.gridDelta` |
| `halfAxes` | `shape=box` 必填；2D 恰好 2 个 `[x, h]`，3D 恰好 3 个 `[x, y, z]`；每项 `> 0`、`≤ 2 µm`、`≥ gridDelta` |
| 掩膜 | **不可表达**：`mask`、`maskMaterials` 等额外字段一律 422（`extra=forbid`） |
| 持续时间/速率 | **不可表达**：`duration`、`rateMagnitude` 等额外字段 422 |
| 维度 | 2D/3D 都支持；`halfAxes` 长度不符合维度时在 ProjectV7 校验中 422 |
| 边界 | 只允许 `reflective`；`periodic` 在 `validate_project` 中 422 |
| Python 回调 | 不可表达（没有 callable/module 字段） |

工作台校验还会拒绝 `0` 和小于一个网格间距的值：原生在 0 时崩溃，在
`< gridDelta` 时打印“distribution may break”的警告。混合符号无法表达，
符号统一由 `operation` 决定。

## 6. 工作台执行与诊断

`worker/simulate.py::apply_particle_shape` 构造真实原生对象：

```python
model = ps.BoxDistribution([sx, sy, sz])      # 2D 传 [sx, sy, 0.0]
model = ps.SphereDistribution(sign * radius)
ps.Process(domain, model, 0.0).apply()        # duration 原生不参与，固定记录 0.0
```

运行记录写入 `manifest.actualExecution[].actualParameters`：

- `geometryOperation: "particleShape"`、`nativeClass`、`nativeConstructor`；
- `dimension`、`operation`、`shape`、`radius` / `halfAxes`（请求值）；
- `nativeSignedRadiusUm` / `nativeSignedHalfAxesUm` / `nativeHalfAxesUm`（实际传入值）；
- `expectedOffsetUm`（球半径或盒最大半轴）；
- `surfaceBoundsBeforeUm` / `surfaceBoundsAfterUm`（真实表面网格包围盒）；
- `levelSetPointCountsBefore` / `After`、`materialIdsByLevelSetIndex`；
- `nativeMask: {mode: "none", supported: false, reason: ...}`；
- `nativeProcessDurationSeconds: 0.0`、`nativeDurationIgnored`；
- `scopedSurface`：沉积只推进顶层材料，刻蚀会与下层 level set 求交。

worker 在调用前后各测一次表面包围盒；如果完全没变，会抛错而不是记录一个静默 no-op。
`actualEngine` 固定为 `not-applicable`，`actualSeed` 为 `null`。

## 7. 测试

`tests/test_particle_shape.py`：

1. 2D/3D 球沉积、盒刻蚀与独立原生参考
   （`tests/fixtures/particle_shape_direct_reference.py`）比较坐标（≤ `1e-6`）、
   拓扑、`MaterialIds`、`materialIdsByLevelSetIndex`；
2. 2D 沟槽上的盒沉积与原生参考比较，覆盖多 level set；
3. 连续两个形状步骤 + replay 逐位一致；
4. 掩膜/周期边界/零半径/低于网格间距/错误维度/额外字段全部 422；
5. 既有项目请求哈希固定在基线值 `605a109b...c42`，新增步骤只在启用时改变哈希；
6. 项目保存/重开保持字段；
7. 用 bounded child 复跑并断言危险路径：
   `CustomSphereDistribution` 2D/3D `-6`、掩膜沉积 2D/3D `-11`、零半轴 `-11`，
   同时确认无掩膜探测仍有真实位移。

## 8. 仍未覆盖 / 不声称

- 不暴露掩膜：安装版本没有安全路径；不要用“先加掩膜以后再修”的方式绕过。
- 不暴露 `CustomSphereDistribution`：需要与表面点数一一对应的半径数组，
  不能从项目几何稳定推导。
- 周期性边界没有暴露：原生几何推进的 periodic 支持仍是 TODO。
- 这是形态学几何偏移，不是物理沉积/刻蚀：没有通量、黏附概率、角度分布、
  材料选择比或时间演化；位移是参数值，不是 `rate × duration`。
- 沉积只移动顶层 level set，不会给新区域重新分配材料；
  多层结构的材料归属沿用原生顶层材料语义。
