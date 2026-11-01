# 工艺模型插件 v2：设计/实现说明

> 范围：`process_models/v2/`。v1（`process_models/contract.py`、`registry.py`、
> `voxel.py`、`surrogate/voxel_probe.py`）**不改动**；本切片不碰 backend/worker，
> 不启动服务，不改项目 schema。
>
> 本文描述 v2 切片的契约与边界。凡是 v2 包内没有对应模块的能力，文中一律标为
> “未实现”，不把设计意图写成已完成状态。

## 0. 一页结论

v1 把四件事塞进了同一层：进程身份、实现注册、原生引擎句柄、单表示状态。结果是
契约里到处是 `ps`/`domain`，任何一步都必须返回一个原生 domain，实现哈希只覆盖类源码，
加一个变体就要顶一个 plugin 版本。v2 的修法不是“再加字段”，而是**拆层并显式化**：

| 关注点 | v1 | v2 |
| --- | --- | --- |
| 身份 | `ProcessPlugin`（schema + 变体表 + 版本） | `ProcessSpec`（只有 schema/语义） + `VariantRegistration`（只有实现） |
| 引擎 | 隐含在 `ProcessState.ps` | `Engine` / `EngineLease` / `EngineRequirement`，显式租约与指纹 |
| 状态 | `ProcessState.ps/domain` + `ProcessResult.domain` | `ProcessState{geometry, fields, voxels, materials}` + `StateDelta` |
| 表示转换 | 变体自己调 `read_voxels`/`write_voxels` | `StateAdapter.prepare/commit`，变体只看见自己声明的表示 |
| 解析 | `registry.resolve()` 只查表 | `resolve_execution()` 做参数/能力/引擎/适配器全链路准入并产出 `ExecutionPlan` |
| 实现哈希 | `inspect.getsource(type(variant))` | 变体模块的**全部 .py 文件字节** + 类 qualname |

## 1. v1 的结构问题

### 1.1 原生状态泄漏进契约

`process_models/contract.py` 的 `ProcessState` 直接暴露执行器内部对象：

```python
@dataclass
class ProcessState:
    ps: Any          # ViennaPS 模块
    domain: Any      # 原生 level-set domain
    dimension: int
    grid_delta: float
    materials: dict[str, Any]
    seed: int = 0
    run_dir: Path | None = None
    logger: Any = None
    fields: dict[str, Any] | None = None
```

后果：

- 纯 Python surrogate 也被迫持有一个它不会用的 `ps` 模块和 `domain`；
- `seed` / `run_dir` / `logger` 是 **executor** 的职责，却出现在插件边界上，谁写谁读没有约束；
- `fields: dict[str, Any] | None` 是个没有类型的口袋，voxel、标量场、掩码都能往里塞，
  下游无法判断里面到底是什么；
- 契约模块因此事实上依赖 ViennaPS 的对象模型，不能在没有原生环境的地方做纯契约测试。

### 1.2 强制返回原生几何

```python
@dataclass
class ProcessResult:
    domain: Any                     # 必填
    metadata: dict[str, Any] = ...
    outputs: list[str] = ...
    warnings: list[str] = ...
```

`domain` 没有默认值，也不能为 `None`。于是一个只改 voxel 的步骤、一个只产出标量场的
步骤，都必须伪造一个 domain 才能返回。反过来，框架也无法表达“这一步只更新了材料表”
或“几何没变，只有 voxel 视图变了”。

### 1.3 实现身份只看类源码

`registry._implementation_hash()`：

```python
source = inspect.getsource(type(variant))
return hashlib.sha256(source.encode("utf-8")).hexdigest()
```

`voxel_probe` 的实际逻辑有一半在模块级函数 `_erode_top_layers()` 里。改这个函数、
改常量 `MAX_LAYERS`、改同模块的 `VoxelProbeParams` 默认值，**类源码不变，哈希就不变**。
replay 记录里的 `implementation_hash` 因此会“匹配”一个行为已经不同的实现。

### 1.4 plugin 版本把 schema 和变体集合绑死

v1 的注册单元是 `ProcessPlugin`：`id + version` 唯一，`variants` 是它的属性，重复注册
同一 `(id, version)` 直接 `ValueError`。于是：

- 只想**新增一个变体**（例如给 `isotropic` 加一个 surrogate）也必须发新 plugin 版本，
  而这个新版本在语义上并没有改参数 schema；
- 只想**改参数 schema**（加一个可选字段）也会顺手改掉“这个版本里有哪些变体”的身份；
- `ModelDescriptor.version` 同时被当作“schema 版本”和“变体集合版本”，replay 无法区分
  “schema 没变、实现换了”和“schema 变了”。

### 1.5 原地修改与返回新对象语义不清

`ProcessState` 的 docstring 写的是：

> A variant mutates `domain` in place and returns it inside `ProcessResult`.

但 `voxel_probe` 明确**不**原地改，它 `write_voxels()` 出一个新 domain 再返回。两种写法
都“合法”，框架却无法知道：

