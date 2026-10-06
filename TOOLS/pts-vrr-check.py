#!/usr/bin/env python3
"""Check PTS submission targets, not physical VRR/scanout.

Uses the built mpv and FFmpeg's lavfi input; saves all traces for inspection.
The null backend tests the common scheduler. waylandvk additionally exercises
real gpu-next submission and deliberately requests the extra callback wait.
"""

import argparse
import pathlib
import re
import statistics
import subprocess
import tempfile


FLIP = re.compile(
    r"PTS flip: id=(\d+) media=([\d.+-]+) target=(\d+) start=(\d+) "
    r"late=(-?\d+) repeat=(\d+) redraw=(\d+) still=(\d+)"
)


def check(mpv, backend, output, name, graph, speed, expected_frames):
    logfile = output / (name + ".log")
    cmd = [
        str(mpv), "--no-config", "--no-terminal", "--load-scripts=no",
        "--audio=no", "--osd-level=0", "--interpolation=no", "--framedrop=no",
        "--video-sync=pts", "--display-fps-override=144",
        "--speed=" + str(speed), "--msg-level=vo=trace",
        "--log-file=" + str(logfile),
    ]
    if backend == "null":
        cmd += ["--vo=null"]
    else:
        cmd += ["--vo=gpu-next", "--gpu-api=vulkan", "--gpu-context=waylandvk",
                "--wayland-internal-vsync=yes"]
    cmd.append("av://lavfi:" + graph)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(f"{name}: mpv exited {result.returncode}; see {logfile}\n"
                           + result.stderr)
    text = logfile.read_text(encoding="utf-8", errors="replace")
    frames = [tuple(float(x) if i == 1 else int(x)
                    for i, x in enumerate(m.groups()))
              for m in FLIP.finditer(text)]
    if len(frames) != expected_frames:
        raise AssertionError(f"{name}: expected {expected_frames} normal submissions, "
                             f"got {len(frames)}; see {logfile}")
    if any(f[5] or f[6] for f in frames):
        raise AssertionError(f"{name}: unexpected repeat/redraw in normal frames")
    if "PTS drop:" in text:
        raise AssertionError(f"{name}: dropped a frame with framedrop disabled")
    # Startup uses an immediate first frame. Ignore the initial synchronization
    # phase when comparing successive PTS and monotonic-clock differences.
    errors = []
    for prev, cur in zip(frames[3:-1], frames[4:]):
        pts_delta = (cur[1] - prev[1]) / speed
        target_delta = (cur[2] - prev[2]) / 1e9
        if pts_delta <= 0 or target_delta <= 0:
            raise AssertionError(f"{name}: non-monotonic PTS or submission target")
        errors.append(abs(target_delta - pts_delta))
    if not errors or max(errors) > 0.003:
        raise AssertionError(f"{name}: PTS/target delta error exceeds 3 ms: {errors}")
    # End-to-end phase error would expose a scheduler running at a different
    # fixed refresh cadence even when per-frame errors happen to be small.
    first, last = frames[3], frames[-1]
    phase_error = abs((last[2] - first[2]) / 1e9 -
                      (last[1] - first[1]) / speed)
    if phase_error > 0.003:
        raise AssertionError(f"{name}: accumulated target phase error {phase_error}s")
    lateness = [f[4] / 1e6 for f in frames[3:]]
    print(f"PASS {name}: {len(frames)} frames, max target delta error "
          f"{max(errors) * 1000:.3f} ms, phase error {phase_error * 1000:.3f} ms, "
          f"median flip lateness {statistics.median(lateness):.3f} ms")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mpv", required=True, type=pathlib.Path)
    parser.add_argument("--backend", choices=["null", "waylandvk"], default="null")
    parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path("build-pts-vrr"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    output = pathlib.Path(tempfile.mkdtemp(prefix="pts-check-", dir=args.output.resolve()))
    mpv = args.mpv.resolve(strict=True)
    print(f"Backend: {args.backend}; traces: {output}")
    cases = [
        ("cfr-23976", "testsrc2=size=160x90:rate=24000/1001:duration=1", 1, 24),
        ("cfr-24-half-speed", "testsrc2=size=160x90:rate=24:duration=1", 0.5, 24),
        ("cfr-60-double-speed", "testsrc2=size=160x90:rate=60:duration=1", 2, 60),
        ("vfr-60-40", "testsrc2=size=160x90:rate=120:duration=1,"
         "select='if(lt(t,0.5),not(mod(n,2)),not(mod(n,3)))'", 1, 50),
    ]
    for case in cases:
        check(mpv, args.backend, output, *case)
    print("Submission-target checks passed. Physical VRR and A/V sync still need "
          "separate hardware/audio tests.")


if __name__ == "__main__":
    main()
