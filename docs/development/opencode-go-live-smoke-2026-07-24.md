# OpenCode Go live smoke — 2026-07-24

This check used the user's already authenticated OpenCode Go account. No
credential, prompt body, account identifier, or auth-file content was recorded.

## Test target

- macOS arm64
- OpenCode CLI `1.18.0`
- official Homebrew executable:
  `/opt/homebrew/bin/opencode -> ../Cellar/opencode/1.18.0/bin/opencode`
- unified-cli source version `0.5.4` release candidate
- synthetic git workspace and synthetic 64×64 PNG

## OpenCode CLI result

The vendor CLI and OpenCode Go subscription worked:

| Check | Result |
|---|---|
| `opencode auth list` | OpenCode Go API credential detected |
| `opencode models --refresh` | 16 current `opencode-go/*` models returned |
| JSON chat with `opencode-go/deepseek-v4-flash` | Passed |
| Local `glob` + `read` tool call | Passed |
| `websearch` with `OPENCODE_ENABLE_EXA=true` | Passed |
| Synthetic PNG with `opencode-go/grok-4.5` | Passed |
| Synthetic PNG with `opencode-go/qwen3.7-plus` | Timed out after more than 140 seconds |

The refreshed Go catalog contained:

`deepseek-v4-flash`, `deepseek-v4-pro`, `glm-5.1`, `glm-5.2`, `grok-4.5`,
`hy3`, `kimi-k2.6`, `kimi-k2.7-code`, `kimi-k3`, `mimo-v2.5`,
`mimo-v2.5-pro`, `minimax-m2.7`, `minimax-m3`, `qwen3.6-plus`,
`qwen3.7-max`, and `qwen3.7-plus`.

## unified-cli result

After replacing the provisional adapter, OpenCode is **Stable** for the
Python, one-shot CLI, and REPL surfaces:

| Surface | Result |
|---|---|
| Python `configure_extension_provider("opencode")` | Passed with the official Homebrew installation |
| Python `create("opencode", ...)` | Passed |
| Python `list_models("opencode", force_refresh=True)` | Passed; 24 models returned at the final refresh |
| Python streaming | Passed |
| Model forwarding | Passed with explicit `provider/model` IDs |
| Session creation and resume | Passed |
| Synthetic 64×64 PNG input | Passed |
| Local file tool | Passed |
| Web-search tool | Passed |
| One-shot `unified-cli run --provider opencode` | Passed |
| REPL | Uses the same tested adapter and exposes the refreshed model picker |
| Browser Providers and model metadata | Listed |
| Browser Chat | Intentionally disabled; see the boundary below |

The provenance binding now accepts the official Homebrew executable without
weakening the unsafe-path checks. The adapter normalizes OpenCode JSON events,
forwards explicit models and images, correlates tool/session events, and uses
the same implementation for Python, CLI, and REPL.

OpenCode can merge remote or system-managed MCP configuration and start those
servers before a normal tool permission decision. Because the wrapper cannot
yet prove that inherited MCP startup is disabled, browser chat remains
fail-closed. This does not block Python, CLI, or REPL use.

## Release decision

- Mark `opencode` **Stable** for Python, CLI, and REPL.
- Keep OpenCode browser chat disabled until inherited MCP startup can be
  positively suppressed.
- The standalone Grok Build provider was independently tested and is also
  **Stable**; an OpenCode-hosted Grok model is not used as evidence for it.
- Keep the other 16 executable adapters at **Preview** until each vendor CLI
  receives its own authenticated smoke test.
