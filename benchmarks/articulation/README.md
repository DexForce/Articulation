# 四例 Articulation Benchmark

本目录包含 Bell、Switch、Stapler、Microwave 的原始参考图片和 manifest。图片未裁剪、压缩或修改；提示词、图片 SHA-256、尺寸、执行顺序、种子元数据、任务时间上限和关节验收条件保留自历史 benchmark。

使用仓库根目录的安装与 API 启动流程后执行：

```bash
".venv/bin/python" "local.py" benchmark --backend api
".venv/bin/python" "local.py" benchmark --backend api --run --continue-on-failure
```

第一条只做预检。第二条会调用真实模型，每例只提交一次；网络请求的有限恢复不等同于重新提交 HTTP 任务。输出位于 `outputs/benchmarks/`，不纳入 Git。

验收检查下载哈希、二进制 USDC、OpenUSD stage、关节数量/限位/刚体目标、已有质量和惯性、有限变换及自包含资源。它不证明图片语义、动力学、全行程碰撞或操作任务成功。模型推理种子未控制，单轮 4/4 不是稳定成功率保证。

## 干净克隆验证

2026-10-09，从 GitHub 克隆 `dadbe5ac4f272c0503a62129d0bb37d2ff1581ac`，在全新 Conda 和 `.venv` 中执行仓库安装流程、离线 USDC smoke 和 70 项测试，均通过。随后在独立端口运行 API 四例；图片、SDK 模块及输出均来自新克隆，没有借用原工作区的 `.venv` 或 baseline 素材。真实 API 凭证由外部用户配置输入，不随仓库发布。

使用 OrcaRouter `openai/gpt-5.6-sol / low / Responses`，每例提交一次，4/4 完成并通过独立 USDC 静态验收，无 HTTP 重试：

| 案例 | 耗时 | 最终编译警告 |
| --- | --- | --- |
| Bell | 1 分 46 秒 | 0 |
| Switch | 3 分 8 秒 | 0 |
| Stapler | 4 分 34 秒 | 1 |
| Microwave | 2 分 34 秒 | 2 |

总耗时约 12 分 3 秒。警告未通过放宽验收清除；动力学和人工语义验收未运行。费用未核算，不能从 `cost=0` 推断免费。查看 [机器可读验证摘要](results/clean-clone-2026-10-09.json)；它不包含 key、本机路径或生成资产，仅记录测试版本、输入/输出哈希和验收指标。该摘要是一次实测，不是他人账号、不同机器或未来模型版本的成功率承诺。
