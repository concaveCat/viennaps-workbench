# 插件组织与数据流

> 状态：v2.2 契约 + 生命周期 + 第一个真实插件（isotropic）已落地。
> 本文描述**插件应该如何组织、数据如何流动**；field/layer 持久化和通用 executor
> 仍未实现，文中明确标出。

## 1. 分层

| 层 | 代码 | 职责 |
| --- | --- | --- |
| Runtime / executor | `worker/` | 加载流程状态、checkpoint、快照、种子、产物落盘、引擎策略 |
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
        ├─ snapshot(S_{i+1})
        ├─ native state (geometry)
        ├─ OutputSet artifacts/metrology
        └─ metadata/warnings + plan identity
        │
        ▼
next step uses S_{i+1}
```

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
  executor 必须在调用前 checkpoint。

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
  hash 进入 run record。
- replay 必须钉住：process/variant 版本、implementation hash、engine version/fingerprint、
  plugin lock、输入状态和输出 artifact。
- 插件升级 = 新 variant 版本或新 process 版本；旧 lock 仍可加载旧版本（如果安装）。

## 6. 当前实现状态

| 项 | 状态 |
| --- | --- |
| manifest / discovery / lock | ✅ |
| `ProcessSpec` / `VariantRegistration` / resolver / `ExecutionPlan` | ✅ |
| `ProcessState` / `StateDelta` / `StepResult` / `OutputSet` | ✅ 类型与校验 |
| native isotropic 插件（StepResult） | ✅ |
| voxel proof surrogate（StepResult） | ✅ |
| 标准插件包布局（isotropic 已在 `plugins/isotropic/`） | ✅ |
| field/layer store + persistence | ❌ |
| 通用 executor 消费 `StepResult`/`OutputSet` | ❌（worker 仍以旧路径为主，isotropic 有 shadow/apply 验证） |
| `OutputSet` 落盘 / API / UI | ❌ |
| external engine adapter | ❌ |
| conformance harness | ❌ |

## 7. 新增插件的清单

1. 建 `plugins/<id>/`，写 `plugin.toml`。
2. `params.py`：canonical Pydantic 参数，`extra="forbid"`。
3. `spec.py`：`ProcessSpec`；版本只随 schema/语义变。
4. `variants/`：每个实现一个 `EngineVariant`，声明 `reads/writes`、engine requirement、
   `mutates_input`、`output_kinds`。
5. `register.py`：只注册 spec + `VariantRegistration(process_id, variant)`。
6. 写 parity/conformance 测试；native 变体必须与 worker 路径对照。
7. 运行 `python scripts/lock-plugins.py` 生成 lock，`--check` 通过。
8. 不要在插件里做快照、文件路径、seed、engine policy 或全局状态。
