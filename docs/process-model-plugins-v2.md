# 工艺模型插件 v2.2：设计/实现说明（结构化输出）

> 范围：`process_models/v2/`。v1（`process_models/contract.py`、`registry.py`、
> `voxel.py`、`surrogate/voxel_probe.py`）是**冻结的 legacy**，不再演进也**不改动**；
> v2 包本身不依赖 backend/worker，不启动服务，不改项目 schema。worker 侧的接入只以
> **默认关闭的影子校验**形式存在，见 §7.5（由集成切片加入，不改变生产执行路径）；
> bridge 的 registry 由 manifest + `plugins.lock` 构建（§2.2），不再在代码里点名注册。
>
> 本文描述 **v2.2** 的契约与边界。v2.2 只做一件事：把 **flow state**（下一步要消费的
> 状态）与 **report outputs**（人可以看、可以比较、可以落盘的报告数据）从同一个
> `StateDelta` 里拆开，见 §0.3。v2.1 的分层、引擎、适配器、replay 身份与插件生命周期
> 全部保留。文中标注 “v2.1” 的段落是 v2.2 之前的历史说明；与 v2.2 不冲突的部分仍是
> 当前基线。
>
> 凡是 v2 包内没有对应模块/生产者的能力，文中一律标为“未实现”，不把设计意图写成
> 已完成状态。v2.2 这一片交付的是**契约 + 测试**，不是新的工艺模型。
>
> “插件作为安装单元”（manifest / 发现 / `plugins.lock`）单独写在
> [`plugin-lifecycle.md`](plugin-lifecycle.md)，本文只在 §2.2 与 §10 给出边界。
>
> v2.1 是 v2 的**收缩**而非扩张：字段更少、依赖更显式、样板代码更少，执行语义不变。
> v2.2 在这条线上继续加**类型**，而不是加执行路径：`StepResult` 取代裸 `StateDelta`
> 作为 `execute()` 的返回值，但没有新增 field/cell-set 生产者、没有新工艺、
> 没有 API/UI。

## 0. 一页结论

v1 把四件事塞进了同一层：进程身份、实现注册、原生引擎句柄、单表示状态。结果是
契约里到处是 `ps`/`domain`，任何一步都必须返回一个原生 domain，实现哈希只覆盖类源码，
加一个变体就要顶一个 plugin 版本。v2 的修法不是“再加字段”，而是**拆层并显式化**：

| 关注点 | v1 | v2 |
| --- | --- | --- |
| 身份 | `ProcessPlugin`（schema + 变体表 + 版本） | `ProcessSpec`（只有 schema/语义） + `VariantRegistration`（只有实现） |
| 引擎 | 隐含在 `ProcessState.ps` | `Engine` / `EngineLease` / `EngineRequirement`，显式租约与指纹 |
| 状态 | `ProcessState.ps/domain` + `ProcessResult.domain` | `ProcessState{geometry, materials, layers, fields, voxels}` + `StateDelta`（只有 flow state） |
| 报告输出 | 混在 `ProcessResult.metadata/outputs` | `StepResult.outputs: OutputSet`（`metrology` + `artifacts`），与 flow state 分离 |
| 表示转换 | 变体自己调 `read_voxels`/`write_voxels` | `StateAdapter.prepare/commit`，变体只看见自己声明的表示 |
| 解析 | `registry.resolve()` 只查表 | `resolve_execution()` 做参数/能力/引擎/适配器全链路准入并产出 `ExecutionPlan` |
| 实现哈希 | `inspect.getsource(type(variant))` | 变体模块的**全部 .py 文件字节** + 类 qualname |

### 0.1 v2.1 相对 v2 的六项简化

v2 的第一版能跑，但把同一事实写在了两三个地方（request 既在 plan 外又在 plan 内、
variant 的 id/版本/能力既在变体上又在 registration 里、engine 既在 lease 里又塞进
state）。v2.1 只做减法：

| # | v2 | v2.1 | 去掉了什么重复/歧义 |
| --- | --- | --- | --- |
| 1 | `execute_plan(plan, state, params, context)` | `execute_plan(plan)`；plan 自带 `request`；另有 `run_step(...)` | 执行参数只有一个来源，不会出现“plan 是 A、传入 state 是 B” |
| 2 | `ProcessState.engine` 保存 lease | `EngineVariant.execute(state, params, ctx, engine)` 显式传入 | 状态里不再藏运行时句柄；`ProcessState` 因此可 `frozen` |
| 3 | `VariantRegistration(process_id, variant_id, version, variant, capabilities, engine, implementation_hash)` | `VariantRegistration(process_id, variant)`，其余由 variant 派生 | 变体身份只声明一次，不会 registration 与 variant 各写一份还对不上 |
| 4 | `ProcessContext.materials`、`ProcessState.meta`、`ProcessSpec.describe()` | 全部删除；材料只在 `state.materials`，descriptor 由 registry 生成 | 上下文与状态各司其职；描述信息不再有“spec 方法 vs registry 拼装”两条路 |
| 5 | `ExecutionPlan` 只有执行所需字段 | `ExecutionPlan.request` + `plan.run()` | 计划与它要执行的请求绑定，admission 结果不会被换掉的输入绕过 |
| 6 | 注册/执行样板长 | 注册只需 `(process_id, variant)`；一步执行用 `run_step` | 新增一个 process 的代码量明显下降 |

为什么这六项值得做：

- **删除重复事实**：一个值只存一处，比较、哈希、replay 都只有一条路径。
- **把准入绑定到执行**：`resolve_execution()` 校验的就是 `plan.request`，`execute_plan(plan)`
  只读它；手工改 plan 的字段不会让未经准入的请求混进来。
- **让引擎依赖显式**：变体签名里就有 `engine`，不需要去 state 里翻；surrogate 也必须
  显式接受（即使不用），避免“有的变体偷偷依赖全局状态”。
- **减少样板**：变体已经声明了 id/version/capabilities/engine/hash 来源，registration
  再抄一遍没有信息增量，只增加写错的机会。

### 0.2 快速上手（最小 process 插件）

下面是一个可注册、可解析、可执行的最小插件。实际原生实现里 `execute` 会用
`engine.handle` 拿 ViennaPS 句柄；这里只用 `StepResult` / `StateDelta` 说明形状。
注意 `execute` 返回的是 `StepResult`，不是裸 `StateDelta`：flow state 放进
`state=`，报告数据放进 `outputs=`。

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from process_models.v2 import (
    EngineRequirement,
    ProcessContext,
    ProcessRegistry,
    ProcessSpec,
    ProcessState,
    StateDelta,
    StepResult,
    VariantCapabilities,
    VariantRegistration,
    run_step,
)

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
    engine = EngineRequirement(kind="native", engine_id="viennaps", min_version="4.6.2")
    implementation_hash = None  # None = 由 registry 按模块字节计算

    def validate(self, ctx: ProcessContext, params: MyParams) -> list:
        return []

    def execute(self, state: ProcessState, params: MyParams, ctx: ProcessContext, engine):
        # engine 是本步租约；原生插件用 engine.handle，surrogate 可以不用。
        # state= 只放下一步要消费的 flow state；outputs= 只放报告数据。
        return StepResult(
            state=StateDelta(),
            metadata={"rate": params.rate},
        )

def register_my_process(registry: ProcessRegistry) -> None:
    spec = ProcessSpec(
        id="my_process",
        version="1.0.0",
        label="My process",
        params_model=MyParams,
        dimensions=(2, 3),
        operations=("etch",),
    )
    registry.register_process(spec)
    # id/version/capabilities/engine/hash 都从 variant 派生，不重复传。
    registry.register_variant(VariantRegistration(process_id=spec.id, variant=MyVariant()))

