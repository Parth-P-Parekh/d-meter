"""Parent-side supervised pipeline worker execution."""

from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .broker import HardwareCapabilityBroker
from .contracts import CapabilityRequest, CycleContext, PipelineResult
from .ipc import AuthenticatedPipeServer, IpcEnvelope, MessageType, PipeConnection
from .output_limits import OutputLimitExceeded
from .packages import PackageArchive, PackageFault
from .settings import PlatformSettings
from .signing import ApprovalSigner
from .worker_limits import WorkerJob
from .worker_output_capture import WorkerOutputCapture


class WorkerExecutionFault(RuntimeError):
    pass


class SupervisedWorkerRunner:
    def __init__(self, settings: PlatformSettings, signer: ApprovalSigner):
        self.settings = settings
        self.signer = signer

    async def execute(
        self,
        *,
        package_path: Path,
        context: CycleContext,
        broker: HardwareCapabilityBroker,
    ) -> PipelineResult:
        manifest = PackageArchive(package_path).inspect()
        if manifest.approval is None:
            raise WorkerExecutionFault("package is not approved")
        trusted_key = base64.b64encode(self.signer.public_key_path.read_bytes()).decode(
            "ascii"
        )
        if manifest.approval.public_key_base64 != trusted_key:
            raise WorkerExecutionFault("package signer is not trusted")
        if (
            context.pipeline_name != manifest.package_name
            or context.pipeline_version != manifest.semantic_version
            or context.pipeline_checksum != manifest.code_checksum
        ):
            raise WorkerExecutionFault("cycle context does not identify the package")
        deadline = min(
            context.monotonic_deadline,
            time.monotonic() + self.settings.cycle_deadline_seconds,
        )
        process: subprocess.Popen[bytes] | None = None
        connection: PipeConnection | None = None
        server = AuthenticatedPipeServer()
        job = WorkerJob(
            memory_bytes=self.settings.worker_memory_bytes,
            cpu_percent=25,
        )
        try:
            environment = os.environ.copy()
            source_root = str(Path(__file__).resolve().parents[1])
            site_packages = str(Path(sys.prefix) / "Lib" / "site-packages")
            environment["PYTHONPATH"] = os.pathsep.join(
                item
                for item in (
                    source_root,
                    site_packages,
                    environment.get("PYTHONPATH"),
                )
                if item
            )
            interpreter = str(Path(sys.base_prefix) / "python.exe")
            process = subprocess.Popen(
                [
                    interpreter,
                    "-m",
                    "inspection_platform.worker_process",
                    "--pipe",
                    server.address,
                    "--package",
                    str(package_path),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            job.assign_process(process._handle)
            if process.stdin is None:
                raise WorkerExecutionFault("worker input channel was not created")
            process.stdin.write(server.auth_key)
            process.stdin.flush()
            process.stdin.close()
            process.stdin = None
            connection = await self._accept_worker(server, process, deadline)
            output_capture = WorkerOutputCapture()
            output_capture.start(process.stdout, process.stderr)
            connection.send(
                IpcEnvelope(
                    message_type=MessageType.PIPELINE_EXECUTE,
                    request_id=f"execute-{context.cycle_id}",
                    cycle_id=context.cycle_id,
                    request_sequence=0,
                    payload={"context": context.model_dump(mode="json")},
                )
            )
            result = await self._message_loop(connection, context, broker, deadline)
            await self._before_deadline(process.wait, deadline)
            try:
                output_capture.finish()
            except OutputLimitExceeded as error:
                raise WorkerExecutionFault(str(error)) from error
            if process.returncode != 0:
                raise WorkerExecutionFault(
                    f"worker exited with code {process.returncode}"
                )
            return result
        except (asyncio.TimeoutError, TimeoutError) as error:
            raise WorkerExecutionFault("pipeline worker exceeded its cycle deadline") from error
        except PackageFault as error:
            raise WorkerExecutionFault(str(error)) from error
        finally:
            if connection is not None:
                connection.close()
            server.close()
            job.close()
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()

    async def _message_loop(
        self,
        connection: PipeConnection,
        context: CycleContext,
        broker: HardwareCapabilityBroker,
        deadline: float,
    ) -> PipelineResult:
        while True:
            incoming = await self._before_deadline(connection.receive, deadline)
            if incoming.cycle_id != context.cycle_id:
                raise WorkerExecutionFault("worker message cycle_id does not match")
            if incoming.message_type is MessageType.CAPABILITY_REQUEST:
                request = CapabilityRequest.model_validate(incoming.payload)
                if (
                    request.cycle_id != incoming.cycle_id
                    or request.request_sequence != incoming.request_sequence
                    or request.idempotency_key != incoming.request_id
                ):
                    raise WorkerExecutionFault("capability request correlation does not match")
                outcome = await broker.execute(request)
                connection.send(
                    IpcEnvelope(
                        message_type=MessageType.CAPABILITY_RESPONSE,
                        request_id=incoming.request_id,
                        cycle_id=incoming.cycle_id,
                        request_sequence=incoming.request_sequence,
                        payload=outcome.model_dump(mode="json"),
                    )
                )
                continue
            if incoming.message_type is MessageType.PIPELINE_RESULT:
                return PipelineResult.model_validate(incoming.payload)
            if incoming.message_type is MessageType.HEALTH:
                raise WorkerExecutionFault(
                    f"worker fault: {incoming.payload.get('fault_code', 'UNKNOWN')}"
                )
            raise WorkerExecutionFault("worker sent an unexpected message type")

    @staticmethod
    async def _before_deadline(function, deadline: float):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        return await asyncio.wait_for(asyncio.to_thread(function), timeout=remaining)

    @staticmethod
    async def _accept_worker(
        server: AuthenticatedPipeServer,
        process: subprocess.Popen[bytes],
        deadline: float,
    ) -> PipeConnection:
        loop = asyncio.get_running_loop()
        accepted: asyncio.Future[PipeConnection] = loop.create_future()

        def set_result(connection: PipeConnection) -> None:
            if accepted.done():
                connection.close()
            else:
                accepted.set_result(connection)

        def set_exception(error: BaseException) -> None:
            if not accepted.done():
                accepted.set_exception(error)

        def accept() -> None:
            try:
                connection = server.accept()
            except BaseException as error:
                loop.call_soon_threadsafe(set_exception, error)
            else:
                loop.call_soon_threadsafe(set_result, connection)

        threading.Thread(target=accept, daemon=True).start()
        while True:
            if accepted.done():
                return accepted.result()
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr is not None else b""
                detail = stderr.decode("utf-8", errors="replace").strip()
                raise WorkerExecutionFault(
                    f"worker exited before connecting with code {process.returncode}: {detail}"
                )
            if time.monotonic() >= deadline:
                raise asyncio.TimeoutError
            await asyncio.sleep(0.01)
