#!/usr/bin/env python3
"""CLI: BMC → G-code converter for diamond-impact engravers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bmc_converter import GcodeOptions, __version__, open_bmc
from bmc_converter.bmc_reader import estimate_point_count
from bmc_converter.export_util import (
    export_header_txt,
    export_points_csv,
    export_preview_png,
)
from bmc_converter.gcode_writer import write_gcode


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Convert AGAT/CNConv .BMC engraving files to generic G-code.",
    )
    p.add_argument("--version", action="version", version=f"bmc_converter {__version__}")
    p.add_argument("input", type=Path, help="Path to .BMC file")

    sub = p.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="Show BMC header summary")
    info.add_argument("-o", "--out", type=Path, help="Write header text to file")

    dump = sub.add_parser("dump", help="Export extracted points to CSV")
    dump.add_argument("-o", "--out", type=Path, required=True)
    dump.add_argument("--max", type=int, default=50_000, help="Max rows (default 50000)")
    dump.add_argument(
        "--all-records",
        action="store_true",
        help="Include path markers (cmd 2/4), not only strikes",
    )

    prev = sub.add_parser("preview", help="Render a PNG preview from strike points")
    prev.add_argument("-o", "--out", type=Path, required=True)
    prev.add_argument("--scale", type=int, default=8)
    prev.add_argument("--sample-every", type=int, default=20)

    gcode = sub.add_parser("gcode", help="Convert BMC to .nc / .gcode")
    gcode.add_argument("-o", "--out", type=Path, required=True)
    gcode.add_argument(
        "--controller",
        choices=["generic", "mach3", "grbl", "linuxcnc"],
        default="generic",
    )
    gcode.add_argument(
        "--impact",
        choices=["hammer", "z_pulse", "dwell", "m7_m9", "m3_m5", "m8_m9"],
        default="hammer",
        help="How to fire the diamond impact (default: separate hammer axis tap)",
    )
    gcode.add_argument(
        "--impact-axis",
        default="A",
        help="Hammer axis letter (A/B/C/U/…). Not X/Y/Z. Default A",
    )
    gcode.add_argument("--dwell", type=float, default=0.01, help="Base dwell seconds")
    gcode.add_argument("--pitch", type=float, help="mm per pixel (overrides size)")
    gcode.add_argument("--width-mm", type=float, help="Target engraving width in mm")
    gcode.add_argument("--height-mm", type=float, help="Target engraving height in mm")
    gcode.add_argument("--feed", type=float, default=3000.0, help="XY feed (G1 mode)")
    gcode.add_argument(
        "--no-rapid",
        action="store_true",
        help="Use G1 instead of G0 between hits",
    )
    gcode.add_argument(
        "--no-flip-y",
        action="store_true",
        help="Do not flip Y (keep image Y-down)",
    )
    gcode.add_argument(
        "--min-intensity",
        type=int,
        default=1,
        help="Skip strikes weaker than this (1–100)",
    )
    gcode.add_argument(
        "--max-strikes",
        type=int,
        default=None,
        help="Limit output strikes (useful for simulator tests)",
    )
    gcode.add_argument(
        "--stride",
        type=int,
        default=1,
        help="Keep every Nth strike (e.g. 50 = full-image preview, smaller file)",
    )
    gcode.add_argument(
        "--z-safe",
        type=float,
        default=0.0,
        help="Hammer retract position on impact axis (mm)",
    )
    gcode.add_argument(
        "--z-impact",
        type=float,
        default=-0.3,
        help="Hammer tap position on impact axis (mm), default -0.3",
    )

    return p


def cmd_info(args: argparse.Namespace) -> int:
    with open_bmc(args.input) as bmc:
        h = bmc.header
        est = estimate_point_count(h)
        print(f"File:        {bmc.path}")
        print(f"Magic:       {h.magic!r}")
        print(f"Version:     {h.version}")
        print(f"Size:        {h.width} x {h.height} px")
        print(f"Scale units: {h.scale_x} x {h.scale_y}")
        print(f"Hard hits:   {h.hard_hit_count:,}")
        print(f"Record size: {h.record_size}")
        print(f"Data offset: 0x{h.data_offset:X}")
        print(f"Est. records:{est:,}")
        print(f"File bytes:  {h.file_size:,}")
        if args.out:
            export_header_txt(h, args.out)
            print(f"Wrote {args.out}")
    return 0


def cmd_dump(args: argparse.Namespace) -> int:
    with open_bmc(args.input) as bmc:
        n = export_points_csv(
            bmc,
            args.out,
            max_points=args.max,
            strikes_only=not args.all_records,
        )
        print(f"Wrote {n:,} rows -> {args.out}")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    with open_bmc(args.input) as bmc:
        export_preview_png(
            bmc,
            args.out,
            scale=args.scale,
            sample_every=args.sample_every,
        )
        print(f"Wrote preview -> {args.out}")
    return 0


def cmd_gcode(args: argparse.Namespace) -> int:
    opt = GcodeOptions(
        pitch_mm=args.pitch,
        width_mm=args.width_mm,
        height_mm=args.height_mm,
        flip_y=not args.no_flip_y,
        feed_xy=args.feed,
        rapid=not args.no_rapid,
        impact_mode=args.impact,
        impact_axis=args.impact_axis,
        dwell_s=args.dwell,
        min_intensity=args.min_intensity,
        max_strikes=args.max_strikes,
        stride=args.stride,
        controller=args.controller,
        z_safe=args.z_safe,
        z_impact=args.z_impact,
    )
    with open_bmc(args.input) as bmc:
        print(
            f"Converting {bmc.header.width}x{bmc.header.height} "
            f"(est. {estimate_point_count(bmc.header):,} records)..."
        )
        if opt.max_strikes is None and opt.stride <= 1:
            print(
                "Note: full convert of ~38M hits yields a very large .nc file. "
                "Use --max-strikes or --stride for simulator tests."
            )
        with args.out.open("w", encoding="utf-8", newline="\n") as out:
            stats = write_gcode(bmc.iter_points(), bmc.header, out, opt)
    print(f"Strikes written: {stats['strikes']:,}")
    print(f"Skipped:         {stats['skipped']:,}")
    print(
        f"Work envelope:   {stats['width_mm']:.2f} x {stats['height_mm']:.2f} mm"
    )
    print(f"Output:          {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Allow running as `python -m bmc_converter` from project parent
    commands = {
        "info": cmd_info,
        "dump": cmd_dump,
        "preview": cmd_preview,
        "gcode": cmd_gcode,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    # Ensure project root is on sys.path when run as a file
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    raise SystemExit(main())
