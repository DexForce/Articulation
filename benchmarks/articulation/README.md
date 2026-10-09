# 四例 Articulation Benchmark

本目录包含 Bell、Switch、Stapler、Microwave 的原始参考图片和 manifest。图片未裁剪、压缩或修改；提示词、图片 SHA-256、尺寸、执行顺序、种子元数据、任务时间上限和关节验收条件保留自历史 benchmark。

使用仓库根目录的安装与 API 启动流程后执行：

```bash
".venv/bin/python" "local.py" benchmark --backend api
".venv/bin/python" "local.py" benchmark --backend api --run --continue-on-failure
```

第一条只做预检。第二条会调用真实模型，每例只提交一次；网络请求的有限恢复不等同于重新提交 HTTP 任务。输出位于 `outputs/benchmarks/`，不纳入 Git。

验收检查下载哈希、二进制 USDC、OpenUSD stage、关节数量/限位/刚体目标、已有质量和惯性、有限变换及自包含资源。它不证明图片语义、动力学、全行程碰撞或操作任务成功。模型推理种子未控制，单轮 4/4 不是稳定成功率保证。
