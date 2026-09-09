"""Approved Phase 1 operating limits."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class PlatformSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    data_root: Path = Path(r"C:\ProgramData\InspectionPlatform")
    session_inactivity_seconds: int = Field(default=15 * 60, gt=0)
    session_absolute_seconds: int = Field(default=8 * 60 * 60, gt=0)
    worker_memory_bytes: int = Field(default=2 * 1024**3, gt=0)
    cycle_deadline_seconds: int = Field(default=120, gt=0)
    step_deadline_seconds: int = Field(default=60, gt=0)
    artifact_limit_per_cycle_bytes: int = Field(default=250 * 1024**2, gt=0)
    low_space_warning_bytes: int = Field(default=20 * 1024**3, gt=0)
    stop_accepting_cycles_bytes: int = Field(default=10 * 1024**3, gt=0)

    @property
    def database_path(self) -> Path:
        return self.data_root / "inspection.sqlite3"

    @property
    def artifact_root(self) -> Path:
        return self.data_root / "artifacts"
