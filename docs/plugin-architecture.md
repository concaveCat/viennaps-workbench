# 插件组织与数据流

> 状态：v2.2 契约 + 生命周期 + 第一个真实插件（isotropic）+ runtime/storage/conformance
> 基础已落地。本文描述**插件应该如何组织、数据如何流动**；这一片是运行时基础，
> 不是生产 backend 接入，也没有 API/UI，文中明确标出。

## 1. 分层

| 层 | 代码 | 职责 |
| --- | --- | --- |
| Runtime / executor | `worker/`、`process_models/v2/executor.py`、`storage.py` | 跨步骤 flow state、checkpoint、on-disk state/outputs、plan identity、引擎策略；v2 runner 尚未接入 backend |
| Conformance | `process_models/v2/conformance.py`、`output_schema.py` | 变体一致性检查、可序列化的输出契约描述 |
| Engine | `process_models/v2/engine.py` | `EngineRequirement` / `EngineLease` / provider；严格版本与后端匹配 |
| Service | `process_models/plugins/`、`registry.py`、`resolver.py` | manifest、发现、lock、版本解析、准入、`ExecutionPlan` |
| State | `process_models/v2/state.py`、`adapters.py` | `ProcessState`、`StateDelta`、`StepResult`、`OutputSet`、表示转换 |
| Plugin | `plugins/<id>/` | 工艺参数、spec、variant 实现、物理模型 |

原则：**plugin 只描述和执行工艺；runtime、持久化、快照、replay、UI 属于平台。**

## 2. 插件包布局

以 `isotropic` 为例：

```text
plugins/isotropic/
  plugin.toml              # api / id / version / entrypoint
  __init__.py              # 公开导出；外部包也可是顶层模块
  params.py                # canonical Pydantic params
  spec.py                  # ProcessSpec
  variants/
    __init__.py
    native.py              # EngineVariant(kind=native)
  register.py              # register(registry)
```

外部插件可以是：

```text
plugins/my_process/
  plugin.toml              # entrypoint = "my_process:register"
  my_process/
    __init__.py
    params.py
    spec.py
    variants.py
    register.py
```

loader 会把插件目录的父目录加入 `sys.path`，因此 manifest 里只写一个稳定的
`module:attribute`，不写文件路径。

### 2.1 职责边界

| 谁 | 负责 | 不负责 |
| --- | --- | --- |
| Plugin | params、spec、variants、reads/writes、物理模型、输出语义 | 快照、路径、seed、engine policy、全局状态 |
| Platform | manifest/lock、registry/resolver、engine lease、state adapter、executor、persistence、replay、API/UI | 具体工艺物理 |

### 2.2 一个插件至少包含

```python
# params.py
class MyParams(BaseModel): ...

# spec.py
MySpec = ProcessSpec(
    id="my_process", version="1.0.0", label="...",
    params_model=MyParams, dimensions=(2, 3), operations=("etch",),
)

# variants/native.py
class MyNativeVariant(EngineVariant):
    variant_id = "native"
    version = "1.0.0"
    capabilities = VariantCapabilities(
        dimensions=(2, 3),
        reads=frozenset({"geometry"}),
        writes=frozenset({"geometry"}),
    )
    engine = EngineRequirement(kind="native", engine_id="viennaps", min_version="4.6.2")

    def validate(self, ctx, params): ...
    def execute(self, state, params, ctx, engine) -> StepResult: ...

# register.py
def register(registry):
    registry.register_process(MySpec)
    registry.register_variant(VariantRegistration(process_id=MySpec.id, variant=MyNativeVariant()))
```

## 3. 单步数据流

```text
Project step (process_id, variant_id, params)
        │
        ▼
resolve_execution
  → ExecutionPlan { spec, variant, engine lease, state adapter }
        │
        ▼
executor loads ProcessState S_i
  { geometry, materials, layers, fields, voxels }
        │
        ├─ variant.mutates_input?
        │     → checkpoint native geometry (copy `Domain`) before execute
        ▼
adapter.prepare(S_i, variant.reads)
  - voxels: derive from geometry
  - layers/fields: must already be stored
        │
        ▼
variant.execute(S_i', params, ctx, engine_lease)
        │
        ▼
StepResult {
  state: StateDelta { geometry, layers, fields, voxels },
  outputs: OutputSet { metrology, artifacts },
  metadata, warnings
}
        │
        ▼
adapter.commit(S_i', result.state, variant.writes)
        │
        ▼
ProcessState S_{i+1}
        │
        ├─ StateStore: state.json + fields.npz
        ├─ OutputSet: outputs.json
        ├─ plan identity: plan.json; flow.json 记录整个 run
        └─ optional native geometry: geometry.vpsd
        │
        ▼
next step uses S_{i+1}
```

一句话概括：`S_i -> resolve -> checkpoint (mutates_input) -> prepare -> execute ->
commit -> S_{i+1} -> store`。`FlowExecutor` 负责串起这条链；单步调用仍可直接用
`ExecutionPlan.run()` / `run_step()`，但不会自动落盘。

