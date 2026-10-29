# DirectionalProcess 项目合同

本合同适用于候选目录中的 schema 5。schema 1–4 的结构与哈希规则保持原样。

## 输入

- `direction` 是三个有限分量，每个分量在 [-1, 1]，且向量非零；执行前单位化。
- 二维输入顺序为 `[x, h, 0]`，其中 `h` 为截面高度；三维顺序为 `[x, y, z]`，`z` 为垂直方向。
- `directionalRateMagnitude` 和 `isotropicRateMagnitude` 是非负速率大小，范围各为 0–2 µm/s。
- 操作映射原生速度符号：刻蚀为负，沉积为正。该符号约定已通过本机平面表面原生调用核实。
- `calculateVisibility` 控制定向项的遮挡可见性。定向速率为零时该值不适用；全零速率步骤不调用 ViennaPS Process，也不改变几何。
- 掩膜由项目几何和层角色推导，并以本机绑定支持的位置参数列表传入。材料名和 ID 都写入实际执行记录。
- 沉积前按所选材料复制顶层 level set；刻蚀不得携带 `depositMaterial`。
- 定向过程目前只开放已验证的 CPU 路径；schema 5 定向步骤请求 GPU 时验证失败，不会静默使用 CPU。

## ViennaPS 调用

运行使用安装的 ViennaPS 4.6.2 / ViennaLS 5.8.5，经 `scripts/bounded_child.py` 派生并受时限、内存、日志和回收策略约束。执行调用形态为：

```python
ps.DirectionalProcess(unit_direction, signed_directional_rate,
                      signed_isotropic_rate, mask_materials, calculate_visibility)
```

本机绑定接受第四个位置参数 `maskMaterial` 的材料列表；`maskMaterials=` 关键字不受该版本绑定接受。该模型不执行射线通量采样，不配置 `RayTracingParameters`，随机种子和 `raysPerPoint` 记录为不适用。由于当前 `Process` 构造默认会探测 CUDA，工作进程显式钉住 `CPU_DISK` 后执行；这不是一个随机射线引擎。

## 持久化与兼容

- 新定向步骤存为 schema 5；同一格式允许已有 schema 4 工艺步骤，因此可在一个配方中交错执行两种模型。
- 项目 JSON、请求哈希、实际原生参数、快照、VTK、历史结果、ZIP 导出及 replay project 保留方向、速率、遮挡、掩膜、材料和原生输出。
- 哈希使用归一化方向；全零步骤忽略方向和遮挡参数。旧格式 1–4 的哈希分支未改变。
- 这不是 ViennaPS Domain 保存格式；从已有 Domain 续算属于路线第 3 阶段。