- 输入 state 在调用后是否还有效（能否复用、能否回滚）；
- `ProcessResult.domain` 是同一个对象还是新对象；
- 出错时已改到一半的几何要不要回滚；
- 哪些变体需要在执行前 checkpoint。

### 1.6 没有引擎/运行时/状态解析器

v1 的 `VariantCapabilities` 自己就写着：

> Capabilities are declarative metadata only; the registry does not enforce them.

`engines: list[str]` 只是个字符串列表，没有版本、没有 backend、没有“能不能拿到”。
`registry.resolve()` 只做 ID → 对象，不做：

- 参数模型类型检查；
- 维度/读写状态集检查；
- 材料表与几何一致性检查；
- 引擎版本/后端可满足性检查；
- 状态适配器选择；
- 一次性产出可用于 replay 的完整身份。

这些都得由未来调用方各自实现，等于把契约的漏洞留给每个调用点。

## 2. v2 的分层模型

四层 + 一个解析器。分层的判据是“谁拥有生命周期”：

| 层 | 模块 | 职责 | 本切片状态 |
| --- | --- | --- | --- |
| **Runtime** | 无（executor 边界） | 跨步骤持有 flow state、checkpoint/rollback、run 目录、快照、事件、种子、资源上限、超时 | **未实现**。只定义 `ProcessState` / `StateDelta` / `Artifact` 作为 runtime 将来对接的面 |
| **Engine** | `engine.py` | 描述引擎能力、按 `EngineRequirement` 严格租用、给出 `engine_version` + `fingerprint` | 实现 `StaticEngine` / `StaticEngineProvider`（包装已初始化的句柄）；**没有**外部引擎、没有 warm pool、没有调度 |
| **Service** | `registry.py` + `resolver.py` | 目录（多版本 spec/variant）、准入检查、解析出 `ExecutionPlan` | 内存注册表 + 纯函数解析器；**没有**持久化、没有 HTTP API、没有插件发现 |
| **State** | `state.py` + `adapters.py` | 多表示状态、`StateDelta`、`prepare/commit` 转换 | 四种表示类型 + `DefaultStateAdapter`；字段/cell-set 只占位 |
| **Resolver** | `resolver.py` | 把 request 变成 plan：能力、参数、引擎、适配器、身份 | `resolve_execution()`，只解析不执行 |

为什么这样切：

- **Engine 不认识工艺**。它只回答“我是谁、什么版本、什么后端、能不能满足这个 requirement”。
  同一个 `viennaps` 引擎可以服务 isotropic、sf6o2、oxidation 多个 process。
- **Service 不认识原生对象**。`registry.py` 不 import `viennaps`；`resolver.py` 只在
  `provider.acquire()` 处接触引擎，不 import 具体变体模块。
- **State 不决定物理**。`ProcessState` 只是“这一步能看到什么”，谁写、写多少由
  `VariantCapabilities.reads/writes` 声明。
- **Runtime 是唯一拥有跨步骤生命周期的地方**，所以它被刻意排除在本切片之外：
  没有 warm pool、没有 worker 接入，避免在设计未定时把 executor 语义固化进契约。

### 2.1 本切片明确不做

| 能力 | 状态 | 原因 |
| --- | --- | --- |
| worker/backend 接入 | 未实现 | 本切片只到包内契约与两个插件 |
| warm pool / 引擎复用池 | 未实现 | `StaticEngine` 只包装已存在的句柄，`release()` 是 no-op |
| 外部引擎（Sentaurus 等） | 未实现 | `EngineKind` 有 `"external"`，但没有 provider |
| 字段 / cell-set 表示 | 未实现 | `FieldState` 是占位；adapter 在 `prepare` 阶段直接报错 |
| 插件发现/动态导入 | 未实现 | 仍然显式注册 |
| 资源上限、超时、沙箱 | 未实现 | 属于 Runtime |
| 自动引擎/变体选择 | 未实现 | 显式 `variant_id`，无降级 |

## 3. 契约类型

### 3.1 身份：spec 与 registration 分开

| 类型 | 文件 | 回答的问题 | 关键字段 |
| --- | --- | --- | --- |
| `ProcessSpec` | `contract.py` | 这是什么工艺、参数长什么样 | `id`、`version`、`label`、`params_model`、`dimensions`、`operations`、`description` |
| `VariantRegistration` | `contract.py` | 用哪个实现、要什么引擎、覆盖哪些状态 | `process_id`、`variant_id`、`version`、`variant`、`capabilities`、`engine`、`implementation_hash` |
| `EngineVariant` | `contract.py` | 怎么校验、怎么执行 | `variant_id`、`version`、`capabilities`、`implementation_hash`、`validate()`、`execute()` |
| `ResolvedVariant` | `registry.py` | 解析结果的四元身份 | `process`、`variant`、`process_version`、`variant_version`、`implementation_hash` |

```python
@dataclass(frozen=True)
class ProcessSpec:
    id: str
    version: str
    label: str
    params_model: type[BaseModel]
    dimensions: tuple[int, ...]
    operations: tuple[str, ...]
    description: str = ""
```

