---
status: accepted
---

# Agent Anthropic 多 key：进程内 SSE 透传轮换，而非会话级冻结或外置代理

Claude Agent SDK 在 warm session 内会冻结一份 `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`，会话级换 key 无法覆盖同会话内的后续 Messages 请求；外置 loopback 代理（如独立 `:10002`）又与凭证配置脱节、易引入 SSE content_block 索引错乱。决定：凭证表存 `api_keys` 池；**≥2 把 key** 时 `build_anthropic_env_dict` 把 SDK 指到进程内 `AnthropicKeyRotator`（loopback + 一次性 proxy token），按请求从池选上游 key、设 `x-api-key`，**原样 aiter_bytes 转发**响应/SSE、不解析 content_block；单 key 仍直连上游 `base_url`。明确不采用：① 仅会话创建时轮换（无法 per-request）；② 继续依赖用户自建外置 rotator；③ 在 ArcReel 内解析/重写 Anthropic SSE（易复现 index desync）。

## Consequences

- 多 key 时 SDK 看到的是 rotator 口令与 loopback URL，真实 upstream 只在 rotator 内使用；`base_url` 须填真实上游（如 `https://apihub.agnes-ai.com`），勿再填 `host.docker.internal:10002`。
- 与 `docs/adr/0017`（每会话从 DB 注入 env、不写全局 os.environ）同轨：注入值在多 key 时变为「指向内嵌轮换器」。
