"""Persisted and in-flight process records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .events import utcnow


def _optional_stamp(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _stamp(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        return datetime.fromisoformat(raw)
    return utcnow()


@dataclass(frozen=True, slots=True)
class Confirm:
    """Outcome of a strip head alignment check."""

    ok: bool = False
    position: float = 0.0
    at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "position": self.position, "at": self.at.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Confirm":
        return cls(
            ok=bool(raw.get("ok", False)),
            position=float(raw.get("position", 0.0)),
            at=_stamp(raw.get("at")),
        )


@dataclass(frozen=True, slots=True)
class WeldRecord:
    """A completed weld seam."""

    seam_id: str = ""
    position: float = 0.0
    tension: float = 0.0
    heat_input: float = 0.0
    at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seam_id": self.seam_id,
            "position": self.position,
            "tension": self.tension,
            "heat_input": self.heat_input,
            "at": self.at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WeldRecord":
        return cls(
            seam_id=str(raw.get("seam_id", "")),
            position=float(raw.get("position", 0.0)),
            tension=float(raw.get("tension", 0.0)),
            heat_input=float(raw.get("heat_input", 0.0)),
            at=_stamp(raw.get("at")),
        )


@dataclass(frozen=True, slots=True)
class TensionSnapshot:
    value: float = 0.0
    gain: float = 1.0
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "gain": self.gain, "saved": self.saved.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TensionSnapshot":
        return cls(
            value=float(raw.get("value", 0.0)),
            gain=float(raw.get("gain", 1.0)),
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class AcidSnapshot:
    level: float = 0.0
    offset: float = 0.0
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "offset": self.offset, "saved": self.saved.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AcidSnapshot":
        return cls(
            level=float(raw.get("level", 0.0)),
            offset=float(raw.get("offset", 0.0)),
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class PumpSnapshot:
    running: bool = False
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"running": self.running, "saved": self.saved.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PumpSnapshot":
        return cls(running=bool(raw.get("running", False)), saved=_stamp(raw.get("saved")))


@dataclass(frozen=True, slots=True)
class TrackSnapshot:
    position: float = 0.0
    seam_id: str = ""
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "seam_id": self.seam_id,
            "saved": self.saved.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TrackSnapshot":
        return cls(
            position=float(raw.get("position", 0.0)),
            seam_id=str(raw.get("seam_id", "")),
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class RinseSnapshot:
    active: bool = False
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {"active": self.active, "saved": self.saved.isoformat()}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RinseSnapshot":
        return cls(active=bool(raw.get("active", False)), saved=_stamp(raw.get("saved")))


@dataclass(frozen=True, slots=True)
class LatchSnapshot:
    interlock: bool = False
    alarm: bool = False
    saved: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "interlock": self.interlock,
            "alarm": self.alarm,
            "saved": self.saved.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LatchSnapshot":
        return cls(
            interlock=bool(raw.get("interlock", False)),
            alarm=bool(raw.get("alarm", False)),
            saved=_stamp(raw.get("saved")),
        )


@dataclass(frozen=True, slots=True)
class GainVersion:
    """One tension-gain calibration submission.

    A version stays pending until an operator explicitly confirms it; only a
    confirmed version is allowed to drive the live tension gain.
    """

    version: int = 0
    gain: float = 1.0
    created: datetime = field(default_factory=utcnow)
    confirmed_at: datetime | None = None

    @property
    def confirmed(self) -> bool:
        return self.confirmed_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "gain": self.gain,
            "created": self.created.isoformat(),
            "confirmedAt": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "status": "confirmed" if self.confirmed else "pending",
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "GainVersion":
        return cls(
            version=int(raw.get("version", 0)),
            gain=float(raw.get("gain", 1.0)),
            created=_stamp(raw.get("created")),
            confirmed_at=_optional_stamp(raw.get("confirmedAt")),
        )

    def as_confirmed(self, at: datetime) -> "GainVersion":
        return GainVersion(
            version=self.version,
            gain=self.gain,
            created=self.created,
            confirmed_at=at,
        )


@dataclass(frozen=True, slots=True)
class GainVersionLog:
    """Every gain calibration version, newest last, plus the active pointer."""

    versions: tuple[GainVersion, ...] = ()
    active_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "versions": [version.to_dict() for version in self.versions],
            "activeVersion": self.active_version,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "GainVersionLog":
        entries = raw.get("versions", ())
        if not isinstance(entries, (list, tuple)):
            entries = ()
        versions = tuple(
            GainVersion.from_dict(entry) for entry in entries if isinstance(entry, dict)
        )
        return cls(
            versions=versions,
            active_version=int(raw.get("activeVersion", 0)),
        )

    def last(self) -> GainVersion | None:
        return self.versions[-1] if self.versions else None

    def pending(self) -> GainVersion | None:
        """The newest version that has not been confirmed, if any."""

        for version in reversed(self.versions):
            if not version.confirmed:
                return version
        return None

    def active(self) -> GainVersion | None:
        if not self.active_version:
            return None
        for version in reversed(self.versions):
            if version.version == self.active_version and version.confirmed:
                return version
        return None