`ProcessSpec.version` 只随**参数 schema 或公开语义**变；新增变体不动它。`VariantRegistration`
自带 `version`，所以“同 schema、两个实现版本”是合法且可区分的状态。

### 3.2 上下文与能力

```python
@dataclass(frozen=True)
class ProcessContext:
    dimension: int                 # 2 或 3
    grid_delta: float              # > 0
    project: Mapping[str, Any]     # 只读原始 JSON
    step: Mapping[str, Any]
    materials: MaterialState

@dataclass(frozen=True)
class VariantCapabilities:
    dimensions: tuple[int, ...]
    reads: frozenset[StateKind]
    writes: frozenset[StateKind]
    geometry_constraints: tuple[str, ...] = ()   # ("height-field",)
    supports_fields: bool = False
    supports_masks: bool = False
    experimental: bool = False
    mutates_input: bool = False
    determinism: Literal["strict", "tolerance"] = "strict"
```

`reads`/`writes` 是**准入依据**，不是注释：`resolve_execution()` 会检查 `reads` 里
每一种状态是否存在，`DefaultStateAdapter.commit()` 会检查 `writes` 是否允许这种写入。

### 3.3 状态与增量

| 类型 | 表示 | 说明 |
| --- | --- | --- |
| `MaterialState` | `order: tuple[int, ...]` + `names: Mapping[int, str]` | 原生材料 ID 与显示名；`name_for()` / `id_for()` 双向查 |
| `LevelSetState` | `handle` + `dimension` + `grid_delta` + `material_order` | flow state 的唯一持久表示 |
| `FieldState` | `data: Mapping[str, Any]` | 阶段 1 占位，没有 cell-set 集成 |
| `VoxelState` | `grid`（`process_models.voxel.VoxelGrid`）+ `source` | 步骤内视图，默认 `"height-field-v1"` |
| `ProcessState` | 上面四个 + `engine` + `meta` | `has(kind)` / `require(kind)`；`require` 缺失时抛 `StateMissingError` |
| `StateDelta` | `geometry` / `fields` / `voxels` / `artifacts` / `metadata` / `warnings` | 字段为 `None` 表示“没变”，不是“清空” |

```python
@dataclass
class StateDelta:
    geometry: LevelSetState | None = None   # None = unchanged
    fields: FieldState | None = None
    voxels: VoxelState | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def is_empty(self) -> bool: ...   # 只看状态，artifacts/metadata 不算状态变化
```

关键语义变化：**变体只声明变化，不负责合并**。合并、写回、清空临时视图由 adapter 完成。

### 3.4 引擎

```python
@dataclass(frozen=True)
class EngineRequirement:
    kind: EngineKind                  # native / surrogate / external
    engine_id: str                    # "viennaps", "python", "sentaurus"
    min_version: str | None = None
    max_version: str | None = None
    backend: Backend = "any"          # cpu / gpu / any
    capabilities: tuple[str, ...] = ()

class EngineLease(Protocol):
    engine_id: str
    engine_version: str
    kind: EngineKind
    backend: str
    fingerprint: str
    handle: Any
    def release(self) -> None: ...
```

`StaticEngine.acquire()` 是**严格**的：`kind`、`engine_id`、版本区间、`backend` 任何一项
不满足都抛 `EngineUnavailableError`，不回退到别的引擎。版本比较只支持
`MAJOR.MINOR.PATCH` 的 `min/max`，不实现 semver 范围语法。

### 3.5 执行请求与计划

```python
@dataclass(frozen=True)
class ExecutionRequest:
    process_id: str
    variant_id: str
    params: BaseModel
    context: ProcessContext
    state: ProcessState
    expected_process_version: str | None = None
    expected_variant_version: str | None = None

@dataclass(frozen=True)
class ExecutionPlan:
    process: ProcessSpec
    variant: VariantRegistration
    engine: EngineLease
    state_adapter: StateAdapter
    process_version: str
    variant_version: str
    implementation_hash: str
    engine_version: str
    engine_fingerprint: str
    def identity(self) -> dict[str, str]: ...

@dataclass(frozen=True)
class ExecutionOutcome:
    plan: ExecutionPlan
    state: ProcessState
    delta: StateDelta
    # metadata / warnings / artifacts delegate to delta

def execute_plan(plan, state, params, context) -> ExecutionOutcome: ...
```

`ExecutionPlan` 是**唯一**允许被写进 run 记录的身份来源；调用方不要自己拼字段。
`execute_plan()` 是唯一的执行入口：它把 engine lease 绑到 state 上，调用
`adapter.prepare`、`variant.execute`、`adapter.commit`，并把 `StateDelta` 原样放进
`ExecutionOutcome`，供 executor 记录 metadata/warnings/artifacts。

### 3.6 适配器

```python
class StateAdapter(Protocol):
    id: str
    provides: frozenset[StateKind]   # 能从现有 state 派生出的表示，例如 {"voxels"}
    def prepare(self, state: ProcessState, capabilities: VariantCapabilities) -> ProcessState: ...
    def commit(self, state: ProcessState, delta: StateDelta, capabilities: VariantCapabilities) -> ProcessState: ...
```

