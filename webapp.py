"""Web UI: upload .BMC → convert → download .nc G-code."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from bmc_converter import GcodeOptions, open_bmc
from bmc_converter.bmc_reader import estimate_point_count, validate_bmc_file
from bmc_converter.gcode_writer import write_gcode

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB

BASE_DIR = Path(__file__).resolve().parent


def _resolve_work_dir() -> Path:
    """Prefer WORK_DIR env (Render disk). Fall back if mount is missing."""
    candidates = []
    env_dir = os.environ.get("WORK_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path("/tmp/bmc_web_work"))
    candidates.append(BASE_DIR / "web_work")

    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return path
        except OSError:
            continue
    # Last resort: /tmp always exists on Render
    fallback = Path("/tmp/bmc_web_work")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


WORK_DIR = _resolve_work_dir()

# job_id -> {status, message, nc_path, created, ...}
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^\w\-]+", "_", stem).strip("_")
    return stem[:80] or "output"


def _cleanup_old_jobs(max_age_s: int = 3600) -> None:
    now = time.time()
    with JOBS_LOCK:
        old = [jid for jid, j in JOBS.items() if now - j.get("created", now) > max_age_s]
        for jid in old:
            job = JOBS.pop(jid, None)
            if not job:
                continue
            folder = job.get("folder")
            if folder and Path(folder).exists():
                shutil.rmtree(folder, ignore_errors=True)


def _run_conversion(job_id: str, bmc_path: Path, options: dict) -> None:
    job_folder = Path(JOBS[job_id]["folder"])
    nc_name = f"{_safe_stem(bmc_path.name)}.nc"
    nc_path = job_folder / nc_name

    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
            JOBS[job_id]["message"] = "Reading BMC and writing G-code…"

        opt = GcodeOptions(
            width_mm=options.get("width_mm"),
            height_mm=options.get("height_mm"),
            pitch_mm=options.get("pitch_mm"),
            impact_mode=options["impact"],
            impact_axis=options.get("impact_axis", "A"),
            dwell_s=options["dwell"],
            controller=options["controller"],
            max_strikes=options.get("max_strikes"),
            stride=options.get("stride") or 1,
            flip_y=options.get("flip_y", True),
            min_intensity=options.get("min_intensity", 1),
            z_impact=options.get("z_impact", -0.3),
            z_safe=options.get("z_safe", 0.0),
        )

        with open_bmc(bmc_path) as bmc:
            est = estimate_point_count(bmc.header)
            with JOBS_LOCK:
                JOBS[job_id]["message"] = (
                    f"Converting {bmc.header.width}x{bmc.header.height} "
                    f"(~{est:,} records)…"
                )
            with nc_path.open("w", encoding="utf-8", newline="\n") as out:
                stats = write_gcode(bmc.iter_points(), bmc.header, out, opt)

        with JOBS_LOCK:
            JOBS[job_id].update(
                {
                    "status": "done",
                    "message": (
                        f"Done — {stats['strikes']:,} strikes, "
                        f"{stats['width_mm']:.1f} x {stats['height_mm']:.1f} mm"
                    ),
                    "nc_path": str(nc_path),
                    "nc_name": nc_name,
                    "stats": stats,
                }
            )
    except Exception as exc:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["message"] = str(exc)


@app.get("/")
def index():
    _cleanup_old_jobs()
    return render_template("index.html")


@app.post("/convert")
def convert():
    _cleanup_old_jobs()
    upload = request.files.get("bmc_file")
    if not upload or not upload.filename:
        flash("Please choose a .BMC file.", "error")
        return redirect(url_for("index"))

    filename = upload.filename
    if not filename.lower().endswith(".bmc"):
        flash("File must have a .BMC extension.", "error")
        return redirect(url_for("index"))

    try:
        width_mm = float(request.form.get("width_mm") or 100)
        height_raw = (request.form.get("height_mm") or "").strip()
        height_mm = float(height_raw) if height_raw else None
        dwell = float(request.form.get("dwell") or 0.01)
        stride = int(request.form.get("stride") or 1)
        max_raw = (request.form.get("max_strikes") or "").strip()
        max_strikes = int(max_raw) if max_raw else None
        min_intensity = int(request.form.get("min_intensity") or 1)
    except ValueError:
        flash("Invalid number in options.", "error")
        return redirect(url_for("index"))

    if stride < 1:
        stride = 1
    if width_mm <= 0:
        flash("Width must be positive.", "error")
        return redirect(url_for("index"))

    impact = request.form.get("impact") or "hammer"
    controller = request.form.get("controller") or "generic"
    if impact not in {"dwell", "m7_m9", "m3_m5", "m8_m9", "z_pulse", "hammer"}:
        impact = "hammer"
    if controller not in {"generic", "mach3", "grbl", "linuxcnc"}:
        controller = "generic"

    impact_axis = (request.form.get("impact_axis") or "A").strip().upper()[:1] or "A"

    try:
        z_impact = float(request.form.get("z_impact") or -0.3)
        z_safe = float(request.form.get("z_safe") or 0.0)
    except ValueError:
        z_impact, z_safe = -0.3, 0.0

    mode = request.form.get("mode") or "preview"
    if mode == "preview":
        stride = max(stride, 80)
        max_strikes = None
    elif mode == "sample":
        stride = 1
        max_strikes = max_strikes or 5000
    # mode == "full" keeps form stride / max_strikes (often stride=1)

    job_id = secrets.token_hex(8)
    job_folder = WORK_DIR / job_id
    job_folder.mkdir(parents=True, exist_ok=True)
    bmc_path = job_folder / Path(filename).name
    upload.save(bmc_path)

    ok, msg = validate_bmc_file(bmc_path)
    if not ok:
        shutil.rmtree(job_folder, ignore_errors=True)
        flash(msg, "error")
        return redirect(url_for("index"))

    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "message": msg,
            "created": time.time(),
            "folder": str(job_folder),
            "nc_path": None,
            "nc_name": None,
        }

    options = {
        "width_mm": width_mm,
        "height_mm": height_mm,
        "pitch_mm": None,
        "impact": impact,
        "impact_axis": impact_axis,
        "dwell": dwell,
        "controller": controller,
        "max_strikes": max_strikes,
        "stride": stride,
        "flip_y": True,
        "min_intensity": min_intensity,
        "z_impact": z_impact,
        "z_safe": z_safe,
    }

    thread = threading.Thread(
        target=_run_conversion,
        args=(job_id, bmc_path, options),
        daemon=True,
    )
    thread.start()
    return redirect(url_for("job_status", job_id=job_id))


@app.get("/job/<job_id>")
def job_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        flash("Job not found or expired.", "error")
        return redirect(url_for("index"))
    return render_template("job.html", job_id=job_id, job=job)


@app.get("/job/<job_id>/json")
def job_json(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return {"status": "missing", "message": "Job not found"}, 404
    return {
        "status": job["status"],
        "message": job["message"],
        "nc_name": job.get("nc_name"),
        "download_url": (
            url_for("download", job_id=job_id) if job["status"] == "done" else None
        ),
    }


@app.get("/download/<job_id>")
def download(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job.get("status") != "done" or not job.get("nc_path"):
        flash("File not ready.", "error")
        return redirect(url_for("index"))
    path = Path(job["nc_path"])
    if not path.is_file():
        flash("Output file missing.", "error")
        return redirect(url_for("index"))
    return send_file(
        path,
        as_attachment=True,
        download_name=job.get("nc_name") or path.name,
        mimetype="text/plain",
    )


def main():
    import os

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print("BMC Converter Web UI")
    print(f"Open http://{host}:{port} in your browser")
    if host == "127.0.0.1":
        print("(Local only. Set HOST=0.0.0.0 to allow LAN access.)")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
