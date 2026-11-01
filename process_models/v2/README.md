# process_models v2.1 — 一页 quickstart

v2.1 是工艺模型插件契约：把“工艺身份 / 实现 / 引擎 / 状态表示”拆开，并用一次
`resolve_execution()` 做准入、`ExecutionPlan.run()` 执行一步。v1 已冻结，只作历史保留；
完整设计与边界见 [`docs/process-model-plugins-v2.md`](../../docs/process-model-plugins-v2.md)。

## 四层（外加 resolver）

| 层 | 模块 | 职责 |
| --- | --- | --- |
| Runtime | 无（executor 边界） | 跨步骤 flow state、checkpoint、run 目录、快照、种子——本切片未实现 |
| Engine | `engine.py` | `EngineRequirement` / `EngineLease` / `StaticEngineProvider`，严格租用、不回退 |
| Service | `registry.py` + `resolver.py` | 多版本 spec/variant 目录、准入检查、产出 `ExecutionPlan` |
| State | `state.py` + `adapters.py` | `ProcessState{geometry, fields, voxels, materials}`、`StateDelta`、`prepare/commit` |

`resolver.py` 把 request 变成 plan：参数类型、维度、`reads`、材料、`validate()`、
引擎租约都在这里检查。`ProcessState` 是 frozen，不含 engine/meta；engine 作为
`execute(..., engine)` 的显式参数传入。

## 新增一个 process

1. 写 Pydantic 参数模型（`extra="forbid"`）。
2. 写 `ProcessSpec`（`id`、`version`、`label`、`params_model`、`dimensions`、`operations`）。
3. 写变体：`variant_id`、`version`、`capabilities`、`engine`、`implementation_hash`
   （`None` = registry 计算），以及 `validate(ctx, params)` 和
   `execute(state, params, ctx, engine) -> StateDelta`。
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

v1 回归测试保持原样，也应在同一轮跑通。本轮已验证 **124 项定向测试通过**
（v1 38 + v2.1 core/execution 56 + isotropic 17 + voxel 13）；不含 backend/worker
集成测试。

## 当前限制

- 未接入 backend/worker，不改项目 schema；项目暂时不能引用 v2.1 变体。
- 没有 warm pool、引擎复用池、并发调度；`StaticEngine.release()` 是 no-op。
- 没有外部引擎（`external` 只是枚举值），没有自动引擎/变体选择。
- `FieldState` 只是占位；字段/cell-set 的数值路径未实现，adapter 会显式拒绝。
- voxel 适配器只支持 height-field；fin/GDS cut-out 在 prepare 阶段被拒绝。
- 只有 isotropic（native）与 voxel_probe（surrogate proof）两个插件；
  快照、VTK、预览、事件、产物落盘仍属未来的 executor。