规则：`prepare`/`commit` 都**不得原地修改输入**，必须 `dataclasses.replace()` 出新对象。
`DefaultStateAdapter.id == "default-v2"`，`provides == frozenset({"voxels"})`。
`commit` 会拒绝“返回了未在 `capabilities.writes` 中声明的表示”的变体。

## 4. 执行流程

`resolve_execution()` 只解析；`execute_plan()` 负责真正执行一次 plan，并返回
`ExecutionOutcome`。未来的 runtime 只需要拿到 plan 后调用 `execute_plan()`。

```
 caller           registry        resolver              provider        adapter         variant
   |                 |               |                      |              |               |
   | ExecutionRequest|               |                      |              |               |
   |---------------->|               |                      |              |               |
   |                 | get_variant(  |                      |              |               |
   |                 |  process_id,  |                      |              |               |
   |                 |  variant_id,  |                      |              |               |
   |                 |  pins)        |                      |              |               |
   |                 |--Resolved---->|                      |              |               |
   |                 |               | 1 params isinstance  |              |               |
   |                 |               |   process.params_model|              |               |
   |                 |               | 2 ctx.dimension ∈ caps.dimensions    |               |
   |                 |               | 3 state has all caps.reads           |               |
   |                 |               | 4 materials ↔ geometry.order         |               |
   |                 |               | 5 variant.validate(ctx, params)      |               |
   |                 |               |---------------------->|              |               |
   |                 |               |<---- list[Issue] -----|              |               |
   |                 |               | 6 provider.acquire(requirement)      |               |
   |                 |               |---------------------->|              |               |
   |                 |               |<---- EngineLease -----|              |               |
   |                 |               | 7 adapter or DefaultStateAdapter()   |               |
   |                 |<--ExecutionPlan (含 identity) ----------------------|               |
   |                 |               |                      |              |               |
   |  (runtime) execute             |                      |              |               |
   |------------------------------------------------------------------>|  |               |
   |                 |               |                      |  prepare()  |               |
   |                 |               |                      |------------>|               |
   |                 |               |                      |             | execute()     |
   |                 |               |                      |             |-------------->|
   |                 |               |                      |             |<--StateDelta--|
   |                 |               |                      |  commit()   |               |
   |                 |               |                      |------------>|               |
   |<--------------------------- new ProcessState (delta applied) -------|               |
```

逐步说明：

1. `registry.get_variant()` 按 `(process_id, variant_id, process_version?, variant_version?)`
   取 `ResolvedVariant`；带 pin 时缺失即报错，不降级。
2. 参数必须是 `spec.params_model` 的实例，否则 `ExecutionResolutionError`。
3. `context.dimension` 必须在该变体的 `capabilities.dimensions` 内。
4. `state` 必须满足 `capabilities.reads` 的每一种状态（`state.has()`）。
5. 同时存在 `ProcessState.materials` 与 `geometry.material_order` 时必须一致。
6. `variant.validate()` 返回任何 `severity == "error"` 的 `Issue` 都终止解析。
7. `provider.acquire()` 失败 → `EngineUnavailableError` → 包成 `ExecutionResolutionError`，
   **不尝试其他引擎**。
8. 适配器缺省为 `DefaultStateAdapter()`；调用方可以传自定义适配器。
9. 返回 plan，**不调用** `variant.execute()`。

`execute_plan(plan, state, params, context)` 随后做四件事：

1. `dataclasses.replace(state, engine=plan.engine)`，把 lease 绑定到 state；
2. `adapter.prepare()` 补齐 `capabilities.reads` 需要的表示；
3. `variant.execute()`，结果必须是 `StateDelta`，否则 `ExecutionResolutionError`；
4. `adapter.commit()` 合并 delta，返回 `ExecutionOutcome(plan, state, delta)`。

执行阶段由 adapter 收尾：

| 阶段 | 谁做 | 做什么 |
| --- | --- | --- |
| `prepare` | adapter | 补齐变体声明要读、但当前 state 里没有的表示（v1 切片：geometry → voxels） |
| `execute` | variant | 只读 `state`，只返回 `StateDelta`，不写盘、不碰 adapter |
| `commit` | adapter | 把 delta 合并进新 `ProcessState`；`delta.voxels` 在有 geometry 时回写成新几何并清空 voxel 视图 |

## 5. 版本与 replay 身份

四类版本，互不替代：

| 项 | 存在哪 | 什么时候升 | 作用 |
| --- | --- | --- | --- |
| `process_version` | `ProcessSpec.version` | 参数 schema 或公开语义变 | 固定“这一步的参数含义” |
| `variant_version` | `VariantRegistration.version` | 实现行为/数值变 | 固定“这一版实现” |
| `implementation_hash` | `VariantRegistration.implementation_hash` | 代码字节变（自动） | 发现“版本号没升但代码改了” |
| `engine_version` + `engine_fingerprint` | `EngineLease` | 引擎装/换/编译选项变 | 固定“用哪个引擎跑的” |