# 一步完成 resolve + execute；outcome.plan.identity() 可直接写进 run 记录。
outcome = run_step(
    registry,
    provider,
    process_id="my_process",
    variant_id="native",
    params=MyParams(rate=1.0),
    context=ctx,
    state=state,
)
```

需要分两步时用 `plan = resolve_execution(request, registry, provider)`，再
`outcome = plan.run()`（等价于 `execute_plan(plan)`）。

### 0.3 v2.2：flow state 与 report outputs 分离（本切片）

v2.1 用一个 `StateDelta` 同时表达两种完全不同的东西：

- **flow state**：下一步要消费的表示（`geometry` / `layers` / `fields` / `voxels`），会被
  adapter commit 进 `ProcessState`，跨步骤存活；
- **report data**：`artifacts` / `metadata` / `warnings`，只给本步的报告、比较、日志和
  落盘用，下一步从不读取。

混在一起带来的是隐式契约：`metadata` 是无类型的 `dict`，计量值、实际速率、solver
配置、文件路径、甚至大数组都能塞进去；没人校验、没人知道键的含义，report/UI 只能
按约定猜。`is_empty()` 也因此变成“状态变了没 + 有没有报告”的混合判断。

v2.2 的拆分：

| 返回面 | 类型 | 谁消费 | 生命周期 | 不应放什么 |
| --- | --- | --- | --- | --- |
| `StepResult.state` | `StateDelta` | adapter commit → 下一步的 `ProcessState` | 跨步骤 | 计量值、文件路径、日志 |
| `StepResult.outputs` | `OutputSet` | executor / 报告 / UI / replay 比较；**不回灌下一步执行** | 本步报告 | flow state、大数组本体 |
| `StepResult.metadata` | `dict[str, Any]` | 本步少量 bookkeeping（实际速率、solver 配置、跳过原因码） | 本步 | 计量、警告、产物、大对象 |
| `StepResult.warnings` | `list[str]` | 人读；不改状态、不进 identity | 本步 | 结构化数据、错误码 |

一条判据足够：**下一步需要它，它就是 state；只是给人看/比较/落盘，它就是 output；
两者都不是，才考虑 metadata。metadata 不是兜底通道。**

`metadata` 不当垃圾桶的具体理由：

1. 没有 schema，不能校验、不能查询、不能跨版本比较；
2. 不参与 `ExecutionPlan.identity()`，却容易被误当成 replay 依据；
3. executor、report、UI 各按自己的约定读同名键，等于把契约藏在字符串里；
4. 数组/句柄走 JSON 边界会膨胀、丢精度或直接不可序列化。

所以：一个带单位的数 → `MetrologyRecord`；一个文件 → `Artifact`；下一步要用的体数据
→ `Field` / `CellSetState`。

#### 0.3.1 `StateDelta` 与 `StepResult`

```python
@dataclass
class StateDelta:
    geometry: LevelSetState | None = None
    layers: LayerState | None = None
    fields: CellSetState | None = None
    voxels: VoxelState | None = None
    # 没有 artifacts / metadata / warnings：delta 只描述 flow state
    def is_empty(self) -> bool: ...   # 只看 geometry/layers/fields/voxels

@dataclass
class StepResult:
    state: StateDelta                 # 只有 flow state
    outputs: OutputSet = OutputSet()  # 报告输出，默认空
    metadata: dict[str, Any] = {}     # 少量 bookkeeping
    warnings: list[str] = []          # 本步警告

