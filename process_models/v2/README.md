# process_models v2.2 — 一页 quickstart（结构化输出）

v2.2 是工艺模型插件契约：把“工艺身份 / 实现 / 引擎 / 状态表示”拆开，并用一次
`resolve_execution()` 做准入、`ExecutionPlan.run()` 执行一步。v2.2 在 v2.1 之上把
**flow state** 与 **report outputs** 分开：`execute()` 返回 `StepResult`，
`StateDelta` 只描述下一步要消费的状态，计量/产物走 `OutputSet`。v1 已冻结，只作历史
保留；完整设计与边界见 [`docs/process-model-plugins-v2.md`](../../docs/process-model-plugins-v2.md)。

## 四层（外加 resolver）

| 层 | 模块 | 职责 |
| --- | --- | --- |
| Runtime | 无（executor 边界） | 跨步骤 flow state、checkpoint、run 目录、快照、种子——本切片未实现 |
| Engine | `engine.py` | `EngineRequirement` / `EngineLease` / `StaticEngineProvider`，严格租用、不回退 |
| Service | `registry.py` + `resolver.py` | 多版本 spec/variant 目录、准入检查、产出 `ExecutionPlan` |
| State | `state.py` + `adapters.py` | `ProcessState{geometry, materials, layers, fields, voxels}`、`StateDelta`（只有 flow state）、`OutputSet`/`StepResult`、`prepare/commit` |

`resolver.py` 把 request 变成 plan：参数类型、维度、`reads`、材料、`validate()`、
引擎租约都在这里检查。`ProcessState` 是 frozen，不含 engine/meta；engine 作为
`execute(..., engine)` 的显式参数传入。

## Structured outputs（结构化输出）

- `StateDelta` = 下一步消费的 flow state：`geometry` / `layers` / `fields`
  （`CellSetState`）/ `voxels`。没有 `artifacts` / `metadata` / `warnings`。
- `OutputSet` = 带类型的报告输出：`metrology`（`MetrologyRecord`：metric/value/unit/
  region/method/uncertainty）+ `artifacts`（`Artifact`：kind/name/path/schema_version/
  metadata）。默认空。
- `StepResult` = `state` + `outputs` + 少量 `metadata` + `warnings`。
- `metadata` 不是垃圾桶：只放实际速率、solver 配置这类本步 bookkeeping。一个带单位的
  数进 `MetrologyRecord`，一个文件进 `Artifact`，下一步要用的数组进 `Field` /
  `CellSetState`。

```python
StepResult(state=StateDelta(geometry=...), outputs=OutputSet(metrology=[...]), metadata=...)
```

FinFET 需要的 state 类型：`GridSpec` / `Field` / `CellSetState` 承载掺杂、损伤和
6/9 分量应力（`unit`、`association` = cell/point、`dtype`、`shape`、`components`）；
`Layer` / `LayerState` 承载 dummy gate、ILD、spacer 的层身份（`layer_id` 唯一，
`material_id` 可以重复）。当前**没有** field/cell-set producer、没有原生 cell-set
adapter、没有 stress/device simulator、没有 UI schema/API、没有 field 持久化/replay；
`isotropic` 与 `voxel_probe` 迁移后返回空 `OutputSet`。

## 新增一个 process

1. 写 Pydantic 参数模型（`extra="forbid"`）。
2. 写 `ProcessSpec`（`id`、`version`、`label`、`params_model`、`dimensions`、`operations`）。
3. 写变体：`variant_id`、`version`、`capabilities`（需要时带 `output_kinds`）、`engine`、
   `implementation_hash`（`None` = registry 计算），以及 `validate(ctx, params)` 和
   `execute(state, params, ctx, engine) -> StepResult`。
4. 注册：`register_process(spec)` + `VariantRegistration(process_id=spec.id, variant=...)`。
   只传这两个字段，其余从变体派生。
5. 一步执行用 `run_step(...)`；需要先看 plan 时用 `resolve_execution(...)`，再 `plan.run()`。

```python
from process_models.v2 import ProcessSpec, VariantRegistration, run_step


def register_my_process(registry):
    spec = ProcessSpec(
        id="my_process",
        version="1.0.0",
        label="My process",
        params_model=MyParams,
        dimensions=(2, 3),
        operations=("etch",),
    )
    registry.register_process(spec)
    registry.register_variant(VariantRegistration(process_id=spec.id, variant=MyVariant()))


outcome = run_step(
    registry,
    provider,
    process_id="my_process",
    variant_id="native",
    params=MyParams(rate=1.0),
    context=ctx,
    state=state,
)
# outcome.plan.identity() 可直接写进 run 记录
# outcome.outputs / outcome.metadata / outcome.warnings 来自 StepResult
```

完整可运行骨架（含 params/variant）见 v2 文档 §0.2。

## 怎么跑

不启动服务，使用隔离数据目录：

```bash
VIENNAPS_WORKBENCH_DATA_DIR=$PWD/var/test-v2 WORKBENCH_PORT=8767 \
  .venv/bin/python -m unittest \
  tests.test_process_models_v2_core tests.test_process_models_v2_execution \
  tests.test_process_models_v2_isotropic tests.test_process_models_v2_voxel
```

v1 回归测试保持原样，也应在同一轮跑通。v2.2 已验证 **203 项定向测试通过**
（v1 38 + core/execution 84 + isotropic 19 + voxel 14 + worker bridge 8 +
shadow/apply 16 + capability 2 + lifecycle 22），另加默认 isotropic dispatch 12 项
通过。v2.1 的 150 项是历史基线，不再作为 v2.2 的通过证据。