### 3.1 状态 vs 输出

| 类别 | 类型 | 是否进入下一步 |
| --- | --- | --- |
| flow state | `StateDelta` → `ProcessState` | **是** |
| report outputs | `OutputSet`（metrology/artifacts） | **否** |
| small metadata | `StepResult.metadata` | 否，只进 run record |

如果下一步需要某个输出值，必须把它提升为 `Field`/`Layer`/`MetrologyRecord` 引用，
而不是让下一个插件去读上一个插件的 `metadata`。

### 3.2 不可变性

- `ProcessState` 是 frozen；插件只返回 `StateDelta`。
- `adapter.prepare/commit` 用 `dataclasses.replace` 生成新状态，不原地改输入。
- `mutates_input=True` 的 native 变体（如 isotropic）会原地改 native geometry handle；
  `FlowExecutor` 在调用前用 `engine.handle.Domain(state.geometry.handle)` 复制一份
  geometry，再用副本重新 resolve，保证 `plan.request.state` 指向副本。引擎不暴露
  `Domain` 时直接抛 `ExecutionResolutionError`，不做“假定可重入”的降级。

### 3.3 运行时组件

| 组件 | 模块 | 职责 |
| --- | --- | --- |
| `StateStore` | `process_models/v2/storage.py` | 逐步骤保存/读取 `ProcessState`、`OutputSet`、plan identity 与 `flow.json`；原子 JSON 写（`tmp` + `os.replace`） |
| `FlowExecutor` | `process_models/v2/executor.py` | 消费 `FlowStep` 列表，执行 `S_i -> resolve -> checkpoint -> prepare -> execute -> commit -> S_{i+1}`，逐步骤落盘并返回 `FlowRun` |
| `check_variant` / `ConformanceReport` | `process_models/v2/conformance.py` | 对单个变体做契约级一致性检查（解析、校验、`StepResult` 形状、输入不可变、strict determinism） |
| `output_contract_schema` | `process_models/v2/output_schema.py` | 返回 `StepResult`/`StateDelta`/`OutputSet` 等类型的 JSON 可序列化契约描述；是给未来 API/UI 的 schema，不是完整 JSON Schema 校验器 |

`FlowExecutor.run()` 的输入是 frozen 的 `FlowStep`（`step_id`、`process_id`、
`variant_id`、`params`、`context`）和初始 `ProcessState`；输出是 `FlowRun`：

```python
@dataclass(frozen=True)
class FlowRun:
    run_dir: Path
    outcomes: tuple[ExecutionOutcome, ...]
    state: ProcessState
```

### 3.4 落盘布局

```text
<root>/
  flow.json                    # 调用方提供的 run 描述（run id、steps、versions）
  steps/<step_id>/
    state.json                 # schemaVersion 1：materials / geometry / layers / fields 元数据
    fields.npz                 # 仅在有 fields/cell-set 时写；field_000, field_001, ... 按 JSON 顺序（可选 material_ids）
    outputs.json               # OutputSet：metrology + artifacts
    plan.json                  # plan.identity()
    geometry.vpsd              # 可选；引擎暴露 Writer 时由 FlowExecutor 写
```

规则：

- `fields.npz` 以 `allow_pickle=False` 读取；`Field.data` 必须是 numpy 数组，opaque
  handle 直接报 `StateStoreError`。
- `outputs.json` 拒绝 NaN/Inf 和不可序列化值，报 `StateStoreError`。
- `voxels` 是逐步骤 scratch，**不落盘**；下一步需要时由 adapter 从 geometry 重新派生。
- `geometry` 只记录 `geometryRef`（通常是 `geometry.vpsd`）和维度/`gridDelta`/材料顺序。
  `StateStore.load_state()` 不内置原生几何 replay loader：有 ref 但没有调用方传入的
  `geometry_loader` 时直接报错，不做静默降级。

### 3.5 Conformance 与输出契约

`check_variant(...)` 检查的是契约，不是物理精度：

- registry 能解析 `process_id` / `variant_id`，参数类型匹配 `ProcessSpec.params_model`；
- `variant.validate()` 返回的 error 级 `Issue` 会让检查失败；
- `execute_plan()` 必须返回带 `StateDelta` 和 `OutputSet` 的 `StepResult`；
- `mutates_input=False` 时，输入 state 的 geometry/layers/fields handle 在 execute 后
  身份不变；
- 声明 `determinism="strict"` 时，两次从 `state_factory()` 出发的运行产生相等的
  state delta / outputs（可传 `digest` 回调；handle 不可比较时退化为 metadata/outputs
  比较）。

`output_contract_schema()` 给出同一组类型的 `schemaVersion: 1` 描述，供未来 API/UI
复用；当前没有 HTTP API 或前端字段消费它。

## 4. 三类插件的数据流

### 4.1 native

```text
ProcessState.geometry.handle + EngineLease.handle (ps)
        → native model / Process
        → geometry delta (+ optional layers/fields)
```