**实现哈希覆盖范围**（v2 修正点）：

```python
# registry.compute_implementation_hash(variant)
# 1) inspect.getsourcefile(type(variant)) 取变体类所在模块文件
# 2) 把该模块目录下参与实现的 .py 文件字节按相对路径排序后一起喂给 sha256
# 3) 再把 type(variant).__module__ + "." + __qualname__ 写进哈希流
# 4) 拿不到文件时退化为类源码哈希（并记录该退化，不假装等价）
```

与 v1 的差别：`_erode_top_layers()` 这种模块级 helper、模块常量、同文件 params 默认值
发生变化都会改变哈希。仍未覆盖的是**跨模块 import 的第三方行为**（例如 numpy 版本），
那由 `engine_version`/依赖锁定负责，不由实现哈希负责。

**没有静默回退**：

| 情况 | 结果 |
| --- | --- |
| 变体不存在 / pin 的版本不存在 | `KeyError` → 调用方 4xx |
| 参数类型不对 | `ExecutionResolutionError` |
| 维度/状态/材料不满足 | `ExecutionResolutionError` |
| `variant.validate` 有 error | `ExecutionResolutionError`（带 issue 列表） |
| 引擎缺失/版本不符/后端不符 | `EngineUnavailableError` → `ExecutionResolutionError` |
| 写入了未声明的状态种类 | `DefaultStateAdapter.commit` 抛 `ExecutionResolutionError` |

**旧 run 如何固定**：run 记录保存 `ExecutionPlan.identity()` 的九个键
（`process_id`、`process_version`、`variant_id`、`variant_version`、`implementation_hash`、
`engine_id`、`engine_version`、`engine_fingerprint`、`adapter`）。replay 时把这些值转成
`expected_*` pin 重新 `resolve_execution()`，再逐键比对；任何一项不同都拒绝执行，
既不“警告后继续”，也不“用最新版本代替”。

版本保留策略（旧版本能留多久）是开放决策，见 §10；本切片注册表只是内存字典。

## 6. 状态表示与逐步 voxel 适配器（方案 1.a）

### 6.1 flow state 仍是 level-set

```
step N-1:  LevelSetState ──┐
                           │  prepare: read_voxels(geometry.handle)
step N:                    ├─> VoxelState (步骤内，临时)
                           │  variant.execute -> StateDelta(voxels=...)
                           │  commit: write_voxels(geometry.handle, grid)
step N+1:  LevelSetState <─┘
```

- voxel 栅格**不进快照、不进 replay、不跨步骤保存**；
- 只有声明 `"voxels" in reads` 的变体才会触发转换；
- 转换由 adapter 负责，变体不 import `process_models.voxel`；
- 材料表（`MaterialState`）独立于几何存在，因为 `write_voxels` 需要原始材料顺序。

### 6.2 四种表示的分工

| 表示 | 谁产生 | 谁消费 | 本切片 |
| --- | --- | --- | --- |
| `geometry`（`LevelSetState`） | 原生变体 / adapter 回写 | 原生变体、voxel 读取 | 实现 |
| `fields`（`FieldState`） | 未来 field/cell-set 插件 | 未来插件 | 只有类型；adapter `prepare` 直接拒绝 |
| `voxels`（`VoxelState`） | adapter（从 geometry） | surrogate / voxel-native 插件 | 实现（height-field） |
| `materials`（`MaterialState`） | 调用方 / 几何元数据 | 所有写入几何的变体 | 实现 |

### 6.3 voxel 适配器的硬边界

`process_models/voxel.py` 的能力（v1 已有，v2 复用，不重写）：

- 读：`viennals.ToVoxelMesh`，按全局 cell index 建 dense 栅格，`-1` 为 void；
- 写：提取每个累积材料区域的边界面，`FromSurfaceMesh` + `setRemoveBoundaryTriangles(False)`
  重建新 domain，再按原顺序 `insertNextLevelSetAsMaterial` 恢复材料句柄；
- **只支持 height-field**：fin/GDS cut-out 在 `read_voxels` 阶段就被拒绝
  （`_HEIGHT_SLACK_CELLS = 2.0`）；
- 只允许删除**最外层前缀**材料层，不能加材料、不能删中间层；
- `VoxelGrid.fields` 在阶段 1 必须为空。

v2 只加了一层：`VoxelState.grid` 承载 `VoxelGrid`，`source` 标记适配器版本
（`"height-field-v1"`），并在 commit 时校验“voxel 写入必须有原始 geometry”。

### 6.4 未来的 field / cell-set

未实现，但类型已经预留：

- `FieldState.data` 是 `Mapping[str, Any]`，将来承载标量场/覆盖率/cell-set 标签；
- `VariantCapabilities.supports_fields` 表示变体能读写字段；
- `DefaultStateAdapter.prepare` 目前对 `"fields" in reads` 且 `state.fields is None`
  直接抛 `StateMissingError("field state is not available in phase 1")`，即**显式不可用**，
  不是空字典冒充。

## 7. 第一个真实工艺：isotropic 原生插件

文件：`process_models/v2/native/isotropic.py`。

