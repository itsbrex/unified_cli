#!/usr/bin/env python3
"""Offline fake for the verified OpenCode JSONL adapter contract."""

import json
import os
import pathlib
import sys


def emit(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


args = sys.argv[1:]
required_environment = {
    "OPENCODE_DISABLE_AUTOUPDATE": "true",
    "OPENCODE_DISABLE_CLAUDE_CODE": "true",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
    "OPENCODE_ENABLE_EXA": "true",
}
if any(os.environ.get(key) != value for key, value in required_environment.items()):
    raise SystemExit(90)
if "OPENCODE_AUTH_CONTENT" in os.environ:
    raise SystemExit(91)

if args == ["--version"]:
    print("1.18.0")
    raise SystemExit(0)
if args == ["run", "--help"]:
    sys.stderr.write(
        "opencode run [message..]\n"
        "  --format\n"
        "  -s, --session\n"
        "  --auto\n"
        "  -f, --file\n"
    )
    raise SystemExit(0)
if args in (["models"], ["models", "--refresh"]):
    print("opencode/big-pickle")
    print("opencode-go/grok-4.5")
    print("opencode-go/kimi-k2.7-code")
    raise SystemExit(0)
if args == ["auth", "list"]:
    print("OpenCode Go 1 credentials")
    raise SystemExit(0)

fixed = ["run", "--pure", "--format", "json"]
if args[: len(fixed)] != fixed:
    raise SystemExit(92)
args = args[len(fixed) :]

model = "default"
session = "session-new"
attachments = []
while args and args[0] != "--":
    flag = args.pop(0)
    if not args:
        raise SystemExit(93)
    value = args.pop(0)
    if flag == "--model":
        model = value
    elif flag == "--session":
        session = value
    elif flag == "--file":
        attachments.append(value)
    else:
        raise SystemExit(94)
if len(args) != 2 or args[0] != "--":
    raise SystemExit(95)
prompt = args[1]

permission = json.loads(os.environ["OPENCODE_PERMISSION"])
web_enabled = (
    permission.get("websearch") == "allow"
    and permission.get("webfetch") == "allow"
)
if permission.get("*") != "deny":
    raise SystemExit(96)
for path in attachments:
    payload = pathlib.Path(path).read_bytes()
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise SystemExit(97)

pathlib.Path(os.path.realpath(sys.argv[0]) + ".invocation.json").write_text(
    json.dumps(
        {
            "argv": sys.argv[1:],
            "attachments": attachments,
            "model": model,
            "prompt": prompt,
            "session": session,
            "web": web_enabled,
        }
    ),
    encoding="utf-8",
)

emit({"type": "step_start", "sessionID": session, "part": {"type": "step-start"}})
if prompt == "tool":
    emit(
        {
            "type": "tool_use",
            "sessionID": session,
            "part": {
                "type": "tool",
                "tool": "read",
                "callID": "tool-1",
                "state": {
                    "status": "completed",
                    "input": {"filePath": "README.md"},
                    "output": "fixture",
                },
            },
        }
    )
text = "{}|{}|{}|{}".format(prompt, model, len(attachments), int(web_enabled))
emit(
    {
        "type": "text",
        "sessionID": session,
        "part": {"type": "text", "text": text},
    }
)
emit(
    {
        "type": "step_finish",
        "sessionID": session,
        "part": {
            "type": "step-finish",
            "reason": "stop",
            "tokens": {
                "input": 4,
                "output": 2,
                "cache": {"read": 1, "write": 0},
            },
        },
    }
)
