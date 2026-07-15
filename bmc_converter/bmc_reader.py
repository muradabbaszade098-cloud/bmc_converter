"""Parse AGAT / CNConv .BMC diamond-impact engraving files."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

MAGIC = b"ECN\xac"
# First point record (after header / prelude sentinels begin around here).
DEFAULT_DATA_OFFSET = 0xBA
RECORD_SIZE = 8

# Command codes observed in sample files.
CMD_NORMAL = 1
CMD_RUN_START = 2
CMD_SEGMENT = 4
CMD_HARD = 14


@dataclass(frozen=True)
class BmcHeader:
    """Fields inferred from binary analysis of AGAT .BMC files."""

    magic: bytes
    unknown_id: int
    version: int
    header_field: int
    width: int
    height: int
    gray_levels: int
    scale_x: int
    scale_y: int
    hard_hit_count: int
    max_intensity_plus_one: int
    hard_cmd_code: int
    record_size: int
    file_size: int
    data_offset: int

    @property
    def max_intensity(self) -> int:
        return max(1, self.max_intensity_plus_one - 1)


@dataclass(frozen=True)
class BmcPoint:
    """One impact / marker record."""

    cmd: int
    intensity: int
    x: int
    y: int
    param: int
    file_offset: int
    index: int

    @property
    def is_strike(self) -> bool:
        """True if this record is a real engraving hit (not a path marker)."""
        return self.cmd in (CMD_NORMAL, CMD_HARD, CMD_RUN_START) and self.x < 0x7FFF

    @property
    def is_marker(self) -> bool:
        return self.cmd == CMD_SEGMENT or self.x >= 0x7FFF


@dataclass
class BmcFile:
    path: Path
    header: BmcHeader
    _fh: BinaryIO

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "BmcFile":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def iter_points(
        self,
        *,
        strikes_only: bool = False,
        max_points: Optional[int] = None,
    ) -> Iterator[BmcPoint]:
        return iter_points(
            self._fh,
            self.header,
            strikes_only=strikes_only,
            max_points=max_points,
            rewind=True,
        )


def _read_header(fh: BinaryIO, file_size: int, data_offset: int) -> BmcHeader:
    fh.seek(0)
    raw = fh.read(0x60)
    if len(raw) < 0x60:
        raise ValueError("File too small to be a BMC")
    if raw[0:4] != MAGIC:
        raise ValueError(
            f"Bad BMC magic {raw[0:4]!r}; expected {MAGIC!r}"
        )

    version = struct.unpack_from("<I", raw, 0x0A)[0]
    width = struct.unpack_from("<I", raw, 0x22)[0]
    height = struct.unpack_from("<I", raw, 0x26)[0]
    if width == 0 or height == 0 or width > 100_000 or height > 100_000:
        raise ValueError(f"Implausible dimensions {width}x{height}")

    return BmcHeader(
        magic=raw[0:4],
        unknown_id=struct.unpack_from("<I", raw, 0x04)[0],
        version=version,
        header_field=struct.unpack_from("<I", raw, 0x0E)[0],
        width=width,
        height=height,
        gray_levels=struct.unpack_from("<I", raw, 0x2A)[0],
        scale_x=struct.unpack_from("<I", raw, 0x2E)[0],
        scale_y=struct.unpack_from("<I", raw, 0x32)[0],
        hard_hit_count=struct.unpack_from("<I", raw, 0x36)[0],
        max_intensity_plus_one=struct.unpack_from("<I", raw, 0x3A)[0],
        hard_cmd_code=struct.unpack_from("<I", raw, 0x3E)[0],
        record_size=struct.unpack_from("<I", raw, 0x52)[0] or RECORD_SIZE,
        file_size=file_size,
        data_offset=data_offset,
    )


def open_bmc(path: str | Path, data_offset: int = DEFAULT_DATA_OFFSET) -> BmcFile:
    path = Path(path)
    fh = open(path, "rb")
    try:
        header = _read_header(fh, path.stat().st_size, data_offset)
    except Exception:
        fh.close()
        raise
    return BmcFile(path=path, header=header, _fh=fh)


def iter_points(
    fh: BinaryIO,
    header: BmcHeader,
    *,
    strikes_only: bool = False,
    max_points: Optional[int] = None,
    rewind: bool = True,
) -> Iterator[BmcPoint]:
    """Stream point records from an open BMC file handle."""
    if rewind:
        fh.seek(header.data_offset)

    record_size = header.record_size or RECORD_SIZE
    index = 0
    yielded = 0
    while True:
        offset = fh.tell()
        raw = fh.read(record_size)
        if len(raw) < record_size:
            break
        cmd, intensity = raw[0], raw[1]
        x, y, param = struct.unpack_from("<HHH", raw, 2)
        point = BmcPoint(
            cmd=cmd,
            intensity=intensity,
            x=x,
            y=y,
            param=param,
            file_offset=offset,
            index=index,
        )
        index += 1
        if strikes_only and not point.is_strike:
            continue
        yield point
        yielded += 1
        if max_points is not None and yielded >= max_points:
            break


def estimate_point_count(header: BmcHeader) -> int:
    payload = max(0, header.file_size - header.data_offset)
    return payload // (header.record_size or RECORD_SIZE)