### 7.1 复用而不是重写

变体直接调用现有 worker helper，保证与线上路径同源：

```python
import worker.simulate as simulate          # 延迟 import，避免契约层依赖 worker

ps = state.engine.handle
domain = state.geometry.handle
if params.operation == "deposit":
    domain.duplicateTopLevelSet(simulate.material_handle(ps, params.depositMaterial))

rate, signed_material_rates = simulate.signed_rates(step_dict)
```

| 复用的 helper | 来源 | 作用 |
| --- | --- | --- |
| `simulate.material_handle(ps, name)` | `worker/simulate.py:995` | 名字 → 原生材料句柄，找不到就报错 |
| `simulate.signed_rates(step)` | `backend/rates.py:77`（`simulate` 转出） | etch 取负、deposit 取正；返回默认速率 + 逐材料速率 |
| `simulate.protected_material_names(project, step)` | `worker/mask_materials.py:14` | 几何派生的掩码材料；`rateMode == "material"` 时为空 |
| `simulate.configure_advection(ps, project, time_step_ratio=...)` | `worker/simulate.py:565` | solver 默认值 + 步骤级 `isotropicTimeStepRatio` 覆盖，并记录实际值 |

原生对象构造与 `worker/steps/process.py` 的 isotropic 分支逐行对应：

| 情况 | worker 现在的写法 | v2 变体 |
| --- | --- | --- |
| 逐材料速率 | `IsotropicProcess(rate, maskMaterials=[])` + `setMaterialRate()` | 相同 |
| 统一速率 | `IsotropicProcess(rate, maskMaterials=[material_handle(...)])` | 相同；显式 `maskMaterials` 优先，否则用 `protected_material_names` |
| deposit | `domain.duplicateTopLevelSet(...)` | 相同 |
| 执行 | `ps.Process(domain, model, duration)`；`setFluxEngineType(CPU_DISK)`；`setParameters(advection)`；`apply()` | 相同 |

### 7.2 参数与声明

`IsotropicParams`（Pydantic v2，`extra="forbid"`）：`operation`（`etch`/`deposit`）、
`rateMagnitude > 0`、`duration > 0`、`depositMaterial`、`rateMode`（`uniform`/`material`）、
`materialRates`、`maskMaterials`、`isotropicTimeStepRatio ∈ [0.05, 0.5]`。

```python
VariantCapabilities(
    dimensions=(2, 3),
    reads=frozenset({"geometry"}),
    writes=frozenset({"geometry"}),
    supports_masks=True,
    mutates_input=True,        # 原生 apply() 就地改 domain
    determinism="strict",
)
EngineRequirement(kind="native", engine_id="viennaps", min_version="4.6.2")
```

`mutates_input=True` 是诚实声明：原生路径就地改句柄，`commit` 信任 `delta.geometry.handle`。
**checkpoint 仍由未来 executor 负责**，本切片文档化但不强制。

### 7.3 “parity”具体指什么

本切片测试要求的口径（通过与否以测试结果为准，本文不预先声称）：

| 检查 | 判据 |
| --- | --- |
| 2D 平面刻蚀 | 表面位移 = `rateMagnitude * duration`，误差 ≤ 1e-6 |
| 2D 平面沉积 | 表面上升，且顶层材料为 `depositMaterial` |
| 逐材料速率 | 只有 `materialRates` 列出的材料移动 |
| 3D 平面 smoke | 能构造并完成一步 |
| 缺 geometry | resolver 报错，不进引擎 |
| 引擎版本不符 | `EngineUnavailableError` → resolver 报错，**不回退** |
| plan identity | 同一输入重复解析，`identity()` 逐键相同 |

parity 的含义是“**同样的参数、同样的原生调用、同样的 solver 配置，得到同样的表面位移**”，
不是“与线上 worker 的 run 记录逐字节一致”。

### 7.4 尚未覆盖

- `isotropicMethod == "geometric"` 分支（`build_isotropic_geometric`）不在 v2 变体内；
- `resolve_isotropic_method()` 的自动分派、dispatch reason 记录未搬；
- flux/GPU 路径、`engine_policy` 选择与证据校验未搬；
- 快照、VTK、预览、事件、种子、产物落盘仍属 executor；
- 没有 JSON schema / API / UI 字段，项目仍不能引用这个变体；
- 没有多步 flow 级回归。

## 8. 第一个 surrogate：voxel probe

文件：`process_models/v2/surrogate/voxel_probe.py`。从 v1 移植，语义不变。

| 项 | 值 |
| --- | --- |
| `variant_id` / `version` | `voxel_probe` / `1.0.0`（v1 的 `0.1.0` 语义不变） |
| `reads` | `{"geometry", "voxels"}` |
| `writes` | `{"voxels"}` |
| `geometry_constraints` | `("height-field",)` |
| `experimental` | `True` |
| `mutates_input` | `False` |
| engine | `surrogate` / `python` / `>=3.12` |

执行体只有一个动作：

