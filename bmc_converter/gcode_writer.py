"""Emit Mach3 / GRBL / LinuxCNC-compatible G-code from BMC points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, TextIO

from .bmc_reader import BmcHeader, BmcPoint, CMD_HARD


@dataclass
class GcodeOptions:
    """Conversion / machine settings."""

    # Geometry: pixel → millimetres
    # If pitch_mm is set, it wins. Else scale_x/scale_y from header map to size_mm.
    pitch_mm: Optional[float] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    flip_y: bool = True  # image Y-down → machine Y-up
    origin: str = "bottom-left"  # or "top-left"

    # Motion
    feed_xy: float = 3000.0
    rapid: bool = True  # G0 between hits; else G1
    decimals: int = 3

    # Impact actuation (diamond tip / NEMA hammer on its OWN axis — not machine Z)
    # "hammer" / "z_pulse" → short move on impact_axis (1 pulse = 1 dot)
    # "dwell"  → G4 Px.xxx (simulator-safe placeholder)
    # "m7_m9" / "m3_m5" / "m8_m9" → digital outputs
    impact_mode: str = "hammer"
    impact_axis: str = "A"  # separate hammer axis letter (A/B/C/U/…) — NOT Z
    dwell_s: float = 0.01
    z_safe: float = 0.0  # retract / rest on impact axis
    z_impact: float = -0.3  # hammer tap stroke (mm)
    z_feed: float = 3000.0

    # Intensity → machine params
    min_intensity: int = 1  # skip weaker hits if desired (0 keeps all strikes)
    intensity_scales_dwell: bool = True
    hard_cmd_extra_dwell: float = 0.005

    # Profiles tweak comments only; motion is ISO-ish
    controller: str = "generic"  # generic | mach3 | grbl | linuxcnc

    # Limit output size for tests
    max_strikes: Optional[int] = None
    # Keep every Nth strike (full-image preview without huge files)
    stride: int = 1


def _mm_per_pixel(header: BmcHeader, opt: GcodeOptions) -> tuple[float, float]:
    if opt.pitch_mm is not None:
        return opt.pitch_mm, opt.pitch_mm

    # Header scale_* appear to be workspace units (sample: 9842).
    # If user gives target physical size, use that; else assume scale is 0.01 mm units.
    if opt.width_mm is not None:
        sx = opt.width_mm / max(1, header.width - 1)
    elif header.scale_x > 0:
        sx = (header.scale_x * 0.01) / max(1, header.width - 1)
    else:
        sx = 0.05  # fallback 0.05 mm/px

    if opt.height_mm is not None:
        sy = opt.height_mm / max(1, header.height - 1)
    elif header.scale_y > 0:
        sy = (header.scale_y * 0.01) / max(1, header.height - 1)
    else:
        sy = sx

    return sx, sy


def _to_machine_xy(
    point: BmcPoint, header: BmcHeader, opt: GcodeOptions, sx: float, sy: float
) -> tuple[float, float]:
    x = point.x * sx
    if opt.flip_y:
        y = (header.height - 1 - point.y) * sy
    else:
        y = point.y * sy
    return x, y


def _impact_lines(point: BmcPoint, opt: GcodeOptions, max_i: int) -> list[str]:
    intensity = max(1, point.intensity)
    scale = intensity / max_i if opt.intensity_scales_dwell else 1.0
    dwell = opt.dwell_s * scale
    if point.cmd == CMD_HARD:
        dwell += opt.hard_cmd_extra_dwell
        # param often carries extra force — fold gently into dwell
        if point.param > 0:
            dwell += (point.param / 1000.0) * 0.01

    dwell = max(0.001, dwell)
    mode = opt.impact_mode.lower()
    axis = (opt.impact_axis or "A").strip().upper()[:1]
    if not axis.isalpha():
        axis = "A"

    if mode == "dwell":
        return [f"G4 P{dwell:.4f}"]
    if mode == "m7_m9":
        return ["M7", f"G4 P{dwell:.4f}", "M9"]
    if mode == "m3_m5":
        return ["M3", f"G4 P{dwell:.4f}", "M5"]
    if mode == "m8_m9":
        return ["M8", f"G4 P{dwell:.4f}", "M9"]
    if mode in ("hammer", "z_pulse", "axis_pulse"):
        # Separate NEMA hammer axis: one short tap = one dot (not machine Z)
        return [
            f"G1 {axis}{opt.z_impact:.{opt.decimals}f} F{opt.z_feed:.1f}",
            f"G0 {axis}{opt.z_safe:.{opt.decimals}f}",
        ]
    raise ValueError(f"Unknown impact_mode: {opt.impact_mode}")


def write_gcode(
    points: Iterable[BmcPoint],
    header: BmcHeader,
    out: TextIO,
    opt: Optional[GcodeOptions] = None,
) -> dict:
    """Write G-code. Returns stats dict."""
    opt = opt or GcodeOptions()
    sx, sy = _mm_per_pixel(header, opt)
    max_i = header.max_intensity
    move = "G0" if opt.rapid else "G1"
    d = opt.decimals

    out.write("(BMC -> G-code converter)\n")
    out.write(f"(Source size: {header.width}x{header.height} px)\n")
    out.write(f"(Scale: {sx:.6f} mm/px X, {sy:.6f} mm/px Y)\n")
    out.write(f"(Controller profile: {opt.controller})\n")
    out.write(f"(Impact mode: {opt.impact_mode})\n")
    axis = (opt.impact_axis or "A").strip().upper()[:1] or "A"
    hammer = opt.impact_mode.lower() in ("hammer", "z_pulse", "axis_pulse")
    if hammer:
        out.write(
            f"(Hammer axis {axis}: {axis}{opt.z_impact} then {axis}{opt.z_safe}, "
            f"stroke {abs(opt.z_impact - opt.z_safe):.3f} mm — NOT machine Z)\n"
        )
    if opt.stride > 1:
        out.write(f"(Stride: every {opt.stride}th strike)\n")
    out.write("G21\n")  # mm
    out.write("G90\n")  # absolute
    out.write("G94\n")  # feed per minute
    if opt.controller == "grbl":
        out.write("G17\n")
    if not opt.rapid:
        out.write(f"F{opt.feed_xy:.1f}\n")
    if hammer:
        out.write(f"G0 {axis}{opt.z_safe:.{d}f}\n")

    strikes = 0
    skipped = 0
    seen = 0
    last_x = last_y = None
    stride = max(1, opt.stride)
    buf: list[str] = []

    for point in points:
        if not point.is_strike:
            continue
        if point.intensity < opt.min_intensity:
            skipped += 1
            continue

        seen += 1
        if (seen - 1) % stride != 0:
            continue

        x, y = _to_machine_xy(point, header, opt, sx, sy)
        # Skip exact duplicate consecutive coords (segment restarts)
        if last_x is not None and abs(x - last_x) < 1e-9 and abs(y - last_y) < 1e-9:
            skipped += 1
            continue

        if opt.rapid:
            buf.append(f"{move} X{x:.{d}f} Y{y:.{d}f}\n")
        else:
            buf.append(f"{move} X{x:.{d}f} Y{y:.{d}f} F{opt.feed_xy:.1f}\n")

        for line in _impact_lines(point, opt, max_i):
            buf.append(line + "\n")

        if len(buf) >= 8192:
            out.write("".join(buf))
            buf.clear()

        last_x, last_y = x, y
        strikes += 1
        if opt.max_strikes is not None and strikes >= opt.max_strikes:
            buf.append(f"(Truncated at {strikes} strikes)\n")
            break

    if hammer:
        buf.append(f"G0 {axis}{opt.z_safe:.{d}f}\n")
    buf.append("G0 X0 Y0\n")
    buf.append("M2\n")
    if buf:
        out.write("".join(buf))

    return {
        "strikes": strikes,
        "skipped": skipped,
        "mm_per_px_x": sx,
        "mm_per_px_y": sy,
        "width_mm": (header.width - 1) * sx,
        "height_mm": (header.height - 1) * sy,
    }
