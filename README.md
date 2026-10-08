# AgenTable Articulation 本地服务

从 GPU08 的 `gongsi / gensim-articulation` 复制的 Articraft 与 HTTP 服务，可使用兼容 API 或本机 Codex CLI。云端服务、原 `.env` 和其他工作区均未修改。

## 使用边界

**仅供可信的本机用户使用。** Articraft 会在本机执行模型生成的 Python 和 shell 命令。虽然子进程会移除 API key 环境变量，但这不是安全沙箱，也不隔离本机文件或 Codex 登录。不要接收不可信提示词或暴露到公网。

启动和 `doctor` 不调用模型。`generate` 或向 `/generate_articulation` 提交有效请求才会调用模型，使用 API 额度或 Codex 配额。

## 当前配置

| 后端 | 地址 | 模型与认证 |
| --- | --- | --- |
| `api` | `http://127.0.0.1:18688` | 启动时读取 baseline `.env` 的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` |
| `codex` | `http://127.0.0.1:18689` | 本机 `codex`、现有登录，CLI 默认模型，`xhigh` |

默认配置文件：`/home/dex/EmbodiChain/baseline/embodichain/gen_sim/.env`。当前为 MiMo 的 `mimo-v2.6-pro`。不复制 key，不复制服务器认证。`--env-file` 可指定其他文件，`--model` 可显式覆盖模型。API 代理只读取该文件的 `EMBODICHAIN_LLM_PROXY`；空值表示直连，不继承全局代理。Codex 使用本机网络环境，不操作云端 Mihomo 或自动切换节点。

两个后端各自单任务并发，产物分别保存在 `outputs/api/` 与 `outputs/codex/`。迁移避开远程排队，不保证模型推理更快。

## 运行

在 `/home/dex/EmbodiChain/AgenTable_Articulation` 中执行：

```bash
".venv/bin/python" "local.py" doctor --backend api
".venv/bin/python" "local.py" doctor --backend codex
".venv/bin/python" "local.py" serve --backend api
```

另一终端运行 Codex 后端：

```bash
".venv/bin/python" "local.py" serve --backend codex
```

前台服务使用 `Ctrl+C` 停止。不配置开机自启。首次迁移启动的后台进程和日志记录在忽略的 `.runtime/` 下，重启前先确认旧进程和端口，避免重复启动。

## 调用

```bash
".venv/bin/python" "local.py" client --backend api health
".venv/bin/python" "local.py" client --backend codex health
```

以下命令会真实调用模型并等待结果：

```bash
".venv/bin/python" "local.py" client --backend api generate \
  "Reconstruct this object with one valid articulation and export USDC." \
  --image "/absolute/path/reference.png" --artifact usdc --output "outputs/object.usdc"
```

将 `--backend api` 改为 `--backend codex` 即可使用本机 Codex。也可不启动 HTTP 服务直接运行：

```bash
".venv/bin/python" "local.py" generate --backend api "a hinged box"
```

HTTP 协议保留：`GET /health`、`POST /generate_articulation`（JSON prompt 或 multipart prompt/image）、`GET /tasks/<request_id>`、`POST /tasks/<request_id>/cancel`、`GET /assets/<request_id>/usdc`。没有新增服务认证。只有健康检查或 `succeeded` 不足以证明有合法 USDC；必须检查下载产物。

## 验证与重建

```bash
".venv/bin/python" "local.py" smoke
LD_LIBRARY_PATH="/home/dex/miniconda3/envs/embodichain311/lib" ".venv/bin/python" -m pytest -q
```

`smoke` 使用固定 CAD 示例，不发送模型请求，验证真实编译、USDZ 转 USDC、二进制格式、关节与刚体结构、离线 HTTP 排队和下载一致性。它不验证真实模型生成、真实代理链路、动力学或操作任务成功率。

测试还会启动临时 loopback 假模型，通过真实服务 worker 执行三轮 `write → compile → finish`，验收 USDC 下载；Codex 协议测试只替换模型执行返回值。所有测试均不消耗真实模型额度。

### 历史图片 Benchmark

```bash
LD_LIBRARY_PATH="/home/dex/miniconda3/envs/embodichain311/lib" ".venv/bin/python" "scripts/benchmark_codex.py"
```

默认只预检，不调用模型。显式添加 `--run` 才会使用本机 Codex 配额，按原顺序运行 Bell、Switch、Stapler、Microwave；`--cases bell` 可只测 Bell。输入沿用 baseline2 的原始 manifest，校验图片哈希和尺寸，原图片及提示词不修改。

每次结果单独保存在 `outputs/benchmarks/codex-<UTC时间>/`，包含 request_id、输入副本、状态、下载的 USDC、哈希与独立 OpenUSD 校验。提交不自动重试，首个失败后停止后续案例，避免重复付费任务。静态验收检查原 benchmark 要求的关节数量、根与刚体关系、合法关节目标/限位、已声明质量/惯性、资源依赖；缺失的质量参数单独标记，不能当作动力学通过。

添加 `--continue-on-failure` 可让四例分别完成一次测试，不因单个案例失败而跳过后面的输入。提交结果不确定时仍立即停止，不自动重试。当前 Codex 思考强度已按用户要求改为 `xhigh`；模型、代理、watchdog、单任务超时均不随之修改。

环境隔离于 `.venv`，不修改既有 Conda 环境。当前 Python 3.11 来自 `embodichain311`，因此该解释器仍是本地运行依赖。重建：

```bash
"/home/dex/miniconda3/envs/embodichain311/bin/python" -m venv ".venv"
".venv/bin/python" -m pip install -r "requirements-cloud.lock" \
  -e "vendor/Articraft" -e "vendor/articulation-server" "pytest==8.4.2" "ruff==0.16.10"
```

`local.py` 仅在子进程中注入 Conda C++ 库路径和本地配置，不修改 shell 配置。不要直接使用系统 Python 3.10 启动。

## 来源与改造

- 复制日期：2026-10-08；容器镜像：`sha256:da95805fb2c0eccb236597daf0d5ceddc8fe4be413c6c57a08b78475f50369c2`。
- 云端 `/opt/Articraft/src/articraft` 与 `/opt/articulation-server/src/articulation_server` 经比较与实际部署包一致。容器没有 Git 元数据，不能声明对应上游 commit。
- `vendor/Articraft` 保留 Apache-2.0 许可；服务封装没有独立许可文件，本地使用不代表可以公开再分发。
- 复制时排除 build、egg-info、缓存、Git、`.env`；不拉取历史 runs/jobs、认证卷或代理配置。原文件哈希见 `cloud-source.sha256`，云端包版本见 `requirements-cloud.lock`。
- 本地新增 `compatible-api` 原生 provider，处理图片、工具调用、MiMo `reasoning_content` 回传、有限重试及不完整输出拒绝。不改变原 OpenAI Responses 或 Codex agent 执行架构。
- 修正服务配置中的 venv 解释器符号链接解析；其余本地启动、凭证读取和离线验证集中在 `local.py`、`scripts/` 与 `tests/`。
- 兼容 API 的 `cost=0` 表示没有接入供应商价格计算，不表示免费。保留 token usage；MiMo thinking 使用供应商默认，不把 Codex 的 `medium` 映射为 MiMo 参数。
- 本地 Git 仅初始化 `main`，没有 commit 或远程仓库；`.venv`、输出、日志、`.env` 与认证文件均被忽略。

协议依据：[MiMo Chat Completions](https://mimo.mi.com/docs/en-US/api/chat)、[MiMo 思考模式](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/other/deep-thinking)、[Codex 非交互调用](https://developers.openai.com/codex/noninteractive/)。