```python
grid = state.voxels.grid               # adapter 已经准备好
if params.operation == "erode_top_layer":
    grid = _erode_top_layers(grid, params.layers)
return StateDelta(voxels=VoxelState(grid=grid), metadata={...})
```

**它不调用 `read_voxels` / `write_voxels`**。这是 v2 与 v1 的关键差别：v1 的变体自己
读写几何，v2 把转换交给 adapter，变体只处理自己声明的表示。

- `identity`：delta 回写后表面偏差为 0（plane/thin film 实测为 0）；
- `erode_top_layer`：表面下降约 1 个 `gridDelta`（trench 有阶梯误差）；
- 不允许写 geometry；即使 delta 里带了 `voxels`，commit 也会用原始 geometry 回写；
- 输入 state 不被修改。

**height-field 限制**：fin、GDS cut-out 在 `prepare` 阶段由 `read_voxels` 拒绝，
抛出的是 `VoxelAdapterError`（被 adapter 包装成解析/执行错误）。这不是精度问题，
是适配器能力边界。voxel_probe 也不是物理模型，只证明转换链路。

## 9. 迁移计划

顺序不能颠：先证明边界，再搬真实模型，最后才让项目引用。

| 阶段 | 内容 | 行为约束 | 退出条件 |
| --- | --- | --- | --- |
| 1. legacy wrapper | 现有 `CATALOG` / `build_specialized_model()` 包成 v2 变体 | 纯包装，不复制原生调用；schema、快照、哈希不变 | 同一 project 走 wrapper 与走原路径结果一致 |
| 2. isotropic | `native/isotropic.py` 落地 | 与 worker 路径 parity（§7.3）；不回退 legacy | 2D/3D、etch/deposit、逐材料速率全过 |
| 3. sf6o2 / oxidation | 按同一模式搬 | 逐模型搬，参数映射逐行对照 | 每个模型有独立对照测试 |
| 4. 可选 `variant` 字段 + API/UI | 项目 JSON 可写 `variant`；API 列 descriptor | 缺省不进请求哈希；显式写入才参与；前端只读 descriptor | 旧 fixture 哈希不变 |
| 5. 生产 CMP surrogate | 第一个非 proof 的 Python 模型 | 通过完整一致性测试；默认仍是 native；不自动选 surrogate | 参考 flow + golden |
| 6. field / cell-set 插件 | 实现 `FieldState` 与对应 adapter | 不改 level-set flow state 语义 | 字段可跨步骤、可 replay |

明确不做 big-bang 重写 `worker/simulate.py`：快照、种子、引擎策略、产物属于 executor
稳定面，插件化只替换“模型构造与执行”这一段。

## 9.1 本切片验证（2026-11-01）

| 测试 | 结果 |
| --- | --- |
| v2 core（state/engine/registry/resolver/adapters） | 38 项通过 |
| v2 execution entry（`execute_plan`/write checks/adapter `provides`） | 9 项通过 |
| v2 isotropic native（parity/rates/masks/3D/identity） | 14 项通过 |
| v2 voxel probe（adapter prepare/commit/guard/determinism） | 11 项通过 |
| v1 contract + voxel + proof plugin 回归 | 38 项通过 |
| 合计定向测试 | **110 项通过** |
| `scripts/check-style.sh`（compileall/ruff/frontend typecheck/diff） | 通过 |

命令（隔离数据目录，未启动服务）：

```bash
VIENNAPS_WORKBENCH_DATA_DIR=$PWD/var/test-v2-final WORKBENCH_PORT=8767 \
  .venv/bin/python -m unittest \
  tests.test_process_models_contract tests.test_voxel_adapter \
  tests.test_process_models_voxel_plugin tests.test_process_models_v2_core \
  tests.test_process_models_v2_execution tests.test_process_models_v2_isotropic \
  tests.test_process_models_v2_voxel
```

这些测试覆盖的是 v2 契约、isotropic 原生路径和 voxel proof，不代表 backend/worker
已经接入，也不代表 16 个工艺都已迁移。

## 10. 非目标与开放决策

### 10.1 非目标（本切片）

- 不接入 backend/worker，不改项目 schema，不改请求哈希；
- 不做 warm pool、引擎复用池、并发调度；
- 不实现外部引擎（`external` 只是枚举值）；
- 不做不可信插件隔离（没有 netns/seccomp/独立 host）；
- 不做插件发现、动态导入、运行时安装；
- 不实现字段/cell-set 的数值路径；
- 不声称任何 surrogate 的物理精度。

### 10.2 开放决策

| 问题 | 现状 | 需要决定 |
| --- | --- | --- |
| 插件粒度 | `sf6o2` 是否独立于 `cf4o2`；isotropic 的 levelSet/geometric 是一个变体还是两个 | 按化学拆还是按家族 + 化学参数 |
| 自动引擎选择 | 只支持显式 `variant_id` + 严格 requirement | 是否允许按 backend 自动挑；若允许，选择原因必须进 plan identity |
| 确定性容差 | `determinism` 只有 `strict`/`tolerance` 两个标签，没有统一度量 | `tolerance` 的度量与阈值如何按 `gridDelta` 定义 |
| 版本保留 | 注册表是内存字典，进程退出即丢 | 旧版本留多久；删除后旧 replay 是拒绝还是只读归档 |
| 插件依赖隔离 | 变体与 worker 共用同一 `.venv` | 是否需要 per-plugin venv / 子进程边界 |
| warm pool | `StaticEngine.release()` 是 no-op | 何时引入池化，池化后 fingerprint 如何保持稳定 |
| `mutates_input` 的 checkpoint | 只文档化，不强制 | 由 executor 统一 checkpoint，还是要求原生变体自证可重入 |

