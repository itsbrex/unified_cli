from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from unified_cli_ext.providers import AdapterStatus
from unified_cli_ext.providers.opencode import (
    ADAPTER_SPEC,
    OPENCODE_DEFAULT_MODEL,
    OPENCODE_HEADLESS_FIXED_ARGV,
    PLUGIN,
)


@pytest.fixture
def opencode_binary(tmp_path: Path) -> Path:
    source = (
        Path(__file__).parent
        / "fixtures"
        / "providers"
        / "fake_opencode_cli.py"
    )
    interpreter = tmp_path / "fixture-python"
    shutil.copyfile(os.path.realpath(sys.executable), interpreter)
    interpreter.chmod(0o700)
    target = tmp_path / "opencode"
    source_text = source.read_text(encoding="utf-8")
    _, separator, body = source_text.partition("\n")
    assert separator
    target.write_text("#!{}\n{}".format(interpreter, body), encoding="utf-8")
    target.chmod(0o700)
    return target


def _provider(tmp_path: Path, binary: Path, **options):
    return PLUGIN.factory(
        cwd=str(tmp_path),
        bin_path=str(binary),
        **options,
    )


def _invocation(binary: Path) -> dict:
    return json.loads(
        binary.with_suffix(".invocation.json").read_text(encoding="utf-8")
    )


def test_opencode_stable_metadata_and_default_chat(
    tmp_path: Path, opencode_binary: Path
) -> None:
    assert ADAPTER_SPEC.status is AdapterStatus.STABLE
    assert ADAPTER_SPEC.prompt.fixed_argv == OPENCODE_HEADLESS_FIXED_ARGV
    assert ADAPTER_SPEC.capabilities == frozenset(
        ("chat", "images", "sessions", "stream", "tools")
    )
    assert PLUGIN.support_status == "stable"
    assert PLUGIN.server_policy.enabled is False

    response = _provider(tmp_path, opencode_binary).chat("hello")
    assert response.text == "hello|default|0|0"
    assert response.session_id == "session-new"
    assert response.model == OPENCODE_DEFAULT_MODEL
    assert response.usage.input_tokens == 4
    assert response.usage.cached_tokens == 1
    assert response.usage.output_tokens == 2
    invocation = _invocation(opencode_binary)
    assert invocation["argv"] == [*OPENCODE_HEADLESS_FIXED_ARGV, "--", "hello"]


def test_opencode_model_session_tools_and_web(
    tmp_path: Path, opencode_binary: Path
) -> None:
    instance = _provider(
        tmp_path,
        opencode_binary,
        model="opencode-go/grok-4.5",
        web_search=True,
    )
    messages = list(instance.stream("tool", session_id="opencode:session-old"))
    assert messages[-1].kind == "done"
    assert [message.kind for message in messages].count("tool_use") == 1
    assert [message.kind for message in messages].count("tool_result") == 1
    invocation = _invocation(opencode_binary)
    assert invocation["model"] == "opencode-go/grok-4.5"
    assert invocation["session"] == "session-old"
    assert invocation["web"] is True


def test_opencode_images_use_private_repeatable_files(
    tmp_path: Path, opencode_binary: Path
) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"fixture"
    response = _provider(tmp_path, opencode_binary).chat(
        "image",
        images=[png, png],
    )
    assert response.text == "image|default|2|0"
    invocation = _invocation(opencode_binary)
    assert len(invocation["attachments"]) == 2
    assert invocation["argv"].count("--file") == 2
    assert all(not os.path.exists(path) for path in invocation["attachments"])


def test_opencode_dynamic_models_and_auth_doctor(
    tmp_path: Path,
    opencode_binary: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    models = PLUGIN.model_lister()
    assert [model.id for model in models] == [
        "default",
        "opencode/big-pickle",
        "opencode-go/grok-4.5",
        "opencode-go/kimi-k2.7-code",
    ]
    assert PLUGIN.doctor()["authenticated"] is True
