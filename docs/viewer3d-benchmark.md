# viewer3d 基准结果

`frontend/src/workflow/viewer3d/` 的 3D/2D 工艺结构查看器基准。数据由
`scripts/run-viewer3d-benchmark.sh` 生成，机器可读版本见
[`docs/viewer3d-benchmark.json`](./viewer3d-benchmark.json)。

## 环境

| 项 | 值 |
|---|---|
| CPU | 12th Gen Intel Core i7-12700H（20 线程） |
| 内存 | 11 GB |
| 浏览器 | Playwright Chromium 1.56.1，headless |
| WebGL 后端 | ANGLE / Vulkan / **SwiftShader**（本机没有 `/dev/dri`，无硬件 GL） |
| 画布 | 1254 × 594（1280 × 800 视口，devicePixelRatio = 1） |
| 负载 | 采集时 1 分钟负载 5–9（共享主机，其他 agent 同时在跑任务） |

## 场景

harness 在页面里生成合成快照，不依赖后端：

- `step-1` / `step-2`：**239,432 三角形**、5 种材料、起伏表面（不同相位/种子）；
- `step-3`：**700,928 三角形**，用来验证顶点聚类简化；
- `?dimension=2`：720 点、719 段的 2D 剖面，3 种材料。

## 结果（最近一次通过）

| 指标 | 结果 | 门槛 |
|---|---|---|
| 三角形数 | 239,432 | ≥ 200,000 ✅ |
| 旋转 120 帧平均帧时间 | **137.8 ms**（p50 158.7 ms，p95 320.9 ms） | ≤ 33 ms ❌ |
| 同机裸 WebGL2 基线（同分辨率、同三角形数、最简着色器） | **49.9 ms/帧** | — |
| 首次切换步骤（冷解析） | **156.8 ms** | < 800 ms ✅ |
| 缓存命中切换步骤 | **11.0 ms** | < 150 ms ✅ |
| 70 万三角形步骤（聚类简化） | 923 ms，700,928 → 188,298 面 | ≤ 300k ✅ |
| 2D 画布 | 9,913 个非背景像素，3 种材料色 | ✅ |
| console error | 0 | ✅ |

同一次运行里的其它断言（都通过）：画布有像素且 5 种材料色都出现、裁剪面把可见像素从
68,000 降到 32,064、线框/表面切换改变图像签名、隐藏 Si 后琥珀色像素从 17,417 降到 19。

### 关于 33 ms 门槛

**在无头 Chromium 下达不到，原因是软件光栅化，不是查看器的 draw call。**

- 同一次运行里，**裸 WebGL2**（一个 `drawElements`、一个位置属性、导数法线 + 一盏方向光、
  同样 239,432 个三角形、同样画布）需要 **49.9 ms/帧** —— 已经超过 33 ms。
- 查看器是 137.8 ms/帧，约为基线的 2.8×。差距来自 vtk.js 的完整片元着色（光照、
  裁剪面、颜色查找表）以及朝向坐标轴的第二遍渲染。
- 硬件 GPU 上这个场景是毫秒级；本机 headless 只能走 SwiftShader，所以这个数字反映的是
  软件光栅器，而不是真实用户环境。
- 负载对数字影响很大：同一份构建在主机空闲时测得 95 ms（等轴视角、同步回读），
  在负载 8–9 时测得 165 ms。表中的 137.8 ms 是负载约 8 时的一次完整运行。

已经做过的优化（见模块 README）：

1. **单 actor / 单 draw call**：全部材料共用一个 `vtkPolyData` + 查找表，而不是每种材料
   一个 actor。
2. **索引化顶点缓冲**：vtk.js 只要发现 *cell* 标量就会摊平成每三角形 3 个顶点
   （`CellArrayBufferObject.canUseIndexedVBO`）。这里在 worker 里按材料拆分顶点、改用
   *point* 标量，顶点数从 3×三角形（约 72 万）降到约 12 万，同时材料边界依然锐利。
3. **超过 50 万三角形自动聚类简化**到 ≤ 30 万（O(n) 网格 snap，保留 materialId），
   避免 70 万面的步骤卡死。
4. **解析全在 worker**（fetch + JSON.parse + 简化 + 拆分），主线程只做 GPU 上传。
5. **LRU(8) + 空闲预取**：相邻步骤切换 11 ms。

**没有采用的取舍**：

- 没有把静态显示也降到 33 ms 所需的约 4 万三角形——那会让材料边界和形貌严重失真，
  属于用画质换数字。
- 没有默认打开背面剔除（可再快约 2×）：ViennaPS 的界面网格可能是开放曲面，从背面看会
  直接消失。需要时可以在 `vtkScene.ts` 里给 `actor.getProperty()` 打开
  `setBackfaceCulling(true)`。

## 复现

```bash
cd /root/wb-ui3d
scripts/run-viewer3d-benchmark.sh
```

脚本会：构建 harness 到 `$TMPDIR/viewer3d-bench-dist` → 用 `vite preview` 起静态服务
（默认 8811，被占用时自动顺延到下一个空闲端口，避开 8769/8789/8819/8820）→ 跑
`frontend/tests/viewer3d-benchmark.mjs` → 把 JSON 写到 `docs/viewer3d-benchmark.json`。

单独调试 harness：

```bash
cd frontend
VIEWER3D_BENCH_OUT=/tmp/v3d npx vite build --config tests/vite.viewer3d.config.ts
VIEWER3D_BENCH_OUT=/tmp/v3d npx vite preview --config tests/vite.viewer3d.config.ts --port 8811
# http://127.0.0.1:8811/viewer3d-harness.html?tris=240000
# http://127.0.0.1:8811/viewer3d-harness.html?dimension=2
```