## Worker shadow validation（v2 isotropic）

切换 worker 之前，先用默认关闭的影子校验证明 v2 isotropic 插件复现现有 worker
路径。这是**已落地**的集成切片，生产执行仍然走旧路径：

- 影子开关：`VIENNAPS_WORKBENCH_V2_ISOTROPIC_SHADOW=1`，不设则完全不参与，输出不变。
- 验证 apply 开关：`VIENNAPS_WORKBENCH_V2_ISOTROPIC_APPLY=1`，只在 shadow 匹配后
  把 `ctx.domain` 换成 v2 domain；这是能力验证，不是生产 cutover。
- 范围：`model: isotropic`、`rateMode: uniform`、`isotropicMethod` 解析为
  `levelSet`（不是 geometric）、schema >= 4（实测 5/7）、2D/3D。
- 机制：旧路径 apply 前深拷贝 domain → 老路径 apply → 在副本上跑
  `worker.process_models_v2.run_isotropic_v2` → 比较 `surface_state` digest 与
  material map → 写 `v2-isotropic-shadow` 事件（两侧 digest + plan identity）→
  一致后可选 apply 替换 domain 并写 `v2-isotropic-applied`；不一致直接抛错。
- 不覆盖：`rateMode: material`、geometric `SphereDistribution`、默认生产 cutover。

```bash
VIENNAPS_WORKBENCH_DATA_DIR=$PWD/var/test-v2-shadow \
VIENNAPS_WORKBENCH_V2_ISOTROPIC_SHADOW=1 WORKBENCH_PORT=8767 \
  .venv/bin/python -m unittest -v tests.test_worker_v2_isotropic_shadow
```

bridge 本身的直接测试：`tests.test_worker_v2_isotropic`（不需要开关，也不启动服务）。

v2.2 的验证结果是 bridge 8 项、shadow/apply 16 项通过；`tests.test_v2_isotropic_capability`
2 项通过（2D 三步 + replay、3D 两步）。这些套件已经跟着 `StepResult` 迁移，行为语义
不变；完整的 203 项结果见 `docs/process-model-plugins-v2.md` §9.2。

## 插件生命周期（manifest + lock）

插件不只是“代码里的一个类”，而是**可安装单元**：`plugins/<id>/plugin.toml` 声明
`api`/`id`/`version`/`entrypoint`，`plugins.lock` 固定实际安装的版本与字节哈希，
`load_registry()` 按 lock 发现并加载，任何漂移都直接失败（无回退）。项目 JSON 仍然
只写 `process_id`/`variant_id`/参数，**永远不会**出现模块路径或 callable。

```bash
.venv/bin/python scripts/lock-plugins.py            # 生成/重写 plugins.lock
.venv/bin/python scripts/lock-plugins.py --check    # 校验（0 = 一致，1 = 漂移）
```

- 发现根：`VIENNAPS_WORKBENCH_PLUGIN_DIRS`（`os.pathsep` 分隔），缺省 `<repo>/plugins`；
  lock 路径可用 `VIENNAPS_WORKBENCH_PLUGIN_LOCK` 覆盖。
- 改了 manifest 或实现代码就必须重新生成 lock，**不要手改**；改完要重启 worker/服务
  （没有热加载）。v2.2 改了 `isotropic` 的实现字节（`execute` 返回 `StepResult`），
  lock 已重新生成并通过 `--check`。
- replay 需要的插件版本没装是硬错误，不会用邻近/最新版本顶替。
- 当前只注册 `isotropic`（`1.0.0`，变体 `native`）；`voxel_probe` 是树内 proof 插件，
  没有 manifest，不在 `plugins/` 与 lock 里。

字段、哈希覆盖范围、失败场景与运维/开发清单见
[`docs/plugin-lifecycle.md`](../../docs/plugin-lifecycle.md)。

## 当前限制

- 未切换默认生产执行路径，不改项目 schema；项目暂时不能引用 v2.2 变体。
  仅有的 worker 接触点是上面的默认关闭影子/验证 apply 开关。
- 没有 warm pool、引擎复用池、并发调度；`StaticEngine.release()` 是 no-op。
- 没有外部引擎（`external` 只是枚举值），没有自动引擎/变体选择。
- `CellSetState` / `Field` / `LayerState` / `MetrologyRecord` / `Artifact` / `OutputSet`
  只有类型、校验和 adapter 写入；**没有 producer**，没有从 level-set 到 cell-set 的
  转换，也没有 field/layer 持久化或 replay。
- 没有 stress / device simulator；`Artifact(kind="device")` 只是报告类型。
- 没有输出 schema / API / UI；`OutputSet` 目前只在 Python 契约里。
- `isotropic` 与 `voxel_probe` 迁移后返回空 `OutputSet`：不产出 metrology 或 artifact。
- voxel 适配器只支持 height-field；fin/GDS cut-out 在 prepare 阶段被拒绝。
- 只有 isotropic（native）是已安装插件（`plugins/isotropic/plugin.toml` + lock）；
  voxel_probe（surrogate proof）仍在树内、没有 manifest，只能由代码显式注册。
  快照、VTK、预览、事件、产物落盘仍属未来的 executor。
- 插件生命周期本切片只做 manifest/发现/lock：没有 conformance harness、warm pool、
  API/UI、插件市场、不可信代码沙箱，也没有自动引擎/变体选择。
