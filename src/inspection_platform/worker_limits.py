"""Windows Job Object limits for a supervised worker process."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass


JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION = 15


class WorkerLimitFault(RuntimeError):
    pass


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _CpuRateControlInformation(ctypes.Structure):
    _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]


@dataclass
class WorkerJob:
    memory_bytes: int
    cpu_percent: int = 25
    max_processes: int = 1

    def __post_init__(self) -> None:
        if os.name != "nt":
            raise WorkerLimitFault("Windows Job Objects are required")
        if self.memory_bytes <= 0:
            raise ValueError("memory_bytes must be positive")
        if not 1 <= self.cpu_percent <= 100:
            raise ValueError("cpu_percent must be between 1 and 100")
        if self.max_processes != 1:
            raise ValueError("Phase 1 permits exactly one worker process")
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32 = kernel32
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError()
        try:
            self._apply_limits()
        except BaseException:
            self.close()
            raise

    def _apply_limits(self) -> None:
        extended = _ExtendedLimitInformation()
        extended.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        extended.BasicLimitInformation.ActiveProcessLimit = self.max_processes
        extended.ProcessMemoryLimit = self.memory_bytes
        self._set_information(JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, extended)

        cpu = _CpuRateControlInformation(
            ControlFlags=(
                JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
            ),
            CpuRate=self.cpu_percent * 100,
        )
        self._set_information(JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION, cpu)

    def _set_information(self, information_class: int, value: ctypes.Structure) -> None:
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        if not self._kernel32.SetInformationJobObject(
            self._handle, information_class, ctypes.byref(value), ctypes.sizeof(value)
        ):
            raise ctypes.WinError()

    def assign_process(self, process_handle: int) -> None:
        self._kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        if not self._kernel32.AssignProcessToJobObject(
            self._handle, wintypes.HANDLE(process_handle)
        ):
            raise ctypes.WinError()

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            self._kernel32.CloseHandle(handle)
            self._handle = None

    def __enter__(self) -> "WorkerJob":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
