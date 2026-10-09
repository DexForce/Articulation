# AgenTable Articulation 本地服务

从 GPU08 的 `gongsi / gensim-articulation` 复制的 Articraft 与 HTTP 服务，可使用兼容 API 或本机 Codex CLI。云端服务、原 `.env` 和其他工作区均未修改。

## 使用边界

**仅供可信的本机用户使用。** Articraft 会在本机执行模型生成的 Python 和 shell 命令。虽然子进程会移除 API key 环境变量，但这不是安全沙箱，也不隔离本机文件或 Codex 登录。不要接收不可信提示词或暴露到公网。

启动和 `doctor` 不调用模型。`generate` 或向 `/generate_articulation` 提交有效请求才会调用模型，使用 API 额度或 Codex 配额。

## 默认配置

| 后端 | 地址 | 模型与认证 |
| --- | --- | --- |
| `api` | `http://127.0.0.1:18688` | 启动时读取 baseline `.env` 的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` |
| `codex` | `http://127.0.0.1:18689` | 本机 `codex`、现有登录，`gpt-5.6-sol / low / Standard` |

默认配置文件：`/home/dex/EmbodiChain/baseline/embodichain/gen_sim/.env`。不复制 key，不复制服务器认证。`--env-file` 可指定其他文件，`--model` 可显式覆盖模型。API 代理只读取该文件的 `EMBODICHAIN_LLM_PROXY`；空值表示直连，不继承全局代理。Codex 使用本机网络环境，不操作云端 Mihomo 或自动切换节点。

API 默认使用 Chat Completions，保留 MiMo 的供应商默认 thinking，不把 `low` 自动映射给 MiMo。支持 Responses 的网关可显式使用 `--api-mode responses --effort low`；模型 ID、地址和 key 仍读取同一个外部 `.env`。已配置完整 endpoint 时不重复追加路径。正在运行的服务不会自动重读 `.env`。

Codex 启动、检查或直接生成时可用 `--effort low|medium|high|xhigh` 指定本次强度。

Codex 本地启动档显式对齐 2026-10-08 核验的 GPU08 进程配置，定义在 `local.py` 的 `CODEX_RUNTIME_PROFILE` 中。它覆盖继承的同名运行参数，但不修改 shell、Codex 全局配置或共享 SDK 默认值：

| 参数 | 本地运行档 |
| --- | --- |
| 传输 | 强制 HTTPS，首轮与 resume 均关闭 WebSocket |
| 回合保护 | shadow 120 秒、无有效进展 hard deadline 180 秒、重连保护 25 秒 |
| 恢复 | 最多 3 次尝试，统一恢复预算 600 秒；传输失败 resume，非法结构化响应 fresh |
| 上限 | CLI 单次 1800 秒、agent 100 轮、编译 900 秒、任务 7200 秒、并发 1 |

180 秒只是恢复云端保护参数，不是所有断流或慢推理的根治。保留本机 CLI 版本、登录与代理，不复制云端认证，也不降级 CLI。`doctor` 不验证模型账号可用性或长流可靠性。

