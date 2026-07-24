"""Verified OpenCode CLI provider with models, tools, sessions, web, and images."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Iterator, Optional, Tuple

from unified_cli.core import ModelInfo
from unified_cli.plugin import (
    PROVIDER_CONFIGURATION_ABI_V1,
    ProviderPluginV1,
    ProviderServerPolicyV1,
)

from ..errors import ConfigurationError, ExtensionError, ProtocolError
from .bridge import (
    AdapterPromptValueV1,
    AdapterProviderBridge,
    _AdapterPluginRuntime,
    _effective_limits,
    _validated_model_id,
)
from .contract import (
    AdapterServerPolicy,
    AdapterStatus,
    BinarySpec,
    DoctorProbeSpec,
    DynamicArgument,
    EnvironmentPolicy,
    ExitStatusProbeSpec,
    FeatureProbeSpec,
    FixedCommandSpec,
    OperationLimits,
    ProbeFormat,
    PromptCommandSpec,
    PromptMode,
    PromptSentinelPolicy,
    ProviderAdapterSpecV1,
    ProviderCapability,
    TransportKind,
    VersionProbeSpec,
)
from .installation import InstallationReceiptV1
from .rich_cli import (
    managed_snapshot_receipt,
    normalized_image_payloads,
    private_invocation_directory,
    run_provider_command,
    write_private_file,
)
from .runtime import AdapterInspectionV1, BinaryProvenance, ProviderAdapterV1


OPENCODE_OFFICIAL_SOURCES = (
    "https://opencode.ai/docs/cli/",
    "https://opencode.ai/docs/permissions/",
)
OPENCODE_DEFAULT_MODEL = "default"
OPENCODE_OFFICIAL_PACKAGE = "opencode-ai"
OPENCODE_HEADLESS_FIXED_ARGV = ("run", "--pure", "--format", "json")

_READ_ONLY_PERMISSION = {
    "*": "deny",
    "read": "allow",
    "glob": "allow",
    "grep": "allow",
    "list": "allow",
    "external_directory": "deny",
    "webfetch": "deny",
    "websearch": "deny",
}
_WEB_PERMISSION = dict(
    _READ_ONLY_PERMISSION,
    webfetch="allow",
    websearch="allow",
)
_SAFE_CONFIG = (
    '{"$schema":"https://opencode.ai/config.json","autoupdate":false,'
    '"plugin":[],"mcp":{},"lsp":false}'
)


def _json_text(value: Mapping[str, str]) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _data_home() -> str:
    configured = os.environ.get("XDG_DATA_HOME")
    if configured and os.path.isabs(configured):
        return os.path.normpath(configured)
    return os.path.join(os.path.expanduser("~"), ".local", "share")


def _environment(*, web_search: bool) -> EnvironmentPolicy:
    return EnvironmentPolicy(
        fixed_values={
            "OPENCODE_CONFIG_CONTENT": _SAFE_CONFIG,
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "OPENCODE_DISABLE_CLAUDE_CODE": "true",
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
            "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
            "OPENCODE_ENABLE_EXA": "true",
            "OPENCODE_PERMISSION": _json_text(
                _WEB_PERMISSION if web_search else _READ_ONLY_PERMISSION
            ),
            # Keep config/cache isolated while letting the official CLI use its
            # own credential/session store without Core reading credential data.
            "XDG_DATA_HOME": _data_home(),
        }
    )


OPENCODE_FIXED_ENVIRONMENT = dict(_environment(web_search=False).fixed_values)

_PROBE_LIMITS = OperationLimits(10.0, 256 * 1024, 64 * 1024, 64)
_MODEL_LIMITS = OperationLimits(30.0, 1024 * 1024, 128 * 1024, 1024)
_PROMPT_LIMITS = OperationLimits(180.0, 16 * 1024 * 1024, 1024 * 1024, 50_000)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _command(*argv: str) -> FixedCommandSpec:
    return FixedCommandSpec(argv, limits=_PROBE_LIMITS)


def _spec(*, web_search: bool) -> ProviderAdapterSpecV1:
    return ProviderAdapterSpecV1(
        id="opencode",
        display_name="OpenCode",
        status=AdapterStatus.STABLE,
        binary=BinarySpec(
            executable="opencode",
            expected_identity="opencode",
            version_probe=VersionProbeSpec(
                _command("--version"),
                minimum_version=(1, 1, 1),
                format=ProbeFormat.PLAIN_TEXT,
                version_is_entire_line=True,
            ),
            feature_probe=FeatureProbeSpec(
                _command("run", "--help"),
                required_features=frozenset(
                    ("chat", "stream", "sessions", "tools", "images")
                ),
                format=ProbeFormat.PLAIN_TEXT,
                feature_markers={
                    "chat": "opencode run [message..]",
                    "stream": "--format",
                    "sessions": "-s, --session",
                    "tools": "--auto",
                    "images": "-f, --file",
                },
                identity_marker="opencode run [message..]",
                marker_prefixes=True,
                identity_prefix=True,
                use_stderr=True,
            ),
        ),
        prompt=PromptCommandSpec(
            fixed_argv=OPENCODE_HEADLESS_FIXED_ARGV,
            dynamic_arguments=(
                DynamicArgument("model", "--model"),
                DynamicArgument("session", "--session"),
                DynamicArgument("attachments", "--file", repeatable=True),
            ),
            mode=PromptMode.POSITIONAL_AFTER_SENTINEL,
            sentinel_policy=PromptSentinelPolicy.REQUIRED,
            limits=_PROMPT_LIMITS,
        ),
        transport=TransportKind.JSONL,
        environment=_environment(web_search=web_search),
        doctor=DoctorProbeSpec(ExitStatusProbeSpec(_command("--version"))),
        capabilities=frozenset(
            (
                ProviderCapability.CHAT.value,
                ProviderCapability.STREAM.value,
                ProviderCapability.SESSIONS.value,
                ProviderCapability.TOOLS.value,
                ProviderCapability.IMAGES.value,
            )
        ),
        server_policy=AdapterServerPolicy(enabled=False),
    )


ADAPTER_SPEC = _spec(web_search=False)
_WEB_ADAPTER_SPEC = _spec(web_search=True)


def _resolve_opencode_installation() -> InstallationReceiptV1:
    return managed_snapshot_receipt(
        provider_id=ADAPTER_SPEC.id,
        executable=ADAPTER_SPEC.binary.executable,
        package_names=(OPENCODE_OFFICIAL_PACKAGE,),
    )


def _state() -> dict:
    return {
        "session": "",
        "terminal": False,
        "active_tools": set(),
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "saw_usage": False,
    }


def _string(record: Mapping, name: str) -> str:
    value = record.get(name)
    if type(value) is not str or not value:
        raise ProtocolError("OpenCode returned a malformed stream record")
    return value


def _counter(value: object) -> int:
    if type(value) is not int or value < 0 or value > 10**15:
        raise ProtocolError("OpenCode returned malformed usage counters")
    return value


def _session_event(record: Mapping, state: dict) -> Tuple[Mapping, ...]:
    session = _string(record, "sessionID")
    if state["session"] and state["session"] != session:
        raise ProtocolError("OpenCode changed session id during one turn")
    if state["session"]:
        return ()
    state["session"] = session
    return ({"type": "session", "session_id": session},)


def _map_record(record: Mapping, state: dict):
    if (
        not isinstance(record, Mapping)
        or type(state) is not dict
        or state.get("terminal") is True
        or type(record.get("type")) is not str
    ):
        raise ProtocolError("OpenCode returned a malformed stream record")
    events = list(_session_event(record, state))
    kind = record["type"]
    part = record.get("part")
    if kind == "step_start":
        if not isinstance(part, Mapping) or part.get("type") != "step-start":
            raise ProtocolError("OpenCode returned a malformed step record")
        return tuple(events)
    if kind == "text":
        if (
            not isinstance(part, Mapping)
            or part.get("type") != "text"
            or type(part.get("text")) is not str
        ):
            raise ProtocolError("OpenCode returned a malformed text record")
        if part["text"]:
            events.append({"type": "text_delta", "text": part["text"]})
        return tuple(events)
    if kind == "tool_use":
        if not isinstance(part, Mapping) or part.get("type") != "tool":
            raise ProtocolError("OpenCode returned a malformed tool record")
        tool_id = _string(part, "callID")
        name = _string(part, "tool")
        tool_state = part.get("state")
        if not isinstance(tool_state, Mapping):
            raise ProtocolError("OpenCode returned a malformed tool state")
        status = tool_state.get("status")
        if status not in ("pending", "running", "completed", "error"):
            raise ProtocolError("OpenCode returned an unknown tool state")
        if tool_id not in state["active_tools"]:
            state["active_tools"].add(tool_id)
            arguments = tool_state.get("input", {})
            if not isinstance(arguments, Mapping):
                raise ProtocolError("OpenCode returned malformed tool arguments")
            events.append(
                {
                    "type": "tool_start",
                    "tool_id": tool_id,
                    "name": name,
                    "arguments": dict(arguments),
                }
            )
        if status in ("completed", "error"):
            state["active_tools"].discard(tool_id)
            events.append(
                {
                    "type": "tool_result",
                    "tool_id": tool_id,
                    "result": tool_state.get(
                        "output", tool_state.get("error", "")
                    ),
                    "is_error": status == "error",
                }
            )
        return tuple(events)
    if kind == "step_finish":
        if not isinstance(part, Mapping) or part.get("type") != "step-finish":
            raise ProtocolError("OpenCode returned a malformed step record")
        tokens = part.get("tokens")
        if not isinstance(tokens, Mapping):
            raise ProtocolError("OpenCode returned malformed usage counters")
        cache = tokens.get("cache", {})
        if not isinstance(cache, Mapping):
            raise ProtocolError("OpenCode returned malformed usage counters")
        state["input_tokens"] += _counter(tokens.get("input"))
        state["output_tokens"] += _counter(tokens.get("output"))
        state["cached_tokens"] += _counter(cache.get("read", 0))
        state["saw_usage"] = True
        reason = part.get("reason")
        if type(reason) is not str or not reason:
            raise ProtocolError("OpenCode returned a malformed stop reason")
        if reason not in ("tool-calls", "unknown"):
            state["terminal"] = True
        return tuple(events)
    if kind == "error":
        state["terminal"] = True
        events.append(
            {
                "type": "error",
                "code": "provider_error",
                "message": "OpenCode reported an error.",
                "retryable": False,
            }
        )
        return tuple(events)
    # Future informational records are ignored only after their shared session
    # envelope has passed validation.
    return tuple(events)


def _finalize(state: dict):
    if (
        type(state) is not dict
        or state.get("terminal") is not True
        or state.get("active_tools")
        or type(state.get("session")) is not str
        or not state["session"]
    ):
        raise ProtocolError("OpenCode stream ended without a complete final record")
    events = []
    if state.get("saw_usage") is True:
        events.append(
            {
                "type": "usage",
                "input_tokens": state["input_tokens"],
                "cached_input_tokens": state["cached_tokens"],
                "output_tokens": state["output_tokens"],
            }
        )
    events.append({"type": "done", "reason": "complete"})
    return tuple(events)


class _OpenCodeBridge(AdapterProviderBridge):
    def _prompt_values(
        self,
        *,
        model: str,
        session_id: Optional[str],
        resume_last: bool,
    ) -> Mapping[str, AdapterPromptValueV1]:
        values = dict(
            super()._prompt_values(
                model=model,
                session_id=session_id,
                resume_last=resume_last,
            )
        )
        if model == OPENCODE_DEFAULT_MODEL:
            values.pop("model", None)
        return MappingProxyType(values)

    @contextmanager
    def _prepare_invocation(
        self,
        prompt: str,
        images: Optional[list],
    ) -> Iterator[Tuple[str, Mapping[str, AdapterPromptValueV1]]]:
        if type(prompt) is not str or not prompt.strip():
            raise ConfigurationError("provider prompt must not be empty")
        payloads = normalized_image_payloads(images)
        if not payloads:
            yield prompt, MappingProxyType({})
            return
        with private_invocation_directory() as directory:
            paths = []
            for index, (payload, _media_type, extension) in enumerate(payloads):
                paths.append(
                    write_private_file(
                        directory,
                        "image-{}{}".format(index + 1, extension),
                        payload,
                    )
                )
            yield prompt, MappingProxyType({"attachments": tuple(paths)})


class _OpenCodeRuntime(_AdapterPluginRuntime):
    def __init__(self) -> None:
        super().__init__(
            ADAPTER_SPEC,
            OPENCODE_DEFAULT_MODEL,
            _resolve_opencode_installation,
            _state,
            _map_record,
            None,
            _finalize,
            None,
        )

    def _active_gate(
        self,
        *,
        spec: ProviderAdapterSpecV1,
        bin_path: Optional[str],
        receipt: Optional[InstallationReceiptV1],
        provider_home: Optional[str],
    ) -> Tuple[
        ProviderAdapterV1,
        BinaryProvenance,
        AdapterInspectionV1,
        Mapping[str, str],
    ]:
        environment = spec.environment.select({})
        adapter = ProviderAdapterV1(spec)
        candidate = self._candidate(bin_path, receipt)
        binary = adapter.resolve_installation(candidate)
        inspection = adapter.inspect(
            binary,
            provider_env=environment,
            provider_home=provider_home,
        )
        if not adapter.doctor_provider(
            inspection,
            provider_env=environment,
            provider_home=provider_home,
        ):
            raise ConfigurationError("provider doctor reported unavailable")
        return adapter, binary, inspection, environment

    def factory(
        self,
        *,
        model: Optional[str] = None,
        cwd: Optional[str] = None,
        bin_path: Optional[str] = None,
        extra_env: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        first_output_timeout: Optional[float] = None,
        web_search: bool = False,
        max_output_bytes: Optional[int] = None,
        max_stderr_bytes: Optional[int] = None,
        max_stream_buffer_bytes: Optional[int] = None,
        max_stream_events: Optional[int] = None,
        max_stream_line_bytes: Optional[int] = None,
        receipt: Optional[InstallationReceiptV1] = None,
        provider_home: Optional[str] = None,
        **unknown: Any,
    ) -> AdapterProviderBridge:
        del extra_env
        if unknown or first_output_timeout is not None:
            raise ConfigurationError("provider factory received unsupported options")
        if type(cwd) is not str:
            raise ConfigurationError("provider factory requires an explicit cwd")
        if type(web_search) is not bool:
            raise ConfigurationError("web_search must be bool")
        from ..transports import validated_workspace

        workspace = validated_workspace(cwd)
        selected_model = _validated_model_id(
            OPENCODE_DEFAULT_MODEL if model is None else model
        )
        spec = _WEB_ADAPTER_SPEC if web_search else ADAPTER_SPEC
        limits = _effective_limits(
            spec,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            max_stderr_bytes=max_stderr_bytes,
            max_stream_buffer_bytes=max_stream_buffer_bytes,
            max_stream_events=max_stream_events,
            max_stream_line_bytes=max_stream_line_bytes,
        )
        adapter, binary, inspection, environment = self._active_gate(
            spec=spec,
            bin_path=bin_path,
            receipt=receipt,
            provider_home=provider_home,
        )
        return _OpenCodeBridge(
            adapter=adapter,
            inspection=inspection,
            binary=binary,
            default_model=OPENCODE_DEFAULT_MODEL,
            model=selected_model,
            cwd=workspace,
            provider_env=environment,
            provider_home=provider_home,
            limits=limits,
            state_factory=_state,
            map_record=_map_record,
            map_response=None,
            finalize=_finalize,
            turn_preflight=None,
            web_search=web_search,
            allow_web_search=True,
            max_stream_buffer_bytes=max_stream_buffer_bytes,
            max_stream_line_bytes=max_stream_line_bytes,
        )

    @staticmethod
    def _model_ids(output: str) -> Tuple[str, ...]:
        values = []
        for raw in output.splitlines():
            value = _ANSI_RE.sub("", raw).strip()
            if (
                "/" in value
                and not any(character.isspace() for character in value)
                and len(value) <= 512
            ):
                values.append(value)
        if not values or len(values) != len(set(values)):
            raise ProtocolError("OpenCode returned an invalid model list")
        return tuple(values)

    def _models_from_gate(
        self,
        adapter: ProviderAdapterV1,
        inspection: AdapterInspectionV1,
        binary: BinaryProvenance,
        environment: Mapping[str, str],
        provider_home: Optional[str],
    ) -> Tuple[ModelInfo, ...]:
        try:
            output, _ = run_provider_command(
                adapter=adapter,
                inspection=inspection,
                binary=binary,
                argv=("models", "--refresh"),
                provider_env=environment,
                provider_home=provider_home,
                limits=_MODEL_LIMITS,
            )
        except ExtensionError:
            output, _ = run_provider_command(
                adapter=adapter,
                inspection=inspection,
                binary=binary,
                argv=("models",),
                provider_env=environment,
                provider_home=provider_home,
                limits=_MODEL_LIMITS,
            )
        values = self._model_ids(output)
        rows = [
            ModelInfo(
                id=OPENCODE_DEFAULT_MODEL,
                provider=ADAPTER_SPEC.id,
                default=True,
                source="plugin",
            )
        ]
        rows.extend(
            ModelInfo(id=value, provider=ADAPTER_SPEC.id, source="plugin")
            for value in values
        )
        return tuple(rows)

    def _models_with_context(
        self,
        receipt: InstallationReceiptV1,
        provider_env: Mapping[str, str],
        provider_home: Optional[str],
    ) -> Tuple[ModelInfo, ...]:
        del provider_env
        receipt.verify()
        adapter, binary, inspection, environment = self._active_gate(
            spec=ADAPTER_SPEC,
            bin_path=None,
            receipt=receipt,
            provider_home=provider_home,
        )
        return self._models_from_gate(
            adapter, inspection, binary, environment, provider_home
        )

    def models(self) -> Tuple[ModelInfo, ...]:
        adapter, binary, inspection, environment = self._active_gate(
            spec=ADAPTER_SPEC,
            bin_path=None,
            receipt=None,
            provider_home=None,
        )
        return self._models_from_gate(
            adapter, inspection, binary, environment, None
        )

    def _doctor_from_gate(
        self,
        adapter: ProviderAdapterV1,
        inspection: AdapterInspectionV1,
        binary: BinaryProvenance,
        environment: Mapping[str, str],
        provider_home: Optional[str],
    ) -> Mapping[str, object]:
        authenticated = False
        try:
            output, _ = run_provider_command(
                adapter=adapter,
                inspection=inspection,
                binary=binary,
                argv=("auth", "list"),
                provider_env=environment,
                provider_home=provider_home,
                limits=_PROBE_LIMITS,
            )
            clean = _ANSI_RE.sub("", output).lower()
            authenticated = "credentials" in clean and "0 credentials" not in clean
        except ExtensionError:
            authenticated = False
        return MappingProxyType(
            {
                "id": ADAPTER_SPEC.id,
                "available": True,
                "authenticated": authenticated,
                "status": ADAPTER_SPEC.status.value,
                "version": inspection.version,
            }
        )

    def _doctor_with_context(
        self,
        receipt: InstallationReceiptV1,
        provider_env: Mapping[str, str],
        provider_home: Optional[str],
    ) -> Mapping[str, object]:
        del provider_env
        try:
            adapter, binary, inspection, environment = self._active_gate(
                spec=ADAPTER_SPEC,
                bin_path=None,
                receipt=receipt,
                provider_home=provider_home,
            )
            return self._doctor_from_gate(
                adapter, inspection, binary, environment, provider_home
            )
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except ExtensionError:
            return MappingProxyType(
                {
                    "id": ADAPTER_SPEC.id,
                    "available": False,
                    "authenticated": False,
                    "status": ADAPTER_SPEC.status.value,
                }
            )

    def doctor(self) -> Mapping[str, object]:
        try:
            adapter, binary, inspection, environment = self._active_gate(
                spec=ADAPTER_SPEC,
                bin_path=None,
                receipt=None,
                provider_home=None,
            )
            return self._doctor_from_gate(
                adapter, inspection, binary, environment, None
            )
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except ExtensionError:
            return MappingProxyType(
                {
                    "id": ADAPTER_SPEC.id,
                    "available": False,
                    "authenticated": False,
                    "status": ADAPTER_SPEC.status.value,
                }
            )


_RUNTIME = _OpenCodeRuntime()
PLUGIN = ProviderPluginV1(
    id=ADAPTER_SPEC.id,
    factory=_RUNTIME.factory,
    default_model=OPENCODE_DEFAULT_MODEL,
    model_lister=_RUNTIME.models,
    doctor=_RUNTIME.doctor,
    capabilities=ADAPTER_SPEC.capabilities,
    route_prefixes=(ADAPTER_SPEC.id,),
    server_policy=ProviderServerPolicyV1(enabled=False),
    support_status="stable",
    configuration_abi_version=PROVIDER_CONFIGURATION_ABI_V1,
    launch_binder=_RUNTIME.bind,
    environment_keys=ADAPTER_SPEC.environment.allowed_keys,
)


__all__ = [
    "ADAPTER_SPEC",
    "OPENCODE_DEFAULT_MODEL",
    "OPENCODE_FIXED_ENVIRONMENT",
    "OPENCODE_HEADLESS_FIXED_ARGV",
    "OPENCODE_OFFICIAL_PACKAGE",
    "OPENCODE_OFFICIAL_SOURCES",
    "PLUGIN",
]
