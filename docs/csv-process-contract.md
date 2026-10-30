# CSV 速率表合同（schemaVersion 7 / `csvProcess`）

本合同描述上传 CSV 速率表与 `csvProcess` 步骤的实际行为，全部基于安装版本
ViennaPS 4.6.2 的真实绑定、`ps.RateGrid` 的直接探测，以及
`tests/test_csv_process.py` 的验收；不是从更新源码推断的能力。

## 1. 原生事实（已实测）

| 事实 | 验证结果 |
|---|---|
| `ps.CSVFileProcess` 在 2D/3D 都存在 | `ps.setDimension(2/3)` 后顶层 `ps.CSVFileProcess` 分别绑定到 `viennaps.d2/d3` |
| CSV 列数 = 维度数 D | 2D 为 **`x,rate`**（2 列）；3D 为 **`x,y,rate`**（3 列）。**没有 z 列** |
| 3D 速率只随横向 x/y 变化 | `RateGrid<D=3>` 用 `pt[0], pt[1]` 建表，`interpolateLinear` 只读 `coord[0]/coord[1]` |
| 2D 只有一条横向轴 | `RateGrid<D=2>` 用 `coord[0]` 查表；`offset[1]` 在 2D 中不参与计算 |
| 插值模式 | 原生枚举 `LINEAR / IDW / CUSTOM`；`loadFromCSV` 会按表结构自动判断，但工作台要求显式选择，并在构造后调用 `setInterpolationMode` |
| `setCustomInterpolator` | 接受任意 Python 可调用对象；工作台没有任何字段能到达它，`CUSTOM` 也在 schema 层被拒绝 |
| 方向向量 | 原生构造时单位化；零向量会在原生 `normalizeVec3D` 抛错 |
| 速率符号 | 原生模型没有 etch/deposit 参数。方向 `[0,-1,0]`（2D）或 `[0,0,-1]`（3D）时，正速率使表面外移（沉积），负速率刻蚀 |
| offset | 原生在 `coordinate + offset` 处查表；把 offset 设为 `[1,0]` 等价于把表整体向负 x 平移 1 µm |
| `calculateVisibility` | 原生用 `CalculateVisibilities` 计算遮挡；关闭后不计算 |
| 掩膜 | 构造函数 `maskMaterials` 中的材料速度为 0；工作台按几何（trench/hole/fin 的 `maskMaterial`、stack 的 mask 层、GDS 的 `mask` 层）推导，不新增材料 |
| 加载失败 | `loadFromCSV` 失败时原生只打印错误并返回 `true` 的过程对象，之后速度恒为 0；因此上传与 worker 都会先校验表，失败即显式报错 |

2D/3D 直接探测（1 s、恒定 0.5 µm/s、方向 -h/-z）都得到 **+0.5 µm**；
负速率得到 **-0.5 µm**。2D/3D 的 reflective 与 periodic 边界都完成，无崩溃。

## 2. 资产与 API

资产类型：`csv-assets`，内容寻址，存放在 `VIENNAPS_WORKBENCH_DATA_DIR/csv-assets/`。

| 端点 | 行为 |
|---|---|
| `POST /api/csv-assets?file_name=<name>&dimension=2\|3` | `text/csv` 或 `application/octet-stream`，流式读取并限制 **16 MiB**；校验列数、有限数值、至少 2 行；以 SHA-256 命名存储；写同名 `.json` 元数据；返回 `assetId` 与摘要 |
| `GET /api/csv-assets/{assetId}` | 重新解析并返回元数据与摘要；SHA-256 不匹配返回 404 |

元数据（`<assetId>.json`）字段：

```jsonc
{
  "assetId": "<64 hex sha256>", "fileName": "rates.csv", "schema": "viennaps-rate-grid-v1",
  "dimension": 2, "columns": ["x", "rate"], "rowCount": 5,
  "coordinateUnit": "um", "rateUnit": "um/s",
  "x": {"min": -2, "max": 2, "count": 5},
  "y": {"min": -2, "max": 2, "count": 3},      // 仅 3D
  "rate": {"min": 0.2, "max": 0.6, "sign": "deposit"},
  "linearCompatible": true, "idwCompatible": true, "sizeBytes": 60
}
```