Codex Fast 使用显式 `--fast`，会透传 `service_tier="fast"` 与 `features.fast_mode=true` 到首轮和 resume；Standard 显式传入 `service_tier="default"` 与 `features.fast_mode=false`，不会修改用户全局配置。根据[官方配置文档](https://learn.chatgpt.com/docs/config-file/config-reference)，Fast 配置请求 `priority` 服务等级，是否可用取决于账号与模型。Fast 会增加额度消耗。

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

Responses API 后端的启动命令（先确认原 API 服务无活动任务并已停止）：

```bash
".venv/bin/python" "local.py" doctor --backend api --api-mode responses --effort low
".venv/bin/python" "local.py" serve --backend api --api-mode responses --effort low
```

Responses 模式复用现有 OpenAI 消息、工具和 checkpoint 逻辑，通过配置的 HTTP 网关发送，不使用官方 WebSocket 固定地址，也不调用 Codex CLI。请求使用 `store=false`，在客户端重放已有 Responses items；加密 reasoning 与工具输出保留。HTTP 尝试的状态、耗时、模型、请求/返回强度和 token 数记录在 `compatible-api-diagnostics/`，不保存请求正文、图片、认证头或 key。鉴权错误不重试，传输及可重试 HTTP 错误最多尝试 `.env` 的 `OPENAI_MAX_ATTEMPTS` 次。

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

### P1 修复验证

已修复 CLI 重连提示 `Reconnecting... N/M (request timed out)` 的错误分类。只有进程成功退出、watchdog 未触发、最终事件为 `turn.completed`、没有终止失败事件且结构化响应合法时，才接受已恢复的回合。鉴权、额度、未知错误及非法工具参数仍拒绝；未恢复的重连超时进入已有的有限重试路径，不自动重提 HTTP 任务。

回归覆盖已保存的 medium/Stapler 失败回合的精简记录、真实失败拒绝、resume 与 fresh 恢复预算，以及首轮/resume 的运行参数。可用安装的真实 CLI 对 loopback 假 API 核验请求参数，不使用真实模型或凭证：

```bash
LD_LIBRARY_PATH="/home/dex/miniconda3/envs/embodichain311/lib" ".venv/bin/python" -m scripts.probe_fast_tier --cloud-profile
```

应用到已启动的本地 Codex 服务时，可复用现有重启工具；它校验 PID、工作区和服务身份，并拒绝在任务活动时重启。以下命令不提交生成任务，也不修改 API 后端：

```bash
LD_LIBRARY_PATH="/home/dex/miniconda3/envs/embodichain311/lib" ".venv/bin/python" -c \
  'import json; from scripts.compare_sol56 import restart; print(json.dumps(restart("low", service_tier="default", align_runtime=True), indent=2))'
```

### 历史图片 Benchmark

```bash
LD_LIBRARY_PATH="/home/dex/miniconda3/envs/embodichain311/lib" ".venv/bin/python" "scripts/benchmark_codex.py"
```

默认只预检，不调用模型。显式添加 `--run` 才会使用本机 Codex 配额，按原顺序运行 Bell、Switch、Stapler、Microwave；`--cases bell` 可只测 Bell。输入沿用 baseline2 的原始 manifest，校验图片哈希和尺寸，原图片及提示词不修改。

同一个驱动也支持 `--backend api`，使用 `127.0.0.1:18688` 和 `outputs/api/`，验收规则、输入及时间上限相同：

```bash
LD_LIBRARY_PATH="/home/dex/miniconda3/envs/embodichain311/lib" ".venv/bin/python" "scripts/benchmark_codex.py" --backend api --run --continue-on-failure
```

API 与 Codex 的协议、输出预算和传输不同；跨后端结果不是严格单因素对照。网关费用不采用 SDK 内置的 OpenAI 静态价格表，`cost=0` 表示未核算，而不是免费。

每次结果单独保存在 `outputs/benchmarks/codex-<UTC时间>/`，包含 request_id、输入副本、状态、下载的 USDC、哈希与独立 OpenUSD 校验。提交不自动重试，首个失败后停止后续案例，避免重复付费任务。静态验收检查原 benchmark 要求的关节数量、根与刚体关系、合法关节目标/限位、已声明质量/惯性、资源依赖；缺失的质量参数单独标记，不能当作动力学通过。

添加 `--continue-on-failure` 可让四例分别完成一次测试，不因单个案例失败而跳过后面的输入。提交结果不确定时仍立即停止，不自动重试。此前 `xhigh` 对照保持了当时的 watchdog；P1 修复后的默认运行档改为上述云端配置。历史结果不改写，不能将不同运行档视为只改变模型强度的对照。

用户确认的 GPT-5.6-Sol 两组对照可使用以下命令复现；默认不执行，`--run` 会重启无活动任务的本地 Codex 服务并真实提交八例：

```bash
LD_LIBRARY_PATH="/home/dex/miniconda3/envs/embodichain311/lib" ".venv/bin/python" -m scripts.compare_sol56 --run
```

顺序为 `medium` 四例，再 `xhigh` 四例。只改变模型和思考强度，不调整代理、watchdog 或校验；完成后服务留在 `gpt-5.6-sol / xhigh`。

GPT-6-Luna / xhigh / Fast 四例测试：

```bash
LD_LIBRARY_PATH="/home/dex/miniconda3/envs/embodichain311/lib" ".venv/bin/python" -m scripts.benchmark_luna_fast --run
```

历史 Luna 测试显式请求 Fast，当时没有恢复事件分类修复或 watchdog 对齐。脚本只调整模型、强度和 tier，其余参数继承运行中的服务；修复后的新运行与旧记录必须分开比较。每例独立提交一次，结束时服务留在此次试验配置；Sol 对照脚本会显式恢复 Standard tier，避免把 Fast 意外混入原对照。

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
- Git 不包含 `.venv`、输出、日志、`.env` 与认证文件；克隆仓库不会携带本机凭证、运行中的服务配置或 benchmark 产物。

协议依据：[MiMo Chat Completions](https://mimo.mi.com/docs/en-US/api/chat)、[MiMo 思考模式](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/other/deep-thinking)、[Codex 非交互调用](https://developers.openai.com/codex/noninteractive/)。