## 11. 在 v2 里新增一个 process 的清单

1. **定义参数模型**：Pydantic v2，`extra="forbid"`，范围用 `Field(...)` 表达；
   不要在 `validate()` 里重复 schema 已保证的约束（只补语义约束）。
2. **定义 `ProcessSpec`**：`id`、`version`、`label`、`params_model`、`dimensions`、
   `operations`。版本只随 schema/语义变。
3. **实现 `EngineVariant`**：`variant_id`、`version`、`capabilities`、
   `validate(ctx, params) -> list[Issue]`、`execute(state, params, ctx) -> StateDelta`。
4. **声明 `EngineRequirement`**：`kind`、`engine_id`、`min_version`，需要 GPU 时写
   `backend="gpu"`。不要写“任意版本”。
5. **写清 `VariantCapabilities`**：`reads`/`writes` 必须与 `execute` 实际访问一致；
   原生就地修改必须 `mutates_input=True`；几何限制写进 `geometry_constraints`。
6. **注册**：提供 `register_<name>(registry)`，内部 `register_process(spec)` +
   `register_variant(VariantRegistration(...))`。不要复用别人的 `variant_id`。
7. **一致性测试**（缺一不可）：
   - `resolve_execution` 能出 plan，`identity()` 稳定；
   - 缺 `reads` 中的状态 → 解析失败；
   - 维度不支持 → 解析失败；
   - 引擎版本/后端不符 → 解析失败且无回退；
   - 形状/材料正确性（按模型定义）；
   - 输入 `ProcessState` 在 `execute` + `commit` 后未被原地修改
     （除非显式声明 `mutates_input=True`）；
   - 2D 与 3D 各一个 smoke。

最小骨架：

```python
class MyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rate: float = Field(gt=0)

class MyVariant:
    variant_id = "native"
    version = "1.0.0"
    capabilities = VariantCapabilities(
        dimensions=(2, 3),
        reads=frozenset({"geometry"}),
        writes=frozenset({"geometry"}),
        mutates_input=False,
        determinism="strict",
    )

    def validate(self, ctx, params): return []
    def execute(self, state, params, ctx):
        return StateDelta(metadata={"rate": params.rate})

def register_my_process(registry: ProcessRegistry) -> None:
    spec = ProcessSpec(id="my_process", version="1.0.0", label="My process",
                       params_model=MyParams, dimensions=(2, 3), operations=("etch",))
    registry.register_process(spec)
    registry.register_variant(VariantRegistration(
        process_id=spec.id, variant_id="native", version="1.0.0",
        variant=MyVariant(), capabilities=MyVariant.capabilities,
        engine=EngineRequirement(kind="native", engine_id="viennaps", min_version="4.6.2"),
        implementation_hash=compute_implementation_hash(MyVariant()),
    ))
```

## 附录：v2 文件清单

| 文件 | 内容 |
| --- | --- |
| `process_models/v2/__init__.py` | 公开导出 |
| `process_models/v2/state.py` | `StateKind`、`MaterialState`、`LevelSetState`、`FieldState`、`VoxelState`、`ProcessState`、`StateDelta`、`Artifact`、`StateMissingError` |
| `process_models/v2/engine.py` | `EngineKind`、`Backend`、`EngineRequirement`、`EngineCapabilities`、`Engine`、`EngineLease`、`EngineProvider`、`StaticEngine`、`StaticEngineProvider`、`EngineUnavailableError` |
| `process_models/v2/contract.py` | `Issue`、`ProcessContext`、`ModelDescriptor`、`VariantDescriptor`、`VariantCapabilities`、`ProcessSpec`、`EngineVariant`、`VariantRegistration` |
| `process_models/v2/registry.py` | `ProcessRegistry`、`ResolvedVariant`、`compute_implementation_hash()` |
| `process_models/v2/resolver.py` | `ExecutionRequest`、`ExecutionPlan`、`ExecutionOutcome`、`ExecutionResolutionError`、`resolve_execution()`、`execute_plan()` |
| `process_models/v2/adapters.py` | `StateAdapter`、`DefaultStateAdapter` |
| `process_models/v2/native/isotropic.py` | 第一个真实原生插件 |
| `process_models/v2/surrogate/voxel_probe.py` | 第一个 surrogate（proof） |

v1 的 `process_models/voxel.py`、`contract.py`、`registry.py` 保持原样；v2 只**复用**
`voxel.VoxelGrid` / `read_voxels` / `write_voxels`，不 import v1 的 `ProcessPlugin`。