项目只保存 `assetId` + `fileName`（`fileName` 禁止 `/`、`\` 和控制字符），从不保存路径。

## 3. 步骤模型

```jsonc
{
  "id": "step_csv_0001", "name": "CSV 速率表", "enabled": true,
  "operation": "native",                        // 固定；符号由 CSV 速率决定
  "model": "csvProcess",
  "duration": 1.0,                              // 0.01–600 s
  "assetId": "<64 hex>", "fileName": "rates.csv",
  "direction": [0, -1, 0],                      // 3D 为 [x,y,z]；2D 第三分量必须为 0
  "offset": [0, 0],                             // 2D 第二分量必须为 0
  "isotropicComponent": 0.0,                    // 0–100
  "directionalComponent": 1.0,                  // 0–100；两者不能同时为 0
  "interpolationMode": "LINEAR",                // 必填；LINEAR | IDW
  "idwNeighbors": 4,                            // 仅 IDW；1–64，且不得超过表行数
  "calculateVisibility": true
}
```

验证在 worker 启动前完成：

- 资产存在且 SHA-256 匹配；元数据维度必须等于项目维度；
- 表结构与维度匹配（2D 2 列 / 3D 3 列），所有数值有限；
- `LINEAR`：2D 要求 x 严格递增；3D 要求完整矩形 x/y 网格（至少 2×2，不能缺格）；
- `IDW`：`idwNeighbors <= rowCount`；
- 方向非零，2D 的第三个分量为 0，2D 的 `offset[1]` 为 0，offset 在 ±1e6 µm 内；
- `interpolationMode` 缺失、`CUSTOM`、未知字段（例如 `customInterpolator`）都得到 422。

表未覆盖项目 x/y 范围时给 warning（原生在 LINEAR 下会外推/钳制，IDW 始终用最近样本）。

## 4. Worker 行为

1. supervisor 把每个被引用资产复制到 run 目录的 `csv-source-<assetId>.csv`，复制后再次校验 SHA-256；
2. worker 只从 `input.json` 的 `csvAssets` 映射取 run 内路径，路径逃出 run 目录即失败；
3. worker 重新解析并校验表，然后构造真实 `ps.CSVFileProcess(path, direction, offset, iso, dir, masks, calculateVisibility)`；
4. 显式调用 `setInterpolationMode`（IDW 时再调用 `setIDWNeighbors`）和 `setOffset`；
5. 运行记录写入 `actualParameters.csvProcess`：资产 id/文件名/SHA-256、实际单位方向、offset、两个分量、插值模式、邻居数、掩膜材料与 id、CSV 行数/范围/符号、时长、`actualEngine: not-applicable`。

引擎固定为 CPU 路径（`FluxEngineType.CPU_DISK`）；CSV 模型没有随机射线/通量引擎，也没有 seed。
任何缺失资产、哈希不符、表不兼容或维度不符都会让 run 显式失败，不会退回零速度场。

## 5. 已验证维度与操作

| 项目 | 结果 |
|---|---|
| 2D | 已实测并通过直接参考比较（平面、以及带 Mask 的 trench） |
| 3D | 已实测并通过直接参考比较（平面） |
| 操作 | 固定 `native`；正速率沉积、负速率刻蚀，由 CSV 符号决定 |
| 引擎 | `not-applicable`（CPU 进程；不使用 GPU/射线通量） |
| 直接参考误差 | 2D/3D 均为 0（容差断言 1e-6 µm） |

## 6. 限制

- 3D 速率表只有 x/y 两列，不能表达随 z 变化的速率；这是安装版 `RateGrid<D=3>` 的实际语义。
- 2D 的 `offset[1]` 在原生实现中完全不参与计算，因此 schema 要求它为 0，避免静默忽略。
- LINEAR 的边界行为由原生决定：2D 超出最后一个 x 时取最后一个样本；3D 在 x/y 边界附近钳制到边缘单元。
- `CUSTOM` 与 `setCustomInterpolator` 永久不可达（任意 Python 会破坏请求哈希、回放和 bounded-child 模型）。
- 便携包（`/api/continuation-package/export`）暂不打包 CSV 资产，因此含有 `csvProcess` 的项目导出会被明确拒绝（422）；本机保存、重开与 replay 正常。运行归档 ZIP 会包含 `assets/<assetId>.csv`。
- 未归档 GPU 覆盖；该模型本来也没有 GPU 路径。
