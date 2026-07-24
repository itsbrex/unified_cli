"""Shared helpers for verified, media-capable one-shot CLI providers."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from typing import Iterator, Mapping, Optional, Tuple

from unified_cli.core import attachment_bytes, normalize_images

from ..errors import ConfigurationError, ProcessFailed
from ..transports import run_fixed_process
from ..transports.security import _OwnedTemporaryDirectory
from .contract import FixedCommandSpec, OperationLimits
from .installation import InstallationReceiptV1
from .path_resolver import resolve_path_installation
from .runtime import AdapterInspectionV1, BinaryProvenance, ProviderAdapterV1


MAX_IMAGES = 4
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_TOTAL_BYTES = 12 * 1024 * 1024


def managed_snapshot_receipt(
    *,
    provider_id: str,
    executable: str,
    package_names: Tuple[str, ...],
) -> InstallationReceiptV1:
    """Resolve a package-identified or direct vendor installation.

    A failed package-identity check is intentionally terminal.  Copying the
    same mismatched executable into a managed directory would preserve its
    bytes while laundering its provenance.
    """

    return resolve_path_installation(
        provider_id=provider_id,
        executable=executable,
        package_names=package_names,
    )


def normalized_image_payloads(
    images: Optional[list],
) -> Tuple[Tuple[bytes, str, str], ...]:
    """Return bounded image bytes, canonical media types, and extensions."""

    if not images:
        return ()
    if type(images) is not list or len(images) > MAX_IMAGES:
        raise ConfigurationError("provider image input exceeds the supported limit")
    try:
        attachments = normalize_images(images)
    except (TypeError, ValueError):
        raise ConfigurationError("provider image input is invalid") from None
    result = []
    total = 0
    for attachment in attachments:
        if attachment.is_url:
            raise ConfigurationError(
                "provider image URLs must be fetched by the caller"
            )
        try:
            payload = attachment_bytes(attachment)
        except (OSError, TypeError, ValueError):
            raise ConfigurationError("provider image input is unavailable") from None
        if not payload or len(payload) > MAX_IMAGE_BYTES:
            raise ConfigurationError(
                "provider image input exceeds the supported limit"
            )
        if payload.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type, extension = "image/png", ".png"
        elif payload.startswith(b"\xff\xd8\xff"):
            media_type, extension = "image/jpeg", ".jpg"
        elif len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
            media_type, extension = "image/webp", ".webp"
        elif payload.startswith((b"GIF87a", b"GIF89a")):
            media_type, extension = "image/gif", ".gif"
        else:
            raise ConfigurationError("provider image format is unsupported")
        declared = attachment.media_type
        if declared is not None and declared != media_type:
            raise ConfigurationError("provider image media type does not match its data")
        total += len(payload)
        if total > MAX_IMAGE_TOTAL_BYTES:
            raise ConfigurationError(
                "provider image input exceeds the supported limit"
            )
        result.append((payload, media_type, extension))
    return tuple(result)


def write_private_file(directory: str, name: str, payload: bytes) -> str:
    path = os.path.join(directory, name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        pending = memoryview(payload)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise OSError("short write")
            pending = pending[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise OSError("private file shape changed")
    except OSError:
        raise ConfigurationError("provider invocation file could not be prepared") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return path


@contextmanager
def private_invocation_directory() -> Iterator[str]:
    owner = _OwnedTemporaryDirectory(prefix="unified-cli-ext-media-")
    owner.create()
    try:
        yield owner.name
    finally:
        try:
            owner.cleanup()
        except OSError:
            raise ConfigurationError(
                "provider invocation files could not be removed"
            ) from None


def run_provider_command(
    *,
    adapter: ProviderAdapterV1,
    inspection: AdapterInspectionV1,
    binary: BinaryProvenance,
    argv: Tuple[str, ...],
    provider_env: Mapping[str, str],
    provider_home: Optional[str],
    limits: OperationLimits,
) -> Tuple[str, str]:
    """Run one fixed provider-owned command through the verified boundary."""

    adapter._require_inspection(inspection)
    command = FixedCommandSpec(argv=argv, limits=limits)
    invocation = adapter._fixed_command_argv(binary, command)
    owner = _OwnedTemporaryDirectory(prefix="unified-cli-ext-command-")
    owner.create()
    try:
        workspace = os.path.join(owner.name, "workspace")
        os.mkdir(workspace, 0o700)
        result = run_fixed_process(
            invocation,
            timeout=limits.timeout_seconds,
            cwd=workspace,
            provider_env=provider_env,
            allowed_provider_env=tuple(adapter.spec.environment.allowed_keys),
            persistent_home=provider_home,
            limits=limits.transport_limits(),
            executable_identity=binary.executable_identity(),
            launch_identities=binary.spawn_identities(),
        )
        if result.returncode != 0:
            raise ProcessFailed(result.returncode, result.stderr)
        return result.stdout, result.stderr
    finally:
        owner.cleanup()


__all__ = [
    "MAX_IMAGES",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_TOTAL_BYTES",
    "managed_snapshot_receipt",
    "normalized_image_payloads",
    "private_invocation_directory",
    "run_provider_command",
    "write_private_file",
]
