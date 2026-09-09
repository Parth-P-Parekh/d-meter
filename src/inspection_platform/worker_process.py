"""Entry point executed inside the supervised pipeline worker."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from pathlib import Path

from .broker import CapabilityOutcome
from .contracts import CapabilityRequest, CycleContext, PipelineResult
from .ipc import IpcEnvelope, MessageType, PipeConnection, connect_pipe
from .packages import PackageArchive


class RemoteCapabilities:
    def __init__(self, connection: PipeConnection, context: CycleContext):
        self._connection = connection
        self._context = context

    async def execute(self, request: CapabilityRequest) -> CapabilityOutcome:
        if request.cycle_id != self._context.cycle_id:
            raise ValueError("capability request cycle_id does not match the active cycle")
        if request.monotonic_deadline > self._context.monotonic_deadline:
            raise ValueError("capability deadline exceeds the cycle deadline")
        outgoing = IpcEnvelope(
            message_type=MessageType.CAPABILITY_REQUEST,
            request_id=request.idempotency_key,
            cycle_id=request.cycle_id,
            request_sequence=request.request_sequence,
            payload=request.model_dump(mode="json"),
        )
        await asyncio.to_thread(self._connection.send, outgoing)
        incoming = await asyncio.to_thread(self._connection.receive)
        if (
            incoming.message_type is not MessageType.CAPABILITY_RESPONSE
            or incoming.request_id != outgoing.request_id
            or incoming.cycle_id != outgoing.cycle_id
            or incoming.request_sequence != outgoing.request_sequence
        ):
            raise ValueError("capability response correlation does not match its request")
        return CapabilityOutcome.model_validate(incoming.payload)


def load_pipeline(package_path: Path):
    manifest = PackageArchive(package_path).inspect()
    module_name, class_name = manifest.entrypoint.split(":", 1)
    sys.path.insert(0, f"{package_path}/pipeline")
    try:
        module = importlib.import_module(module_name)
        pipeline_class = getattr(module, class_name)
        pipeline = pipeline_class()
    finally:
        sys.path.pop(0)
    if not callable(getattr(pipeline, "execute", None)):
        raise TypeError("pipeline entrypoint does not implement execute")
    return manifest, pipeline


async def execute_one(
    connection: PipeConnection, package_path: Path, envelope: IpcEnvelope
) -> None:
    if envelope.message_type is not MessageType.PIPELINE_EXECUTE:
        raise ValueError("worker expected one pipeline_execute message")
    context = CycleContext.model_validate(envelope.payload["context"])
    if context.cycle_id != envelope.cycle_id:
        raise ValueError("execution envelope cycle_id does not match context")
    manifest, pipeline = load_pipeline(package_path)
    if (
        context.pipeline_name != manifest.package_name
        or context.pipeline_version != manifest.semantic_version
        or context.pipeline_checksum != manifest.code_checksum
    ):
        raise ValueError("cycle context does not identify the loaded package")
    capabilities = RemoteCapabilities(connection, context)
    result = await pipeline.execute(context, capabilities)
    if not isinstance(result, PipelineResult):
        result = PipelineResult.model_validate(result)
    if (
        result.cycle_id != context.cycle_id
        or result.pipeline_name != context.pipeline_name
        or result.pipeline_version != context.pipeline_version
        or result.pipeline_checksum != context.pipeline_checksum
    ):
        raise ValueError("pipeline result identity does not match cycle context")
    connection.send(
        IpcEnvelope(
            message_type=MessageType.PIPELINE_RESULT,
            request_id=envelope.request_id,
            cycle_id=context.cycle_id,
            request_sequence=envelope.request_sequence,
            payload=result.model_dump(mode="json"),
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipe", required=True)
    parser.add_argument("--package", type=Path, required=True)
    arguments = parser.parse_args()
    auth_key = sys.stdin.buffer.read(32)
    if len(auth_key) != 32:
        raise SystemExit("worker did not receive its pipe authentication key")
    with connect_pipe(arguments.pipe, auth_key) as connection:
        incoming = connection.receive()
        try:
            asyncio.run(execute_one(connection, arguments.package, incoming))
        except Exception as error:
            connection.send(
                IpcEnvelope(
                    message_type=MessageType.HEALTH,
                    request_id=incoming.request_id,
                    cycle_id=incoming.cycle_id,
                    request_sequence=incoming.request_sequence,
                    payload={
                        "status": "fault",
                        "fault_code": "PIPELINE_WORKER_EXCEPTION",
                        "exception_type": type(error).__name__,
                    },
                )
            )
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