- 当前：`isotropic`（2D/3D，uniform/material level-set，mask，StepResult）。
- 引擎：ViennaPS；CPU/GPU 由 engine requirement 选择。

### 4.2 surrogate

```text
ProcessState { geometry, voxels, fields, layers } + params
        → pure Python/NumPy model
        → typed StateDelta / OutputSet
```

- 当前：`voxel_probe`（proof；height-field identity/erode）。
- 表示转换由 adapter 负责；surrogate 不直接持有 `ps`。

### 4.3 external

```text
stable input package (mesh/voxel/field files or arrays)
        → external engine/binary
        → stable output package
        → platform converts to StateDelta / OutputSet
```

- 当前：仅枚举 `external`，未实现。
- 需要：进程隔离、版本/哈希固定、输入/输出 schema、超时/资源上限。

## 5. 多步 flow 与 replay

```text
S_0 → step_1 → S_1 → step_2 → S_2 → ... → S_n
```

- 每个 step 的 `ExecutionPlan.identity()` + 输入状态 hash + params hash + 输出 artifact
  hash 进入 run record。`FlowExecutor` 至少把 plan identity 写进 `steps/<step_id>/plan.json`，
  并在 `flow.json` 里记录本次 run 的步骤顺序与版本；`StateStore` 固定
  `state.json` / `fields.npz` / `outputs.json` 的 schema。
- replay 必须钉住：process/variant 版本、implementation hash、engine version/fingerprint、
  plugin lock、输入状态和输出 artifact。
- 原生几何 replay 还缺 loader：`state.json` 只有 `geometryRef`，重载需要调用方提供能把
  ref 变回引擎 handle 的 `geometry_loader`；store 本身不生成/解析 `.vpsd`。
- 插件升级 = 新 variant 版本或新 process 版本；旧 lock 仍可加载旧版本（如果安装）。

## 6. 当前实现状态

> 这一片的具体测试项按实际运行单独报告；本文只描述职责与边界，不写死数量。

| 项 | 状态 |
| --- | --- |
| manifest / discovery / lock | ✅ |
| `ProcessSpec` / `VariantRegistration` / resolver / `ExecutionPlan` | ✅ |
| `ProcessState` / `StateDelta` / `StepResult` / `OutputSet` | ✅ 类型与校验 |
| native isotropic 插件（StepResult） | ✅ |
| voxel proof surrogate（StepResult） | ✅ |
| 标准插件包布局（isotropic 已在 `plugins/isotropic/`） | ✅ |
| field/layer store + persistence | ✅ `StateStore`：`state.json` + `fields.npz`，原子写，geometry 只存 ref |
| 通用 executor 消费 `StepResult`/`OutputSet` | ✅ `FlowExecutor`：多步状态转移、`mutates_input` checkpoint、逐 step 落盘；**尚未接入 backend** |
| `OutputSet` 落盘 | ✅ `outputs.json`（metrology/artifacts，拒绝 NaN/Inf） |
| `OutputSet` API / UI | ❌ 只有 Python 契约和 `output_contract_schema()` |
| conformance harness | ✅ `check_variant()`：解析、校验、`StepResult` 形状、输入不可变、strict determinism |
| output contract schema | ✅ `output_contract_schema()`，JSON 可序列化，`schemaVersion: 1` |
| external engine adapter | ❌ |
| 原生 geometry replay loader | ❌ `geometryRef` 已记录；重载需调用方提供 loader |
| v2 run 接入 backend/API/UI | ❌ |

### 6.1 本切片仍未完成

- **没有 API/UI**：`output_contract_schema()` 只是契约描述，项目 JSON、HTTP API 和前端
  都还不会引用 v2 run。
- **没有原生几何 replay loader**：`StateStore` 记录 `geometryRef`（以及可选的
  `geometry.vpsd`），但把 ref 重新加载成引擎 handle 的 loader 由调用方提供；
  缺失时 `load_state()` 直接报错。
- **没有外部引擎 adapter**：`external` 仍只是枚举值，registry/provider 只支持 ViennaPS
  原生路径。
- **没有 backend 集成**：`FlowExecutor` 是独立运行时组件；worker/正式服务仍走旧路径，
  v2 只有默认关闭的影子/apply 开关（v2 文档 §7.5），没有生产 cutover。

## 7. 新增插件的清单

1. 建 `plugins/<id>/`，写 `plugin.toml`。
2. `params.py`：canonical Pydantic 参数，`extra="forbid"`。
3. `spec.py`：`ProcessSpec`；版本只随 schema/语义变。
4. `variants/`：每个实现一个 `EngineVariant`，声明 `reads/writes`、engine requirement、
   `mutates_input`、`output_kinds`。
5. `register.py`：只注册 spec + `VariantRegistration(process_id, variant)`。
6. 写 parity/conformance 测试；至少跑一次 `check_variant(...)`，native 变体还必须与
   worker 路径对照。
7. 运行 `python scripts/lock-plugins.py` 生成 lock，`--check` 通过。
8. 不要在插件里做快照、文件路径、seed、engine policy 或全局状态。
