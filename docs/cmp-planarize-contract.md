# 材质感知平坦化（CMP）与实测厚度记录

本契约覆盖两个能力，均以安装版 ViennaPS 4.6.2 的实测行为为准：

- `planarize` 步骤可按材料停止（`stopMaterial` + `overPolish`），不再需要手工试出一个截断高度；
- 每个真正执行过的步骤记录各材料顶面的实测变化（`measuredRemoval` / `measuredAdded`）。

## 1. 原生能力边界（先说清限制）

安装版 `viennaps.Planarize(domain, cutoffHeight)` 只有一个高度参数：

```
__init__(self: viennaps.d2.Planarize, geometry: viennaps.d2.Domain, cutoffHeight: SupportsFloat = 0.0)
```

它把所有 level set 在 `cutoffHeight` 以上部分切掉，既没有“停在某种材料”的语义，也没有横向选择比。
ViennaLS 侧同样没有可对单个 level set 做 planarize 的绑定。因此本工作台**不伪造** CMP 选择性：

- `stopMaterial` 停高是**推导**出来的，不是原生选项：运行时先测量该材料当前暴露的顶面，
  取面积加权中位高度，再减去 `overPolish`，把结果作为原生 `Planarize` 的高度参数。
- 落地后仍然是一刀切的全局高度切割；`stopMaterial` 自身高于该高度的部分也会被切掉。
  若停止材料不共面（例如同一材料在场区与栅极顶部各有一段），推导出的单一高度会过切或欠切。
- 上述限制在 `/api/projects/validate` 的 `warnings` 里明确返回，步骤编辑器也会显示说明。

对 22 nm FinFET 流程（`/root/spx_examples/finfet22/workbench/README.md`，run
`f285e7d1146a6b526b2f63da` / `92f9877d838af76e314eda6e`）这正是需要的行为：ILD 顶面是平坦的，
停在 `SiO2` 就能一次去掉场区的 W/HfO2/TiN 覆盖层，而不会像“cutoff 0.092”那样把沟槽里的 W 一起削掉 3 nm。

## 2. 步骤字段

```json
{ "model": "planarize", "stopMaterial": "SiO2", "overPolish": 0.0 }
```

| 字段 | 规则 |
|---|---|
| `cutoffHeight` | 与 `stopMaterial` **二选一**（模型校验强制）；旧项目只写这个字段，行为与请求哈希都不变。 |
| `stopMaterial` | 材料目录中的名字，且在该步运行时必须已经存在（初始几何或更早的启用沉积）。 |
| `overPolish` | 仅用于 `stopMaterial`，单位 µm，`0 ≤ overPolish ≤ 100`；缺省等价于 0。 |

有效截断高度 = `measuredStopTopHeight - overPolish`，其中 `measuredStopTopHeight` 是该材料
上表面单元的**面积加权中位高度**（不是最高点：一条窄而高的毛刺不应改变整片切割高度）。

该步的 `actualParameters` 记录：

```json
{ "geometryOperation": "planarize", "cutoffMode": "material-aware",
  "stopMaterial": "SiO2", "stopMaterialId": 30,
  "measuredStopTopHeight": 0.095, "overPolish": 0.0, "cutoffHeight": 0.095,
  "cutoffDerivation": "area-weighted median height of the up-facing stopMaterial surface ... minus overPolish",
  "nativePlanarizeScope": "viennaps.Planarize(domain, cutoffHeight) cuts every level set above the derived height; lateral selectivity is not modeled" }
```

运行期若该材料在步开始时没有上表面单元（被完全覆盖或已移除），步骤以明确错误失败，提示改用
`cutoffHeight` 或先沉积/暴露该材料；不会静默退回到某个默认高度。

## 3. 实测厚度记录

表面网格在每一步本来就会提取一次（用于快照），因此测量没有额外原生开销，也**不生成体网格**。
每步把上表面单元（法向与竖直方向夹角 ≤ 60°）按材料聚合：

- `topHeight`：该材料上表面最高点（用于厚度变化）
- `medianTopHeight`：面积加权中位高度（用于 CMP 停高）
- `upFacingArea`、`upFacingCellCount`：证据量

记录形式（快照文档、`worker-result.json` 的步骤元数据、`<step>.meta.json` 中一致）：

```json
"measuredRemoval": {
  "material": "Si3N4", "topHeightBefore": 0.0140, "topHeightAfter": 0.0120,
  "removedThickness": 0.0020, "removedCompletely": false,
  "method": "surface-mesh up-facing cells; no volume mesh",
  "materials": [ { "material": "Si3N4", "topHeightBefore": 0.0140, "topHeightAfter": 0.0120,
                   "topHeightDelta": -0.0020, "removedThickness": 0.0020, "addedThickness": null,
                   "removedCompletely": false, "upFacingAreaBefore": 0.803, "upFacingAreaAfter": 0.066 } ]
}
```

- 沉积步骤写 `measuredAdded.addedThickness`；刻蚀/平坦化/移除材料写 `measuredRemoval.removedThickness`。
- 主材料：沉积用 `depositMaterial`，`removeMaterial` 用 `material`，其余取该步顶面下降最多的材料。
- 该步首次出现的沉积材料，其“变化前顶面”取上一步整个表面的最高点（对应
  `Domain.duplicateTopLevelSet` 从当前顶面开始生长），并标记 `materialAppeared: true`。
- 材料被完全移除时 `topHeightAfter` 为 `null`、`removedThickness` 为 `null`，`removedCompletely` 为 `true`，
  并记录该步之后整个表面的最高点 `remainingTopHeight`（不虚构一个厚度值）。
- 变化量小于 `0.25 × gridDelta` 的材料不进入列表，避免栅格噪声。

因此 ionBeam 步骤不再只能看 `planeWaferRate × duration`：那只是标定通量，不是线速度。
实测例子里 0.2 s / 0.05 µm·s⁻¹ 只去掉约 2 nm SiN，而 1.0 s 才清干净；现在这个数字会直接出现在
结果的“实测材料变化”里（`removedThickness` 与 `upFacingAreaAfter`）。

## 4. UI

- 步骤编辑器（平坦化）：`截断高度来源` 选择“固定高度”或“按材料停止 · <材料> 顶面”，
  后者显示 `过抛光量` 与限制说明。
- 结果：查看器信息条显示“实测去除/增加 <材料> <厚度>”；原生结果页签给出顶面前后高度、
  有效截断高度与逐材料变化表。

## 5. 回归

`tests/test_cmp_measured_thickness.py`：

- `MaterialAwareCmpWorkflow`：真实原生 2D 流程（SiO2 场区 + 沟槽，TiN/W 覆盖，再按 SiO2 停止）。
  断言 CMP 后上表面 W 只留在沟槽开口内（长度 ≤ 1.2 µm、|x| ≤ 0.54）、场区没有 W/TiN 暴露，
  且 `measuredRemoval`（W 0.55 µm、TiN 0.05 µm）与 `measuredAdded`（TiN 0.05 µm、W 0.50 µm）成立。
- `PlanarizeDerivationUnit`：停高推导公式、缺少停高表面时的明确错误、表面网格测量的定义。
- `PlanarizeStopMaterialValidation`：材料目录校验、运行时可见性校验、限制 warning，以及把
  `tests/fixtures/finfet22_project.json` 的第 11 步（W CMP）改写成 `stopMaterial: SiO2` 后仍可校验。
- `PlanarizeStopMaterialModel`：二选一约束与“未使用新字段时请求哈希输入不变”。
