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

v1 回归测试保持原样，也应在同一轮跑通。本轮已验证 **150 项定向测试通过**
（v1 38 + v2.1 core/execution 56 + isotropic 17 + voxel 13 + worker bridge 8 +
worker shadow/apply 16 + capability 2）；另有 12 项现有 isotropic dispatch
API/worker 套件在影子开关打开/关闭下都通过。

## Worker shadow validation（v2 isotropic）

切换 worker 之前，先用默认关闭的影子校验证明 v2.1 isotropic 插件复现现有 worker
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

验证结果：bridge 8 项、shadow/apply 16 项通过；`tests.test_v2_isotropic_capability`
2 项通过（2D 三步 + replay、3D 两步，baseline vs apply 的 snapshot/state 完全一致；
2D 三步包含重复 SiO2 沉积）。现有 `tests.test_isotropic_dispatch` 12 项在开关
关闭/打开下都通过。离线 parity 还验证了 2D etch/deposit 的 snapshot JSON
（除 run-relative outputs）与 native state 文件 `stateId` 与旧路径完全一致。

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
  （没有热加载）。
- replay 需要的插件版本没装是硬错误，不会用邻近/最新版本顶替。
- 当前只注册 `isotropic`（`1.0.0`，变体 `native`）；`voxel_probe` 是树内 proof 插件，
  没有 manifest，不在 `plugins/` 与 lock 里。

字段、哈希覆盖范围、失败场景与运维/开发清单见
[`docs/plugin-lifecycle.md`](../../docs/plugin-lifecycle.md)。

## 当前限制

- 未切换默认生产执行路径，不改项目 schema；项目暂时不能引用 v2.1 变体。
  仅有的 worker 接触点是上面的默认关闭影子/验证 apply 开关。
- 没有 warm pool、引擎复用池、并发调度；`StaticEngine.release()` 是 no-op。
- 没有外部引擎（`external` 只是枚举值），没有自动引擎/变体选择。
- `FieldState` 只是占位；字段/cell-set 的数值路径未实现，adapter 会显式拒绝。
- voxel 适配器只支持 height-field；fin/GDS cut-out 在 prepare 阶段被拒绝。
- 只有 isotropic（native）是已安装插件（`plugins/isotropic/plugin.toml` + lock）；
  voxel_probe（surrogate proof）仍在树内、没有 manifest，只能由代码显式注册。
  快照、VTK、预览、事件、产物落盘仍属未来的 executor。
- 插件生命周期本切片只做 manifest/发现/lock：没有 conformance harness、warm pool、
  API/UI、插件市场、不可信代码沙箱，也没有自动引擎/变体选择。
