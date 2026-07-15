BMC → G-code Converter
======================

Converts AGAT / CNConv `.BMC` diamond-impact engraving files into generic
G-code (`.nc` / `.gcode`) for Mach3, GRBL, or LinuxCNC.


Requirements
------------
- Python 3.10+
- `pip install -r requirements.txt`


Web application (upload / convert / download)
---------------------------------------------
Easiest for non-technical users:

```bash
pip install -r requirements.txt
python webapp.py
```

Or double-click `run_webapp.bat` on Windows.

Then open **http://127.0.0.1:5000** in a browser:

1. Upload a `.BMC` file  
2. Choose Preview / Sample / Full quality  
3. Set width (mm) and impact mode  
4. Click **Convert to G-code**  
5. Download the `.nc` when ready  

Temporary files are stored under `web_work/` and cleaned up after about one hour.


Command-line quick start
------------------------
From the project folder:

```bash
python -m bmc_converter "2 copy.bmc" info
python -m bmc_converter "2 copy.bmc" dump -o points_sample.csv --max 20000
python -m bmc_converter "2 copy.bmc" preview -o preview.png
python -m bmc_converter "2 copy.bmc" gcode -o sample.nc --max-strikes 20000 --width-mm 100
python -m bmc_converter "2 copy.bmc" gcode -o preview_toolpath.nc --stride 80 --width-mm 100
```

Full conversion (very large output — tens of millions of lines):

```bash
python -m bmc_converter "2 copy.bmc" gcode -o full.nc --width-mm 100 --impact dwell
```


Commands
--------

### info
Print BMC header (size, version, hard-hit count).

### dump
Export extracted points to CSV (`index,cmd,intensity,x,y,param,file_offset`).

### preview
Rebuild a grayscale PNG from strike points (quality check).

### gcode
Write `.nc` / `.gcode`.

Useful options:

| Option | Meaning |
|--------|---------|
| `--width-mm` / `--height-mm` | Physical size of the engraving |
| `--pitch` | mm per pixel (overrides width/height) |
| `--impact` | `dwell` (default), `m7_m9`, `m3_m5`, `m8_m9`, `z_pulse` |
| `--dwell` | Base impact dwell in seconds |
| `--controller` | `generic`, `mach3`, `grbl`, `linuxcnc` |
| `--max-strikes` | Truncate for simulator tests |
| `--stride` | Keep every Nth strike (full portrait, smaller file) |
| `--min-intensity` | Skip weak hits (1–100) |
| `--no-flip-y` | Keep image Y-down |


Impact modes
------------
Diamond engravers need a digital output to fire the tip. Map that to G-code:

- **dwell** — `G4 P…` only (safe to view in any simulator; no I/O)
- **m7_m9** — mist coolant relay as solenoid pulse
- **m8_m9** — flood coolant relay
- **m3_m5** — spindle on/off as pulse
- **z_pulse** — brief Z dive (mechanical proxy if no solenoid pin)

Wire your impact driver to the matching Mach3/GRBL/LinuxCNC output.


BMC format (summary)
--------------------
- Magic `ECN\xAC`, uncompressed, little-endian
- Header holds width, height, scale, counts
- From offset `0xBA`: 8-byte records  
  `cmd | intensity(0–100) | X | Y | param`
- `cmd` 1 / 14 = strikes; 2 = run start; 4 = segment marker


Simulator test
--------------
1. Small clip (top of image):  
   `python -m bmc_converter "2 copy.bmc" gcode -o sample.nc --max-strikes 5000 --width-mm 100`
2. Full portrait, thinned for viewers:  
   `python -m bmc_converter "2 copy.bmc" gcode -o preview_toolpath.nc --stride 80 --width-mm 100`
3. Open the `.nc` in CAMotics, [ncviewer.com](https://ncviewer.com), Mach3 toolpath, or LinuxCNC preview.
4. Expect a silhouette point cloud matching `bmc_preview.png` / PNG preview.


Notes
-----
- Full jobs preserve one G-code impact per BMC strike (photo detail).
- Output size scales with strike count (~tens of millions → multi‑GB files).
- Physical scale: prefer `--width-mm` for your stone/plate size.
- Header `scale_x/scale_y` (e.g. 9842) are treated as 0.01 mm workspace units when no size is given.
