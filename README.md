# ViennaPS Workbench

正式发布仍为 `/root/viennaps-workbench-cpu-gpu`，入口 `http://127.0.0.1:8789/`。原有正式服务 `http://127.0.0.1:8769/` 与其用户数据保持独立。

当前目录 `/tmp/viennaps-gpu-geometry-coverage` 是待审核候选版，使用本机 ViennaPS 4.6.2 / ViennaLS 5.8.5。候选版尚未替换正式服务；候选端口和数据目录单独配置。

## 启动与维护

在本目录运行项目生命周期脚本：

```bash
./scripts/start.sh
./scripts/status.sh
./scripts/stop.sh
```

端口保存在 `var/listen-port.json`。项目和运行结果保存在本目录的 `var/`，与 8769 的用户数据隔离。`.venv` 指向原安装环境以继续使用本机 ViennaPS/ViennaLS；前端依赖在 `frontend/node_modules/`，可以在本目录构建 UI：

```bash
cd frontend
npm run typecheck
npm run build
```

## 当前能力和限制

- 第 1–9 阶段的已接入能力及逐阶段验收记录见 `docs/ACCEPTANCE_STAGE*.md`。
- CPU 线程上限按服务进程可用的 CPU affinity/cpuset 动态读取；当前为 20。初始线程数仍为 1。
- 候选版 GPU 只开放 `docs/stage9-evidence/gpu-capability-matrix.json` 中经过本机 GPU_DISK 验证的精确组合，当前有 545 行。本机自动选择优先使用可见 CUDA 设备中显存最大的 GPU；schema 7 可按稳定 UUID 选择设备，设置随项目保存、导出并由重放固定。显式 GPU 失败不会静默切到 CPU。设备枚举、CUDA Runtime UUID/PCI 读回和 RTX 3060 Laptop 性能对照见 `docs/stage9-evidence/gpu-coverage-audit/gpu-device-selection/`。
- 该机器实测到一张 NVIDIA GeForce RTX 3060 Laptop GPU，因此真实设备切换只在这一张卡上验收；模拟双设备列表的单元测试覆盖 UUID 选择解析。三维查看器请求浏览器 WebGL 高性能适配器，实际渲染设备由浏览器和驱动决定，独立于 ViennaPS 计算设备。
- 对 `sf6o2`、`hbro2`、`sf6c4f8`、`neutralTransport`，ViennaPS 4.6.2 的原生 MakeFin 会导致进程崩溃。保留旧项目的原生构造输入，但在这些步骤启用时自动调用经验证的 ViennaPS 布尔 Fin；实际构造和触发回退的模型会写入初始快照并随重放重现。
- 安装版 ViennaPS GPU MultiParticle 只处理一种离子类型，多离子 GPU 请求会被拦截；自动模式仍可使用已验证的 CPU_DISK 组合。其他专用模型、边界、几何、材料或粒子配置的 GPU 支持以矩阵为准。GPU_TRIANGLE / GPU_LINE 未开放。
- 2D/3D 网格不再受 4096 格点固定限制；bounded child 超时、内存/日志限制、取消与进程回收、存储配额及几何有效性检查仍生效。
- 单步 3D GPU 步骤的耗时分解（射线追踪、加速结构重建、level-set 演化、快照输出）、
  CPU 线程数对照、GPU context 复用结论见 `docs/performance-profile-20261029.md`。
  分段计时用 `VIENNAPS_WORKBENCH_PROFILE=1` 触发 `segment-timing` 事件（默认关闭）；
  独立重放探针为 `worker/profile_segments.py`。

Stage 9 设备选择和 RTX 3060 性能验收报告见 `docs/stage9-evidence/gpu-coverage-audit/gpu-device-selection/`；端到端设备选择验收使用隔离候选服务和数据目录。`docs/implementation-status.md` 是早期基线历史记录；当前验收状态以各阶段报告及本说明为准。
