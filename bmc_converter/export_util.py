"""Export helpers: CSV samples and PNG reconstruction."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from .bmc_reader import BmcFile, BmcHeader, BmcPoint, estimate_point_count


def export_header_txt(header: BmcHeader, path: str | Path) -> None:
    path = Path(path)
    lines = [
        f"magic: {header.magic!r}",
        f"unknown_id: {header.unknown_id}",
        f"version: {header.version}",
        f"header_field: {header.header_field}",
        f"width: {header.width}",
        f"height: {header.height}",
        f"gray_levels: {header.gray_levels}",
        f"scale_x: {header.scale_x}",
        f"scale_y: {header.scale_y}",
        f"hard_hit_count: {header.hard_hit_count}",
        f"max_intensity_plus_one: {header.max_intensity_plus_one}",
        f"hard_cmd_code: {header.hard_cmd_code}",
        f"record_size: {header.record_size}",
        f"file_size: {header.file_size}",
        f"data_offset: {header.data_offset}",
        f"estimated_records: {estimate_point_count(header)}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_points_csv(
    bmc: BmcFile,
    path: str | Path,
    *,
    max_points: int = 50_000,
    strikes_only: bool = True,
) -> int:
    path = Path(path)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "cmd", "intensity", "x", "y", "param", "file_offset"])
        for p in bmc.iter_points(strikes_only=strikes_only, max_points=max_points):
            w.writerow([p.index, p.cmd, p.intensity, p.x, p.y, p.param, p.file_offset])
            count += 1
    return count


def export_preview_png(
    bmc: BmcFile,
    path: str | Path,
    *,
    scale: int = 8,
    sample_every: int = 20,
    max_points: Optional[int] = None,
) -> Path:
    """Render a downscaled grayscale preview from strike points."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise SystemExit(
            "Pillow is required for PNG export. Run: pip install pillow"
        ) from exc

    path = Path(path)
    h = bmc.header
    pw, ph = max(1, h.width // scale), max(1, h.height // scale)
    img = Image.new("L", (pw, ph), 255)
    pix = img.load()

    n = 0
    for p in bmc.iter_points(strikes_only=True, max_points=max_points):
        n += 1
        if sample_every > 1 and (n % sample_every) != 0:
            continue
        px, py = p.x // scale, p.y // scale
        if 0 <= px < pw and 0 <= py < ph:
            gray = 255 - int(max(0, min(100, p.intensity)) * 2.55)
            if gray < pix[px, py]:
                pix[px, py] = gray

    img.save(path)
    return path
