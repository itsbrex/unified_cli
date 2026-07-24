#!/usr/bin/env python3
"""Offline fake for the bounded Grok Build adapter contract."""

import json
import os
import sys
import time


def emit(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def sidecar(suffix, default):
    path = os.path.realpath(sys.argv[0]) + suffix
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        return default


args = sys.argv[1:]
fixed_environment = {
    "GROK_DISABLE_AUTOUPDATER": "1",
    "GROK_WRITE_FILE": "0",
    "GROK_TOOL_SEARCH": "0",
    "GROK_LSP_TOOLS": "0",
    "GROK_MEMORY": "0",
    "GROK_SUBAGENTS": "0",
    "GROK_RESPECT_GITIGNORE": "1",
    "GROK_CURSOR_SKILLS_ENABLED": "false",
    "GROK_CURSOR_RULES_ENABLED": "false",
    "GROK_CURSOR_AGENTS_ENABLED": "false",
    "GROK_CURSOR_MCPS_ENABLED": "false",
    "GROK_CURSOR_HOOKS_ENABLED": "false",
    "GROK_CURSOR_SESSIONS_ENABLED": "false",
    "GROK_CLAUDE_SKILLS_ENABLED": "false",
    "GROK_CLAUDE_RULES_ENABLED": "false",
    "GROK_CLAUDE_AGENTS_ENABLED": "false",
    "GROK_CLAUDE_MCPS_ENABLED": "false",
    "GROK_CLAUDE_HOOKS_ENABLED": "false",
    "GROK_CLAUDE_SESSIONS_ENABLED": "false",
    "GROK_CODEX_SKILLS_ENABLED": "false",
    "GROK_CODEX_RULES_ENABLED": "false",
    "GROK_CODEX_AGENTS_ENABLED": "false",
    "GROK_CODEX_MCPS_ENABLED": "false",
    "GROK_CODEX_HOOKS_ENABLED": "false",
    "GROK_CODEX_SESSIONS_ENABLED": "false",
    "GROK_OFFICIAL_MARKETPLACE_AUTO_REGISTER": "0",
    "GROK_MARKETPLACE_REQUIRE_SHA": "1",
    "GROK_MANAGED_MCPS_ENABLED": "false",
    "GROK_MANAGED_MCP_GATEWAY_TOOLS_ENABLED": "false",
}
if any(os.environ.get(name) != value for name, value in fixed_environment.items()):
    raise SystemExit(95)
if args == ["--version"]:
    print(sidecar(".version", "grok 0.2.111 (94172f2aa4e5) [stable]"))
    raise SystemExit(0)

if args == ["--help"]:
    if sidecar(".identity", "official") != "official":
        print("Usage: grok-cli [OPTIONS]")
        print("  --prompt <PROMPT>")
    else:
        print("Grok Build TUI")
        print("Usage: grok [OPTIONS] [PROMPT] [COMMAND]")
        print("  -p, --single <PROMPT>        Single-turn prompt")
        print("  -r, --resume [<SESSION_ID>]  Resume a session")
        print("      --output-format <FORMAT> Output format")
        print("      --tools <TOOLS>          Enabled tools")
        print("      --prompt-json <JSON>     Structured prompt blocks")
    raise SystemExit(0)

if args == ["inspect", "--json"]:
    print('{"status":"ok"}')
    raise SystemExit(0)

if args == ["--no-auto-update", "models"]:
    print("Default model: grok-4.5")
    print("* grok-4.5 (default)")
    print("* grok-code-fast-1")
    raise SystemExit(0)

fixed = [
    "--no-auto-update",
    "--sandbox",
    "unified-cli-strict",
    "--permission-mode",
    "dontAsk",
    "--tools",
    "read_file,grep,list_dir",
    "--deny",
    "Bash",
    "--deny",
    "Edit",
    "--deny",
    "MCPTool",
    "--deny",
    "WebFetch",
    "--deny",
    "WebSearch",
    "--no-plan",
    "--no-subagents",
    "--no-memory",
    "--disable-web-search",
    "--output-format",
    "streaming-json",
]
web_fixed = [
    "--no-auto-update",
    "--sandbox",
    "unified-cli-strict",
    "--permission-mode",
    "dontAsk",
    "--tools",
    "read_file,grep,list_dir,web_search,web_fetch",
    "--allow",
    "WebSearch",
    "--allow",
    "WebFetch",
    "--deny",
    "Bash",
    "--deny",
    "Edit",
    "--deny",
    "MCPTool",
    "--no-plan",
    "--no-subagents",
    "--no-memory",
    "--output-format",
    "streaming-json",
]
if args[: len(fixed)] == fixed:
    args = args[len(fixed) :]
    expected_web_fetch = "0"
elif args[: len(web_fixed)] == web_fixed:
    args = args[len(web_fixed) :]
    expected_web_fetch = "1"
else:
    raise SystemExit(91)
if os.environ.get("GROK_WEB_FETCH") != expected_web_fetch:
    raise SystemExit(95)
if len(args) < 4 or args[0] != "-m":
    raise SystemExit(92)
model = args[1]
args = args[2:]
session = "session-new"
if args[:1] == ["-r"]:
    if len(args) < 4:
        raise SystemExit(93)
    session = args[1]
    args = args[2:]
allow_rules = []
while args[:1] == ["--allow"] and len(args) >= 2:
    allow_rules.append(args[1])
    args = args[2:]
deny_rules = []
while args[:1] == ["--deny"] and len(args) >= 2:
    deny_rules.append(args[1])
    args = args[2:]
if (
    len(allow_rules) != 2
    or not allow_rules[0].startswith("Read(")
    or not allow_rules[1].startswith("Grep(")
    or len(deny_rules) not in (2, 4)
    or not all(
        rule.startswith(("Read(", "Grep(")) for rule in deny_rules
    )
):
    raise SystemExit(93)
if len(args) != 2 or args[0] != "--prompt-file":
    raise SystemExit(94)
try:
    with open(args[1], "r", encoding="utf-8") as handle:
        blocks = json.load(handle)
except (OSError, ValueError):
    raise SystemExit(96)
if (
    not isinstance(blocks, list)
    or not blocks
    or not isinstance(blocks[0], dict)
    or blocks[0].get("type") != "text"
    or not isinstance(blocks[0].get("text"), str)
):
    raise SystemExit(97)
for block in blocks[1:]:
    if (
        not isinstance(block, dict)
        or block.get("type") != "image"
        or not isinstance(block.get("data"), str)
        or block.get("mimeType") not in (
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/gif",
        )
    ):
        raise SystemExit(98)
prompt = blocks[0]["text"]
with open(os.path.realpath(sys.argv[0]) + ".prompt", "a", encoding="utf-8") as handle:
    handle.write("{}|{}|{}\n".format(prompt, len(blocks) - 1, expected_web_fetch))

if prompt == "nonzero":
    sys.stderr.write("provider failed\n")
    raise SystemExit(7)
if prompt == "cancel":
    while True:
        time.sleep(0.05)
if prompt == "flood":
    for index in range(100):
        emit({"type": "text", "data": str(index)})
    emit(
        {
            "type": "end",
            "stopReason": "complete",
            "sessionId": session,
            "requestId": "request-flood",
        }
    )
    raise SystemExit(0)
if prompt == "malformed":
    emit({"type": "text", "data": 7})
    raise SystemExit(0)
if prompt == "unknown":
    emit({"type": "mystery", "data": "secret"})
    raise SystemExit(0)
if prompt == "missing-end":
    emit({"type": "text", "data": "unfinished"})
    raise SystemExit(0)
if prompt in ("duplicate-end", "after-end"):
    end = {
        "type": "end",
        "stopReason": "complete",
        "sessionId": session,
        "requestId": "request-end",
    }
    emit(end)
    emit(end if prompt == "duplicate-end" else {"type": "text", "data": "late"})
    raise SystemExit(0)

emit({"type": "thought", "data": "never expose this"})
emit({"type": "text", "data": prompt})
end = {
    "type": "end",
    "stopReason": "complete",
    "sessionId": session,
    "requestId": "request-{}".format(model),
    "usage": {
        "input_tokens": 3,
        "cache_read_input_tokens": 1,
        "output_tokens": 2,
        "reasoning_tokens": 1,
        "total_tokens": 5,
    },
}
if prompt == "malformed-usage":
    end["usage"]["input_tokens"] = "3"
if prompt == "incomplete-usage":
    end.pop("usage")
    end["usage_is_incomplete"] = True
emit(end)