@dataclass(frozen=True)
class OutputSet:
    schema_version: int = 1
    metrology: tuple[MetrologyRecord, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
```

`None` 仍然表示“没变”，不是“清空”。`StepResult.state` 为空是合法的：一个只产出
计量或只做校验的步骤可以返回空的 `StateDelta` 和非空 `OutputSet`。

#### 0.3.2 新增 state 类型：`GridSpec` / `Field` / `CellSetState` / `Layer` / `LayerState`

v2.1 的 `FieldState.data: Mapping[str, Any]` 只是一个口袋；v2.2 用带网格、单位、关联
位置和形状的类型替换它。**`FieldState` 被 `CellSetState` 取代，不保留别名**；旧的
import 必须改成新类型。

```python
StateKind = Literal["geometry", "materials", "layers", "fields", "voxels"]
FieldAssociation = Literal["cell", "point"]

@dataclass(frozen=True)
class GridSpec:
    dimension: int                  # 2 或 3
    origin: tuple[float, ...]       # 每个维度一个
    spacing: float                  # 有限且 > 0
    shape: tuple[int, ...]          # 每个维度一个，均为正

@dataclass(frozen=True)
class Field:
    name: str                       # 非空
    unit: str                       # 非空
    association: FieldAssociation   # "cell" | "point"
    dtype: str                      # 例如 "float64"
    shape: tuple[int, ...]          # 网格形状，不含分量维
    components: int = 1             # 1=标量, 3=向量, 6/9=张量
    data: Any = None                # numpy 数组或不透明句柄

@dataclass(frozen=True)
class CellSetState:
    grid: GridSpec
    material_ids: Any               # 数组/句柄，shape == grid.shape
    fields: Mapping[str, Field] = {}
    provenance: Mapping[str, Any] = {}

@dataclass(frozen=True)
class Layer:
    layer_id: str                   # 非空，LayerState 内唯一
    material_id: int                # >= 0
    name: str                       # 非空
    source_step: str | None = None
    thickness: float | None = None  # 存在时有限且 > 0
    metadata: Mapping[str, Any] = {}

@dataclass(frozen=True)
class LayerState:
    layers: tuple[Layer, ...]
```

| 类型 | 校验点 |
| --- | --- |
| `GridSpec` | `dimension ∈ {2, 3}`；`origin`/`shape` 长度等于 `dimension`；`spacing` 有限且 > 0；`shape` 每项为正 |
| `Field` | `name`/`unit` 非空；`association` 只能是 `cell`/`point`；`components` 的约定是 1=标量 / 3=向量 / 6 或 9=张量；`data` 是 numpy 时，`dtype` 与 `shape` 必须匹配：`components == 1` 时 `data.shape == shape`，否则 `data.shape == (components, *shape)`；不透明句柄按声明信任，`shape`/`dtype` 就是契约 |
| `CellSetState` | `fields` 的键等于 `Field.name`；所有 field 必须是 `cell` 关联；`Field.shape == grid.shape`；`material_ids` 是 numpy 时校验 shape/dtype |
| `Layer` | `layer_id` 非空且在同一 `LayerState` 内唯一；`material_id >= 0`；`name` 非空；`thickness` 存在时有限且 > 0 |
| `LayerState` | 只要求 `layer_id` 唯一。**层序（top-to-bottom / bottom-to-top）暂不由契约规定**，由 producer 在文档中声明 |

为什么 FinFET 需要这些类型：

- **implant / anneal / diffusion / stress 不是表面量。** level-set 几何只描述界面；
  掺杂浓度、损伤、应力张量是体/节点上的量。没有 `CellSetState` + `Field`，这些结果
  只能塞进 `metadata`，下一步和器件仿真都无法消费。
- **张量不是一个数。** 应力是 6 或 9 分量，掺杂/损伤是标量，位移/通量是向量；
  `components` 把它们区分开，`dtype`/`shape`/`unit` 让下游知道数值的含义。
- **cell 与 point 关联不可互换。** 有限体积量通常在 cell 上，有限元/插值量在节点上；
  写错关联位置不会报错，只会静默算错。
- **dummy gate / ILD / spacer 的身份不等于材料 ID。** 两次 SiO2 沉积可以共享同一
  material ID（v2.1 的 `MaterialState` 已允许重复 ID）。只看 material 10 无法区分
  dummy gate、ILD 和 spacer；`Layer.layer_id` + `name` + `source_step` 才是身份，
  `thickness` 可用来和 metrology 对账。
- **layer 是 flow state，不是报告。** 下一步（CMP、etch、deposition）要按层身份选择
  行为，所以它进 `StateDelta.layers`，而不是 `OutputSet`。

#### 0.3.3 输出类型：`MetrologyRecord` / `Artifact` / `OutputSet`

```python
ArtifactKind = Literal[
    "surface_mesh", "cell_set", "vtu", "json", "text", "native",
    "table", "profile", "device", "image",
]

@dataclass(frozen=True)
class MetrologyRecord:
    metric: str                     # 非空
    value: float                    # 有限
    unit: str                       # 非空
    region: str | None = None
    method: str = "plugin"          # 非空
    uncertainty: float | None = None  # 存在时有限且 >= 0

@dataclass(frozen=True)
class Artifact:
    kind: ArtifactKind
    name: str                       # 非空
    path: str | None = None         # None = 仅内存描述，由 executor 决定是否落盘
    schema_version: int = 1         # >= 1
    metadata: Mapping[str, Any] = {}

@dataclass(frozen=True)
class OutputSet:
    schema_version: int = 1         # >= 1
    metrology: tuple[MetrologyRecord, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
```

- `MetrologyRecord` 是**一个带单位的标量报告值**（CD、结深、薄膜厚度、方阻……）。
  `region` 限定测点/区域，`method` 记录来源（默认 `"plugin"`），`uncertainty` 可选。
- `Artifact` 是**一个报告产物**。v2.2 把 kind 扩到 `table` / `profile` / `device` /
  `image`，并给每个产物加 `schema_version`；`path=None` 表示“先描述、后落盘”。
- `OutputSet` 默认全空，`schema_version` 标记这一组输出的形状版本。

**typed 与 generic 的分工**：

| 情况 | 用什么 |
| --- | --- |
| 一个可比较、可查询的标量报告值 | `MetrologyRecord`，不要塞 `metadata` |
| 已知类别的文件（网格、VTU、表格、profile、器件曲线、图片） | 对应的 `Artifact(kind=...)` + `path` |
| 契约还没建模的 payload | escape hatch：`Artifact(kind="json" / "text" / "native")` + `Artifact.metadata` + `schema_version` |
| 下一步要消费的数组/网格数据 | 不是 output：进 `CellSetState` / `Field`（若 producer 存在） |

escape hatch 只解决“先能表达”，不解决“能比较”：它的内容仍然是 blob，读取方必须
自己解释 `schema_version`。因此**不要**把本可以作为 `MetrologyRecord` 的数放进
artifact metadata，也**不要**把数组放进 `StepResult.metadata`。

#### 0.3.4 契约形状的改动

```python
@dataclass(frozen=True)
class VariantCapabilities:
    dimensions: tuple[int, ...]
    reads: frozenset[StateKind]
    writes: frozenset[StateKind]
    geometry_constraints: tuple[str, ...] = ()
    supports_fields: bool = False
    supports_masks: bool = False
    experimental: bool = False
    mutates_input: bool = False
    determinism: Literal["strict", "tolerance"] = "strict"
    output_kinds: tuple[str, ...] = ()   # 例如 ("metrology", "artifact:profile")

class EngineVariant(Protocol):
    ...
    def execute(self, state, params, ctx, engine) -> StepResult: ...

@dataclass(frozen=True)
class ExecutionOutcome:
    plan: ExecutionPlan
    state: ProcessState          # commit 之后的 flow state
    result: StepResult           # execute 的原始返回

    @property
    def delta(self) -> StateDelta: return self.result.state
    @property
    def metadata(self) -> dict[str, Any]: return self.result.metadata
    @property
    def warnings(self) -> list[str]: return self.result.warnings
    @property
    def outputs(self) -> OutputSet: return self.result.outputs
    @property
    def artifacts(self) -> list[Artifact]: return list(self.result.outputs.artifacts)
```

- `execute()` 必须返回 `StepResult`；返回裸 `StateDelta` 是错误（`execute_plan` 会报
  `ExecutionResolutionError`）。
- `output_kinds` 是**声明式**的：registry 把它抄进 `VariantDescriptor`，executor 记录
  它，但本切片不强制“声明了就必须产出”。它让 UI/报告知道该期待什么，而不是新增
  一条准入规则。
- `ExecutionOutcome` 的 `delta` 现在是 `result.state` 的别名；调用方原来读
  `outcome.delta.metadata` 的地方必须改成 `outcome.metadata` / `outcome.outputs`。

`DefaultStateAdapter` 对 `layers` / `fields` 的规则：

| 阶段 | `geometry` | `voxels` | `layers` | `fields` |
| --- | --- | --- | --- | --- |
| `prepare` | 原样传递 | 从 geometry 派生（`provides={"voxels"}`） | **只接受已存在的表示**，不派生 | **只接受已存在的表示**，不派生 |
| `commit` | 写入，并清掉过期 voxel 视图 | 回写成新 geometry，清空 voxel 视图 | 独立写入 | 独立写入 |

- `prepare` 时变体声明要读 `layers`/`fields`、但 state 里没有：直接
  `StateMissingError`，不会凭空构造空层表或空 cell-set。
- `commit` 对四种可写表示分别做 write check（未在 `writes` 中声明就报错）；
  `delta.geometry` 优先于 `delta.voxels`；`delta.layers` 与 `delta.fields` 各自独立
  应用，互不覆盖；输入 `ProcessState` 永远不被原地修改。
- resolver 的 `_state_provides` 把 `layers`/`fields` 视为 **stored-only**：只有
  state 里已有才算满足，adapter 的 `provides` 只包含 `"voxels"`。

#### 0.3.5 FinFET 输出到 v2.2 类型的映射

| 输出 | 典型内容 | v2.2 落点 |
| --- | --- | --- |
| 表面形貌 | 表面网格、final geometry | flow state 是 `StateDelta.geometry`；网格文件额外报成 `Artifact(kind="surface_mesh")` |
| 材料 / 层 | dummy gate、spacer、ILD 的身份与厚度 | `StateDelta.layers`（`LayerState`）；层清单表可另报 `Artifact(kind="table")` |
| 掺杂 / 损伤 | 浓度、damage 标量场 | `CellSetState` + `Field(components=1, association="cell")` |
| 应力 | 6/9 分量应力张量 | `CellSetState` + `Field(components=6 或 9)` |
| 计量 | CD、结深、薄膜厚度、方阻 | `OutputSet.metrology`（`MetrologyRecord`） |
| 器件 | Id-Vg、Vt、SS、DIBL 曲线 | `Artifact(kind="device")`；单点指标可再补 `MetrologyRecord` |
| 剖面 | SIMS / depth profile | `Artifact(kind="profile")` |

这张表是**契约目标**，不是当前能力清单：本切片里没有任何插件会产出上表的
`MetrologyRecord`、`Artifact`、`LayerState` 或 `CellSetState`（见下一节）。

#### 0.3.6 本切片没有实现

v2.2 交付的是类型、校验和测试；下面这些明确**不在**本切片：

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| field / cell-set **producer** | 未实现 | 没有插件计算掺杂、损伤、应力或任何 `Field`；类型只被构造和校验 |
| 原生 cell-set adapter | 未实现 | `DefaultStateAdapter` 对 `fields` 只做“已有则透传/写入”，不做 level-set ↔ cell-set 转换 |
| stress / device simulator | 未实现 | 没有力学求解器，也没有器件 I-V 仿真；`Artifact(kind="device")` 只是报告类型 |
| UI schema / API | 未实现 | 没有 JSON schema、HTTP API 或前端字段；项目仍不能引用 v2.2 输出 |
| field 持久化 / replay | 未实现 | `LayerState`/`CellSetState` 不进快照、不进 native state；`ExecutionPlan.identity()` 的九项不变 |
| 插件产出计量/产物 | 未实现 | `isotropic` 与 `voxel_probe` 移植后返回**空 `OutputSet`**（只有 `metadata`/`warnings`），不声称已有 metrology/artifact |
| conformance harness | 未实现 | 没有跨插件的一致性套件 |

因此“v2.2 完成”只意味着：`execute → StepResult`、`OutputSet`、`LayerState`、
`CellSetState`、`MetrologyRecord`、`Artifact(schema_version)` 的契约与校验成立，
isotropic/voxel 两个既有插件随之迁移；新增测试的结果单独报告，本文不预先写数字。

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
| **Runtime** | 无（executor 边界） | 跨步骤持有 flow state、checkpoint/rollback、run 目录、快照、事件、种子、资源上限、超时 | **未实现**。只定义 `ProcessState` / `StateDelta` / `StepResult` / `OutputSet` / `Artifact` 作为 runtime 将来对接的面 |
| **Engine** | `engine.py` | 描述引擎能力、按 `EngineRequirement` 严格租用、给出 `engine_version` + `fingerprint` | 实现 `StaticEngine` / `StaticEngineProvider`（包装已初始化的句柄）；**没有**外部引擎、没有 warm pool、没有调度 |
| **Service** | `registry.py` + `resolver.py` | 目录（多版本 spec/variant）、准入检查、解析出 `ExecutionPlan` | 内存注册表 + 纯函数解析器；插件来源由 manifest + `plugins.lock` 固定（§2.2）；**没有**持久化服务端状态、没有 HTTP API |
| **State** | `state.py` + `adapters.py` | 多表示状态、`StateDelta`（只有 flow state）、`OutputSet`、`prepare/commit` 转换 | 五种表示类型（`geometry`/`materials`/`layers`/`fields`/`voxels`）+ `DefaultStateAdapter`；`layers`/`fields` 有类型但**只接受已存表示**，没有 producer |
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
| worker/backend 接入 | 未实现 | 生产执行路径不变；worker 侧只有默认关闭的影子校验（§7.5），以及按 lock 构建 registry 的 bridge（§2.2） |
| warm pool / 引擎复用池 | 未实现 | `StaticEngine` 只包装已存在的句柄，`release()` 是 no-op |
| 外部引擎（Sentaurus 等） | 未实现 | `EngineKind` 有 `"external"`，但没有 provider |
| 字段 / cell-set 表示 | 类型已定义，路径未实现 | `CellSetState`/`Field` 有校验，但**没有 producer**；adapter 对 `fields` 只做 stored-only，不做转换 |
| 层表示 | 类型已定义，路径未实现 | `LayerState`/`Layer` 有校验，但当前没有插件写入；`commit` 已支持独立写 `layers` |
| 计量 / 产物输出 | 类型已定义，未产出 | `OutputSet`/`MetrologyRecord`/`Artifact` 存在；既有插件移植后返回空 `OutputSet` |
| 插件发现/动态导入 | 目标切片 | 见 §2.2：manifest + lock 固定来源，`load_registry()` 替代显式注册；不做任意模块导入、不做运行时安装 |
| 资源上限、超时、沙箱 | 未实现 | 属于 Runtime |
| 自动引擎/变体选择 | 未实现 | 显式 `variant_id`，无降级 |

### 2.2 插件安装单元（manifest / lock）

v2.1 的注册调用（`register_<name>(registry)`）回答“怎么装进 registry”，
插件生命周期回答“装的是哪一份代码、能不能被信任”。两者是**不同层**，后者不改变
本文任何契约类型：

| 关注点 | 载体 | 约束 |
| --- | --- | --- |
| 身份与入口 | `plugins/<id>/plugin.toml` | 严格字段：`api`/`id`/`version`/`entrypoint`/`description`；未知键拒绝 |
| 发现 | `VIENNAPS_WORKBENCH_PLUGIN_DIRS`，缺省 `<repo>/plugins` | `dir/*/plugin.toml`，不递归；运维安装的信任模型 |
| 安装事实 | `plugins.lock` | pin id/version/api/entrypoint/manifest 字节哈希/每个变体的实现哈希 |
| 加载 | `process_models.plugins.load_registry()` | 缺失、多出、字节漂移、注册越界一律 `PluginLifecycleError`，无回退 |

三条与本文直接相关的结论：

- **项目 JSON 仍然只写 `process_id`/`variant_id`/参数**，永远不含 module path 或
  callable；插件选择属于部署面，不属于请求面。
- 插件层固定的是“已安装版本集合”，replay 需要的版本没装就是硬错误，不会回退到
  邻近或最新版本——与 §5 的“没有静默回退”同一条规则。
- 生命周期只做 manifest/发现/lock；**不做** conformance harness、warm pool、API/UI、
  市场、沙箱、自动引擎选择，见 §10.1。

完整字段、哈希覆盖范围、lock 生成/校验命令与运维清单见
[`plugin-lifecycle.md`](plugin-lifecycle.md)。该切片的精确测试结果单独报告，本文
不预先写入数字。

v2.2 把 `isotropic` 的 `execute` 改成返回 `StepResult`，插件实现的代码字节因此变化，
`plugins.lock` 必须用 `scripts/lock-plugins.py` 重新生成并用 `--check` 校验；lock 里
记录的是身份与哈希事实，本文不写死任何 digest。`voxel_probe` 是树内 proof 插件、
不在 lock 中，但同样要迁移到 `StepResult`。

## 3. 契约类型

### 3.1 身份：spec 与 registration 分开

| 类型 | 文件 | 回答的问题 | 关键字段 |
| --- | --- | --- | --- |
| `ProcessSpec` | `contract.py` | 这是什么工艺、参数长什么样 | `id`、`version`、`label`、`params_model`、`dimensions`、`operations`、`description` |
| `VariantRegistration` | `contract.py` | 用哪个实现、归属于哪个 process | `process_id`、`variant`；`variant_id`/`version`/`capabilities`/`engine`/`declared_hash` 由 variant 派生 |
| `EngineVariant` | `contract.py` | 怎么校验、怎么执行 | `variant_id`、`version`、`capabilities`、`engine`、`implementation_hash`、`validate()`、`execute(state, params, ctx, engine) -> StepResult` |
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

@dataclass(frozen=True)
class VariantRegistration:
    process_id: str
    variant: EngineVariant

    @property
    def variant_id(self) -> str: return self.variant.variant_id
    @property
    def version(self) -> str: return self.variant.version
    @property
    def capabilities(self) -> VariantCapabilities: return self.variant.capabilities
    @property
    def engine(self) -> EngineRequirement: return self.variant.engine
    @property
    def declared_hash(self) -> str | None: return self.variant.implementation_hash
```

`ProcessSpec.version` 只随**参数 schema 或公开语义**变；新增变体不动它。变体版本在
`EngineVariant.version` 上，所以“同 schema、两个实现版本”是合法且可区分的状态。
`ProcessSpec` **没有** `describe()`：`registry.list_processes()` 用 spec 和它已注册的
变体现场拼 `ModelDescriptor`，不再让 spec 自己描述自己。

`VariantRegistration.__post_init__` 只做结构校验：`process_id` 非空；variant 的
`variant_id`/`version` 非空；`capabilities` 是 `VariantCapabilities`；`engine` 是
`EngineRequirement`；`implementation_hash` 是 `str | None`。

### 3.2 上下文与能力

```python
@dataclass(frozen=True)
class ProcessContext:
    dimension: int                 # 2 或 3
    grid_delta: float              # > 0
    project: Mapping[str, Any]     # 只读原始 JSON
    step: Mapping[str, Any]
    # 没有 materials：材料只存在于 ProcessState.materials

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
    output_kinds: tuple[str, ...] = ()           # ("metrology", "artifact:profile")
```

`reads`/`writes` 是**准入依据**，不是注释：`resolve_execution()` 会检查 `reads` 里
每一种状态是否存在，`DefaultStateAdapter.commit()` 会检查 `writes` 是否允许这种写入。
`output_kinds` 则是**声明式**的：registry 把它抄进 `VariantDescriptor`，executor
记录它，本切片不据此做准入或强制产出。

### 3.3 状态、增量与步骤结果

| 类型 | 表示 | 说明 |
| --- | --- | --- |
| `MaterialState` | `order: tuple[int, ...]` + `names: Mapping[int, str]` | 原生材料 ID 与显示名；`order` 按 level-set index，**允许同一材料 ID 重复**（例如两次 SiO2 沉积）；`name_for()` / `id_for()` 双向查 |
| `LevelSetState` | `handle` + `dimension` + `grid_delta` + `material_order` | flow state 的几何表示 |
| `Layer` / `LayerState` | `layer_id`/`material_id`/`name`/`source_step`/`thickness`；`LayerState.layers` | 层身份与厚度；`layer_id` 唯一，层序由 producer 声明。**有类型，当前无 producer** |
| `GridSpec` / `Field` / `CellSetState` | 网格 + `material_ids` + 命名 `Field`（unit/association/dtype/shape/components） | 取代 v2.1 的 `FieldState`；支持标量/向量/张量、cell/point 关联。**有类型，当前无 producer** |
| `VoxelState` | `grid`（`process_models.voxel.VoxelGrid`）+ `source` | 步骤内视图，默认 `"height-field-v1"` |
| `ProcessState` | geometry / materials / layers / fields / voxels；**frozen**，没有 `engine`/`meta` | `has(kind)` / `require(kind)`；`require` 缺失时抛 `StateMissingError` |
| `StateDelta` | `geometry` / `layers` / `fields` / `voxels` | **只有 flow state**；字段为 `None` 表示“没变”，不是“清空” |
| `StepResult` | `state` / `outputs` / `metadata` / `warnings` | `execute()` 的返回类型；报告数据在 `outputs`，`metadata` 只放少量 bookkeeping |
| `OutputSet` | `metrology` / `artifacts` + `schema_version` | 报告输出；默认空 |

```python
@dataclass
class StateDelta:
    geometry: LevelSetState | None = None   # None = unchanged
    layers: LayerState | None = None
    fields: CellSetState | None = None
    voxels: VoxelState | None = None
    # 没有 artifacts / metadata / warnings

    def is_empty(self) -> bool: ...   # 只看 geometry/layers/fields/voxels

@dataclass
class StepResult:
    state: StateDelta
    outputs: OutputSet = OutputSet()
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
```

`FieldState` 已被 `CellSetState` 取代且**没有别名**，旧 import 必须改。完整字段与校验
规则见 §0.3.2，报告类型见 §0.3.3。

关键语义变化：**变体只声明变化，不负责合并**。合并、写回、清空临时视图由 adapter 完成。
`ProcessState` 是 frozen dataclass，adapter 与调用方用 `dataclasses.replace()` 产生新对象；
引擎 lease 不再放进状态，而是作为 `execute(state, params, ctx, engine)` 的显式参数。

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
    request: ExecutionRequest        # plan 拥有它要执行的完整请求
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
    def run(self) -> ExecutionOutcome: ...   # == execute_plan(self)

@dataclass(frozen=True)
class ExecutionOutcome:
    plan: ExecutionPlan
    state: ProcessState          # commit 后的 flow state
    result: StepResult           # variant.execute 的原始返回
    # delta / metadata / warnings / outputs / artifacts 都是 result 的委托属性

def execute_plan(plan: ExecutionPlan) -> ExecutionOutcome: ...
def run_step(registry, provider, process_id, variant_id, params, context, state,
             *, expected_process_version=None, expected_variant_version=None,
             adapter=None) -> ExecutionOutcome: ...
```

`ExecutionOutcome` 的委托关系：`delta == result.state`、`metadata == result.metadata`、
`warnings == result.warnings`、`outputs == result.outputs`、
`artifacts == list(result.outputs.artifacts)`。`result` 是唯一的事实来源，避免
“outcome 上另存一份 metadata 然后过期”的第二种表示。

`ExecutionPlan` 是**唯一**允许被写进 run 记录的身份来源；调用方不要自己拼字段。
它同时拥有 `request`，所以“准入时校验的输入”和“执行时使用的输入”是同一个对象，
不存在把 plan 之外的 `state`/`params` 再传一遍的缝隙。

`execute_plan(plan)` 只读 `plan.request.state`、`plan.request.params`、
`plan.request.context`；`plan.run()` 就是它的方法形式。`run_step(...)` 是给常见
“解析并立即执行一步”的调用方准备的便利函数：构造 `ExecutionRequest`、调用
`resolve_execution()`、再 `execute_plan()`，不引入第三条执行路径。

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

v2.2 的 `layers`/`fields` 规则：

- `prepare`：`layers`/`fields` 是 **stored-only**，不会从 geometry 派生；state 里没有
  就直接 `StateMissingError`。只有 `voxels` 仍由 geometry 派生。
- `commit`：`geometry`、`layers`、`fields`、`voxels` 各自做 write check；
  `delta.geometry` 优先于 `delta.voxels`；`delta.layers` 与 `delta.fields` **独立**
  应用，互不覆盖；任何一条路径都不修改输入 state。

## 4. 执行流程

`resolve_execution()` 只解析；`ExecutionPlan.run()` / `execute_plan(plan)` 负责真正执行
一次 plan，并返回 `ExecutionOutcome`。未来的 runtime 只需要拿到 plan 后调用其中之一。

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
   |  (runtime) plan.run()          |                      |              |               |
   |------------------------------------------------------------------>|  |               |
   |                 |               |                      |  prepare()  |               |
   |                 |               |                      |------------>|               |
   |                 |               |                      |             | execute()     |
   |                 |               |                      |             |-------------->|
   |                 |               |                      |             |<--StepResult--|
   |                 |               |                      |  commit()   |               |
   |                 |               |                      |------------>|               |
   |<--------------------------- new ProcessState (state applied) -------|               |
   |<--------------------------- StepResult.outputs ----------------------|               |
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

`execute_plan(plan)` 随后做四件事：

1. 再次检查 `plan.request.params` 是 `plan.process.params_model` 的实例（防止手工构造的 plan）；
2. `prepared = plan.state_adapter.prepare(plan.request.state, plan.variant.capabilities)`；
3. `plan.variant.variant.execute(prepared, plan.request.params, plan.request.context, plan.engine)`，
   结果必须是 `StepResult`，否则 `ExecutionResolutionError`；
4. `plan.state_adapter.commit(prepared, result.state, capabilities)`，返回
   `ExecutionOutcome(plan, committed, result)`。

不需要再把 engine lease `replace` 进 state：lease 是 `execute` 的显式参数。adapter
只消费 `result.state`；`result.outputs` / `result.metadata` / `result.warnings` 原样
留在 `ExecutionOutcome` 上，不进 `ProcessState`。

执行阶段由 adapter 收尾：

| 阶段 | 谁做 | 做什么 |
| --- | --- | --- |
| `prepare` | adapter | 补齐变体声明要读、但当前 state 里没有的表示：`voxels` 从 geometry 派生；`layers`/`fields` 只接受已存表示 |
| `execute` | variant | 只读 `state`，只返回 `StepResult`，不写盘、不碰 adapter |
| `commit` | adapter | 把 `result.state` 合并进新 `ProcessState`；`geometry` 优先于 `voxels`；`layers`/`fields` 独立应用 |

## 5. 版本与 replay 身份

四类版本，互不替代：

| 项 | 存在哪 | 什么时候升 | 作用 |
| --- | --- | --- | --- |
| `process_version` | `ProcessSpec.version` | 参数 schema 或公开语义变 | 固定“这一步的参数含义” |
| `variant_version` | `VariantRegistration.version` | 实现行为/数值变 | 固定“这一版实现” |
| `implementation_hash` | `VariantRegistration.declared_hash`，缺省时 registry 计算 | 代码字节变（自动） | 发现“版本号没升但代码改了” |
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

`StepResult.outputs` / `metadata` 的子字段**不**进入 identity：它们不是执行身份，而是
本步结果。可复现的插件在相同 identity、相同输入 state/params 下应产出相同的
`OutputSet`；做不到就是 `determinism` 声明错了，不能靠在 metadata 里写“结果可能不同”
绕过。

版本保留策略（旧版本能留多久）是开放决策，见 §10；本切片注册表只是内存字典。

## 6. 状态表示与逐步 voxel 适配器（方案 1.a）

### 6.1 flow state 仍是 level-set

```
step N-1:  LevelSetState ──┐
                           │  prepare: read_voxels(geometry.handle)
step N:                    ├─> VoxelState (步骤内，临时)
                           │  variant.execute -> StepResult(state=StateDelta(voxels=...))
                           │  commit: write_voxels(geometry.handle, grid)
step N+1:  LevelSetState <─┘
```

- voxel 栅格**不进快照、不进 replay、不跨步骤保存**；
- 只有声明 `"voxels" in reads` 的变体才会触发转换；
- 转换由 adapter 负责，变体不 import `process_models.voxel`；
- 材料表（`MaterialState`）独立于几何存在，因为 `write_voxels` 需要原始材料顺序。

### 6.2 五种表示的分工

| 表示 | 谁产生 | 谁消费 | 本切片 |
| --- | --- | --- | --- |
| `geometry`（`LevelSetState`） | 原生变体 / adapter 回写 | 原生变体、voxel 读取 | 实现 |
| `materials`（`MaterialState`） | 调用方 / 几何元数据 | 所有写入几何的变体 | 实现 |
| `layers`（`LayerState`） | 未来 layer-aware 插件 / 调用方 | 未来 CMP/etch/deposition 插件 | 类型与 commit 写检查；**无 producer** |
| `fields`（`CellSetState`） | 未来 field/cell-set 插件 | 未来插件 | 类型与校验；adapter 只做 stored-only；**无 producer** |
| `voxels`（`VoxelState`） | adapter（从 geometry） | surrogate / voxel-native 插件 | 实现（height-field） |

注意：`layers`/`fields` 的“实现”只到**类型 + 校验 + adapter 写入**，没有任何模型会
计算它们，也没有从 level-set 到 cell-set 的转换。

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

### 6.4 未来的 field / cell-set / layer

未实现，但 v2.2 已经把类型定清楚：

- `CellSetState` = `GridSpec` + `material_ids` + 命名 `Field` + `provenance`；
  `Field` 带 `unit`、`association`（`cell`/`point`）、`dtype`、`shape`、
  `components`（1/3/6/9），足够表达掺杂、损伤、应力这类体/节点量。
- `LayerState` = 有序的 `Layer` 元组，`layer_id` 唯一；`material_id` 可以和别的层
  重复，身份由 `layer_id` + `name` + `source_step` 表达。
- `VariantCapabilities.supports_fields` 仍表示变体声明能读写字段；实际的准入靠
  `reads`/`writes` 里的 `"fields"`/`"layers"`。
- `DefaultStateAdapter.prepare` 对 `"layers"`/`"fields" in reads` 且 state 里没有对应
  表示时抛 `StateMissingError`：**显式不可用**，不会用空层表/空 cell-set 冒充，也不会
  从 level-set 静默降级转换。
- 没有 producer、没有原生 cell-set adapter、没有 field/layer 持久化；这些见 §0.3.6
  和 §10.1。

## 7. 第一个真实工艺：isotropic 原生插件

文件：`plugins/isotropic/`。

### 7.1 复用而不是重写

变体直接调用现有 worker helper，保证与线上路径同源：

```python
import worker.simulate as simulate          # 延迟 import，避免契约层依赖 worker

ps = engine.handle                          # engine 是 execute(..., engine) 的显式参数
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

变体签名是 `execute(self, state, params, ctx, engine) -> StepResult`：`engine` 由 resolver
租好后显式传入，变体不再从 `state` 里取句柄。按 v2.2 契约，isotropic 迁移后的返回形状是
`StepResult(state=StateDelta(geometry=...), metadata={...}, warnings=[...])`，
`outputs` 保持默认空 `OutputSet`：它**不产出** metrology 或 artifact。

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
    output_kinds=(),           # 不声明 metrology / artifact
)
EngineRequirement(kind="native", engine_id="viennaps", min_version="4.6.2")
```

注册时只写 `VariantRegistration(process_id="isotropic", variant=IsotropicNativeVariant())`；
`variant_id`、版本、capabilities、engine 和哈希来源都从变体上读取，不在 registration
里重复声明。

`mutates_input=True` 是诚实声明：原生路径就地改句柄，`commit` 信任 `result.state.geometry.handle`。
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

### 7.5 worker 影子校验（集成切片）

这一节描述**已落地并通过验证**的 worker 影子集成，但生产执行仍然只走旧路径。
它的目的只有一个：在把 worker 切到 v2 执行之前，先证明 v2 isotropic 插件能复现
现有 worker 路径的结果。v2.2 只改了 `execute` 的返回类型（`StepResult`），
这里的比较逻辑和结论不变。

#### 7.5.1 开关与范围

| 项 | 值 |
| --- | --- |
| 影子开关 | `VIENNAPS_WORKBENCH_V2_ISOTROPIC_SHADOW=1`（默认关闭） |
| 验证 apply 开关 | `VIENNAPS_WORKBENCH_V2_ISOTROPIC_APPLY=1`（默认关闭；只用于能力验证，不是生产 cutover） |
| 模型 | 仅 `model: isotropic` |
| 速率模式 | 仅 `rateMode: uniform` |
| 分派 | `isotropicMethod` 解析结果必须是 `levelSet`，**不是** `geometric` |
| schema | `>= 4`（实测 5 / 7） |
| 维度 | 2D / 3D |

**本阶段明确不做**：

- `rateMode: material`（逐材料速率）不进影子比较；
- `isotropicMethod` 走 geometric `SphereDistribution` 的步骤不进影子比较；
- 其他模型（particle、directional、specialized、CSV 等）完全不触发影子路径。

开关关闭时，worker 的行为、事件流和产物字节不变；没有任何“先算一遍再丢掉”的额外开销。

#### 7.5.2 机制

```
现有 isotropic apply
  ├─ (仅开关打开且参数在范围内) deep copy domain ──┐
  ├─ 旧路径 process.apply() 就地修改原 domain        │
  │                                                  ▼
  │                         worker.process_models_v2.run_isotropic_v2(copy, ...)
  ├─ 记录原 domain 的 surface_state digest + 材料表
  │                         v2 副本的 surface_state digest + 材料表
  ▼                                                  │
比较两个 digest / 材料表 ── 不一致 → 抛错，该 step 失败
  │
  └─ 一致 → 写 events.ndjson: {"type": "v2-isotropic-shadow", ...}
```

要点：

- 深拷贝发生在**旧路径 apply 之前**，且只拷贝 domain，不复制 run 目录、快照或产物；
- 影子执行在副本上进行，不触碰旧路径已经产出的 domain，因此不会改变快照、
  中间输出、VTK 或后续步骤；
- 比较对象是 `worker/simulate.py::surface_state()` 的 `digest` 和 domain 的
  material map（`getMaterialMap()` 的逐项 material ID），覆盖点坐标、线/三角形拓扑、
  每个 cell 的 `MaterialIds` 和 level-set 材料映射；
- 事件记录两侧 digest、两侧材料表、step id 以及 v2 plan identity
  （`ExecutionPlan.identity()` 的九项），供事后排查；
- 不一致时**抛错**而不是只写 warning，避免影子差异被当作通过；
- 影子本身不产生快照、不推进 flow state、不写任何 sink。

**验证 apply 模式**（`...APPLY=1`）在 shadow 比较通过之后，把 `ctx.domain` 替换成
v2 的 domain，并写一条 `v2-isotropic-applied` 事件。这样后续 snapshot、native
state、下一步和 replay 都来自 v2，但默认生产路径仍然不设置这个开关。apply 永远以
shadow 比较为前提，不存在“未比较就 apply”。旧路径仍然会跑一遍以生成现有
`actualParameters`，所以这是**正确性验证**，不是性能 cutover。

#### 7.5.3 “parity”在这里指什么

| 判定项 | 是否作为相等判据 |
| --- | --- |
| `surface_state(...)["digest"]` 相同 | 是（主判据） |
| material map 逐项 material ID 相同 | 是 |
| bounds / pointCount / cellCount 相同 | 是（digest 已覆盖，事件里单列便于阅读） |
| v2 plan identity（process/variant/hash/engine） | 否，只记录；用于解释“哪一版实现跑出的影子” |
| 执行耗时、事件顺序、日志文本 | 否 |

注意：digest 已经包含 material map，因此“同一 digest”强于“同一材料表”；
两者都记录是为了在失败时能直接看出是几何变了还是材料映射变了。

#### 7.5.4 这个影子**没有**证明的事

- 没有 `rateMode: material` 的逐材料速率影子（目前记 skip 事件）；
- 没有 geometric `SphereDistribution` 影子（目前记 skip 事件）；
- **验证 apply 模式**已经跑过 2D 三步 + 3D 两步的完整 API/worker run，并 replay 了
  2D apply run；默认生产路径仍然不执行 v2，`run_isotropic_v2` 只在显式开关下被调用；
- 默认生产路径的 snapshot/replay 不变；apply 模式是测试/验证开关，不是 cutover；
- material/geometric 仍不覆盖；影子通过不等于可以删除旧路径。

#### 7.5.5 怎么跑

worker 侧影子必须用仓库内的隔离数据目录，且显式打开开关：

```bash
VIENNAPS_WORKBENCH_DATA_DIR=$PWD/var/test-v2-shadow \
VIENNAPS_WORKBENCH_V2_ISOTROPIC_SHADOW=1 WORKBENCH_PORT=8767 \
  .venv/bin/python -m unittest -v tests.test_worker_v2_isotropic_shadow
```

不经 worker、直接验证 bridge 的测试（不需要开关）：

```bash
VIENNAPS_WORKBENCH_DATA_DIR=$PWD/var/test-v2-bridge WORKBENCH_PORT=8767 \
  .venv/bin/python -m unittest -v tests.test_worker_v2_isotropic
```

直接 bridge 测试用两个相同的 level-set domain 分别跑旧 worker 路径与
`run_isotropic_v2`，再用 `compare_isotropic_surfaces()` 比较；它不启动服务，也不经过
worker 子进程。比较函数把差异作为数据返回（`matched` / `reasons`），是否抛错由影子
hook 决定。

验证结果（2026-11-01）：

| 项 | 结果 |
| --- | --- |
| `tests.test_worker_v2_isotropic`（bridge 直接对比） | 8 项通过 |
| `tests.test_worker_v2_isotropic_shadow`（影子 + apply + snapshot/state parity） | 16 项通过 |
| `tests.test_v2_isotropic_capability`（API/worker：2D 三步 + replay、3D 两步） | 2 项通过 |
| `tests.test_isotropic_dispatch`（API/worker，开关关闭） | 12 项通过 |
| `tests.test_isotropic_dispatch`（API/worker，开关打开） | 12 项通过；3 条 `v2-isotropic-shadow` 匹配，5 条 material-mode skip |

匹配事件示例（3D deposit）：`oldDigest == newDigest`、两侧 material map 都是 `[10, 30]`、
point/cell count 都是 `882/1600`；plan identity 记录 `viennaps 4.7.0`、isotropic/native
1.0.0、`default-v2` adapter。

额外的离线 parity 测试（2D etch/deposit）还比较了：

- `simulate.save_snapshot()` 写出的 JSON：除 run-relative `outputs` 路径外**逐字段相等**
  （points、topology、cellData/MaterialIds、materials、materialTops、measured change、
  domainReadback）；
- `simulate.save_native_state()` 写出的 `.vpsd` 文件 `stateId`（文件 SHA-256）：**完全相同**。

这两项说明：在相同的输入 domain 上，v2 路径产生的 snapshot 几何和 replay 原生状态文件
与旧路径一致。

验证 apply 模式的 API/worker 测试（`tests.test_v2_isotropic_capability`）进一步覆盖：

- 2D 三步 flow（SiO2 沉积 → 刻蚀 → **再次 SiO2 沉积**），baseline vs apply 的每步
  snapshot JSON（除 run-relative `outputs`）和 native `stateId` 完全相同；
- apply run 的 replay 成功，replay 的 final snapshot/state 与 apply 和 baseline 都相同；
- 3D 两步 flow 的 baseline vs apply 同样逐字段一致；
- 重复材料（level-set material order `[10, 30, 30]`）现在被
  `MaterialState` 明确支持；voxel adapter 仍要求唯一 material ID（独立限制）。

这证明了“验证开关下的 v2 结果路径 + snapshot + replay”可用；它仍然不是默认生产
cutover，默认路径不设置 `...APPLY`。

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

执行体只有一个动作（下面是 v2.2 迁移后的返回形状）：

```python
def execute(self, state, params, ctx, engine) -> StepResult:  # surrogate 不用 engine，但签名必须接受
    grid = state.voxels.grid                     # adapter 已经准备好
    if params.operation == "erode_top_layer":
        grid = _erode_top_layers(grid, params.layers)
    return StepResult(
        state=StateDelta(voxels=VoxelState(grid=grid)),
        metadata={...},
    )  # outputs 默认空 OutputSet
```

注册同样只写 `VariantRegistration(process_id="voxel_probe", variant=VoxelProbeVariant())`。

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
| 2. isotropic | `plugins/isotropic/` 落地 | 与 worker 路径 parity（§7.3）；不回退 legacy | 2D/3D、etch/deposit、逐材料速率全过 |
| 3. sf6o2 / oxidation | 按同一模式搬 | 逐模型搬，参数映射逐行对照 | 每个模型有独立对照测试 |
| 4. 可选 `variant` 字段 + API/UI | 项目 JSON 可写 `variant`；API 列 descriptor | 缺省不进请求哈希；显式写入才参与；前端只读 descriptor | 旧 fixture 哈希不变 |
| 5. 生产 CMP surrogate | 第一个非 proof 的 Python 模型 | 通过完整一致性测试；默认仍是 native；不自动选 surrogate | 参考 flow + golden |
| 6. field / cell-set / layer producer | 实现 `CellSetState`/`LayerState` 的生产者与原生 adapter | 不改 level-set flow state 语义 | 字段/层可跨步骤、可 replay |
| 7. 输出 schema / API / UI | `OutputSet` 暴露成 schema，项目可查询 metrology/artifact | 不进请求哈希；只读展示 | 旧 fixture 哈希不变 |

明确不做 big-bang 重写 `worker/simulate.py`：快照、种子、引擎策略、产物属于 executor
稳定面，插件化只替换“模型构造与执行”这一段。

## 9.1 验证口径（v2.1 历史结果）

> 这一节记录 **v2.1** 的验证结果，保留作为基线。v2.2 改了 `execute` 的返回类型、
> 新增了 state/output 类型并迁移了 isotropic/voxel 插件；v2.2 的结果见 §9.2，
> 不把 v2.1 的 150 项当成 v2.2 的通过证据。

v2.1 只改 API 形状，不改 isotropic/voxel 的物理语义。验证结果是：**v2.1 core/plugins、
worker bridge/shadow/apply 与保持不变的 v1 测试一起通过下列定向套件（150 项，0 失败）**。

| 套件 | 覆盖 | 结果 |
| --- | --- | --- |
| v1 contract + voxel + proof plugin | v1 冻结契约与 voxel 往返 | 38 项通过 |
| v2 core + execution entry | frozen `ProcessState`、`VariantRegistration` 派生、registry 生成 descriptor、`plan.run()`/`run_step()`、write checks、adapter `provides` | 56 项通过 |
| v2 isotropic native | 显式 `engine` 参数下的 parity/rates/masks/3D/identity | 17 项通过 |
| v2 voxel probe | adapter prepare/commit/guard/determinism | 13 项通过 |
| worker v2 bridge | 旧 worker 路径 vs `run_isotropic_v2` 的 2D/3D surface digest parity | 8 项通过 |
| worker v2 shadow/apply | 开关关闭无变化、开关打开匹配、apply 替换 domain、material/geometric skip、snapshot JSON 与 native state hash parity | 16 项通过 |
| v2 capability validation | API/worker：2D 三步 + replay、3D 两步；baseline vs apply | 2 项通过 |
| 合计 | v1 + v2.1 + worker 集成 + capability | **150 项通过** |

命令（隔离数据目录，未启动服务）：

```bash
VIENNAPS_WORKBENCH_DATA_DIR=$PWD/var/test-v2-final WORKBENCH_PORT=8767 \
  .venv/bin/python -m unittest \
  tests.test_process_models_contract tests.test_voxel_adapter \
  tests.test_process_models_voxel_plugin tests.test_process_models_v2_core \
  tests.test_process_models_v2_execution tests.test_process_models_v2_isotropic \
  tests.test_process_models_v2_voxel tests.test_worker_v2_isotropic \
  tests.test_worker_v2_isotropic_shadow tests.test_v2_isotropic_capability
```

这些测试覆盖 v2.1 契约、isotropic 原生路径、voxel proof、worker shadow/apply 和
2D/3D capability validation；**默认生产路径没有切到 v2**，也不代表 16 个工艺都已迁移。

## 9.2 验证结果（v2.2 结构化输出）

v2.2 的定向套件覆盖 core/execution/isotropic/voxel、worker bridge/shadow/apply、
capability validation 和插件生命周期，另加 `GridSpec`/`Field`/`CellSetState`/
`Layer`/`LayerState`/`MetrologyRecord`/`Artifact`/`OutputSet`/`StepResult`/adapter
的校验。结果是 **203 项通过，0 失败**。

| 套件 | 覆盖 | 结果 |
| --- | --- | --- |
| v1 contract + voxel + proof plugin | v1 冻结契约与 voxel 往返 | 38 项通过 |
| v2 core + execution entry | `StateDelta`/`StepResult`/`OutputSet`、grid/field/cell-set/layer/metrology、adapter write checks、resolver stored-only layers/fields | 84 项通过 |
| v2 isotropic native | `StepResult` 迁移后的 parity/rates/masks/3D/identity | 19 项通过 |
| v2 voxel probe | `StepResult` 迁移后的 adapter/guard/determinism | 14 项通过 |
| worker v2 bridge | 旧 worker 路径 vs `run_isotropic_v2` 的 2D/3D surface digest parity | 8 项通过 |
| worker v2 shadow/apply | 开关关闭无变化、apply 替换 domain、snapshot/native state parity | 16 项通过 |
| v2 capability validation | API/worker：2D 三步 + replay、3D 两步 | 2 项通过 |
| plugin lifecycle | manifest/discovery/lock/tamper/duplicate/temp plugin | 22 项通过 |
| 合计 | v1 + v2.2 + worker + lifecycle | **203 项通过** |

另外，默认 `tests.test_isotropic_dispatch` 12 项在影子/apply 开关未设置时通过。
`plugins.lock` 在 isotropic 实现改为 `StepResult` 后已重新生成并通过 `--check`。

这些测试证明的是 **契约、类型、parity 和生命周期**；它们不证明 field/cell-set
producer、stress/device 模拟、field/layer 持久化或 UI/API 已经可用。

## 10. 非目标与开放决策

### 10.1 非目标（本切片）

- 不接入 backend/worker 执行路径（仅默认关闭的影子校验见 §7.5），不改项目 schema，不改请求哈希；
- 不做 warm pool、引擎复用池、并发调度；
- 不实现外部引擎（`external` 只是枚举值）；
- 不做不可信插件隔离（没有 netns/seccomp/独立 host）；
- 不做运行时安装、热加载、插件市场或签名分发；插件发现与 lock 只覆盖运维已部署的目录（§2.2）；
- **不实现 field / cell-set / layer 的 producer 或原生 adapter**；只有类型、校验和
  stored-only 的 prepare/commit；
- **没有 stress / device simulator**，`Artifact(kind="device")` 只是报告类型；
- **没有 field/layer 持久化与 replay**：不进快照、不进 native state；
- **没有输出 schema / API / UI**：`OutputSet` 暂时只在 Python 契约里；
- 不声称任何 surrogate 的物理精度。

### 10.2 开放决策

| 问题 | 现状 | 需要决定 |
| --- | --- | --- |
| 插件粒度 | `sf6o2` 是否独立于 `cf4o2`；isotropic 的 levelSet/geometric 是一个变体还是两个 | 按化学拆还是按家族 + 化学参数 |
| 自动引擎选择 | 只支持显式 `variant_id` + 严格 requirement | 是否允许按 backend 自动挑；若允许，选择原因必须进 plan identity |
| 确定性容差 | `determinism` 只有 `strict`/`tolerance` 两个标签，没有统一度量 | `tolerance` 的度量与阈值如何按 `gridDelta` 定义 |
| 版本保留 | `plugins.lock` 只固定**已安装**的版本；未安装版本的 replay 直接拒绝（§2.2） | 旧版本留多久；删除后旧 replay 是拒绝还是只读归档 |
| 插件依赖隔离 | 变体与 worker 共用同一 `.venv` | 是否需要 per-plugin venv / 子进程边界 |
| warm pool | `StaticEngine.release()` 是 no-op | 何时引入池化，池化后 fingerprint 如何保持稳定 |
| `mutates_input` 的 checkpoint | 只文档化，不强制 | 由 executor 统一 checkpoint，还是要求原生变体自证可重入 |
| layer 顺序 | 契约只要求 `layer_id` 唯一，不规定 top-to-bottom / bottom-to-top | 统一方向，还是让每个 producer 在 descriptor 里声明 |
| output schema 演进 | `OutputSet.schema_version` / `Artifact.schema_version` 已存在，但没有迁移策略 | 跨版本如何读取旧 `OutputSet`；是否要求 artifact 自带 payload schema |
| metadata 边界 | “少量 bookkeeping” 只是约定，没有大小/键限制 | 是否给出允许的键前缀或大小上限，避免重新变成垃圾桶 |

## 11. 在 v2.2 里新增一个 process 的清单

1. **定义参数模型**：Pydantic v2，`extra="forbid"`，范围用 `Field(...)` 表达；
   不要在 `validate()` 里重复 schema 已保证的约束（只补语义约束）。
2. **定义 `ProcessSpec`**：`id`、`version`、`label`、`params_model`、`dimensions`、
   `operations`。版本只随 schema/语义变；不要写 `describe()`。
3. **实现 `EngineVariant`**：`variant_id`、`version`、`capabilities`、`engine`、
   `validate(ctx, params) -> list[Issue]`、
   `execute(state, params, ctx, engine) -> StepResult`。engine 是显式参数。
   报告数据放 `outputs`，不要塞进 `state` 或 `metadata`。
4. **声明 `EngineRequirement`**：`kind`、`engine_id`、`min_version`，需要 GPU 时写
   `backend="gpu"`。不要写“任意版本”。
5. **写清 `VariantCapabilities`**：`reads`/`writes` 必须与 `execute` 实际访问一致；
   原生就地修改必须 `mutates_input=True`；几何限制写进 `geometry_constraints`；
   产出计量/产物时在 `output_kinds` 里声明（例如 `("metrology", "artifact:profile")`）。
6. **注册**：提供 `register_<name>(registry)`，内部 `register_process(spec)` +
   `register_variant(VariantRegistration(process_id=spec.id, variant=...))`。
   只传这两个字段；id/版本/能力/引擎/哈希来源都从变体派生。不要复用别人的 `variant_id`。
7. **一致性测试**（缺一不可）：
   - `resolve_execution` 能出 plan，`identity()` 稳定；
   - `plan.run()` / `run_step()` 与 `execute_plan(plan)` 结果一致；
   - 缺 `reads` 中的状态 → 解析失败；
   - 维度不支持 → 解析失败；
   - 引擎版本/后端不符 → 解析失败且无回退；
   - 返回值是 `StepResult`，且 `state` / `outputs` / `metadata` 的边界正确
     （报告值不出现在 `metadata`，flow state 不出现在 `outputs`）；
   - 形状/材料正确性（按模型定义）；
   - 输入 `ProcessState` 在 `execute` + `commit` 后未被原地修改
     （除非显式声明 `mutates_input=True`）；
   - 2D 与 3D 各一个 smoke。

完整的最小可运行例子见 §0.2。这里只给注册部分的最小骨架：

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
    engine = EngineRequirement(kind="native", engine_id="viennaps", min_version="4.6.2")
    implementation_hash = None

    def validate(self, ctx, params): return []
    def execute(self, state, params, ctx, engine):
        return StepResult(state=StateDelta(), metadata={"rate": params.rate})

def register_my_process(registry: ProcessRegistry) -> None:
    spec = ProcessSpec(id="my_process", version="1.0.0", label="My process",
                       params_model=MyParams, dimensions=(2, 3), operations=("etch",))
    registry.register_process(spec)
    registry.register_variant(VariantRegistration(process_id=spec.id, variant=MyVariant()))
```

## 附录：v2.2 文件清单

| 文件 | 内容 |
| --- | --- |
| `process_models/v2/README.md` | 一页 quickstart：分层、结构化输出、新增 process、运行方式、限制 |
| `process_models/v2/__init__.py` | 公开导出 |
| `process_models/v2/state.py` | `StateKind`、`MaterialState`、`LevelSetState`、`GridSpec`、`Field`、`CellSetState`、`Layer`、`LayerState`、`VoxelState`、`ProcessState`（frozen）、`StateDelta`、`MetrologyRecord`、`Artifact`、`OutputSet`、`StepResult`、`StateMissingError` |
| `process_models/v2/engine.py` | `EngineKind`、`Backend`、`EngineRequirement`、`EngineCapabilities`、`Engine`、`EngineLease`、`EngineProvider`、`StaticEngine`、`StaticEngineProvider`、`EngineUnavailableError` |
| `process_models/v2/contract.py` | `Issue`、`ProcessContext`、`ModelDescriptor`、`VariantDescriptor`、`VariantCapabilities`（含 `output_kinds`）、`ProcessSpec`、`EngineVariant`、`VariantRegistration` |
| `process_models/v2/registry.py` | `ProcessRegistry`、`ResolvedVariant`、`compute_implementation_hash()` |
| `process_models/v2/resolver.py` | `ExecutionRequest`、`ExecutionPlan`、`ExecutionOutcome`（`result: StepResult`）、`ExecutionResolutionError`、`resolve_execution()`、`execute_plan()`、`run_step()` |
| `process_models/v2/adapters.py` | `StateAdapter`、`DefaultStateAdapter`（layers/fields 独立 commit，stored-only prepare） |
| `process_models/plugins/` | 生命周期库：`PluginManifest`/`read_manifest`、lock schema 与 `write_lock`/`verify_lock`、`discover`/`load_registry`、`PluginLifecycleError` |
| `plugins/` + `plugins.lock` + `scripts/lock-plugins.py` | 树内 `isotropic` 的 manifest、生成的锁、生成/校验脚本；见 [`plugin-lifecycle.md`](plugin-lifecycle.md) |
| `plugins/isotropic/` | 第一个真实原生插件（返回 `StepResult`，空 `OutputSet`） |
| `process_models/v2/surrogate/voxel_probe.py` | 第一个 surrogate（proof，返回 `StepResult`，空 `OutputSet`） |

v1 的 `process_models/voxel.py`、`contract.py`、`registry.py` 保持原样；v2 只**复用**
`voxel.VoxelGrid` / `read_voxels` / `write_voxels`，不 import v1 的 `ProcessPlugin`。
