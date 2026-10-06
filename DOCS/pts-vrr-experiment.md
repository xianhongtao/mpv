# PTS-paced VRR experiment

Branch: `experiment/pts-vrr`. Initial target: Linux Wayland, Vulkan, `vo=gpu-next`.

## What changes

`--video-sync=pts` uses mpv's existing audio-clock video scheduler (the monotonic
system clock when audio is absent). Media PTS are mapped onto monotonic target
times. The VO renders ahead using `video-timing-offset`, waits until the target,
and calls `flip_page`. With gpu-next, libplacebo's frame submission happens there,
not during early rendering. Video is not quantized onto fixed display refresh
intervals, and playback speed is not corrected to fit the display.

With gpu-next, the Wayland Vulkan backend skips its additional frame-callback
wait in this mode, including when `wayland-internal-vsync` explicitly requests
that wait. This decision uses both the current video-sync option and the timing
mode of the actual submitted frame. Retained display-sync frames keep their
original pacing during mode transitions. Other VOs keep their existing waits.
Legacy compositors without a suspended-state event retain callback-timeout
visibility detection using nonblocking event polling and elapsed time (three
callback timeout intervals), rather than waiting on every submission. It does **not** remove Vulkan resource synchronization, event
processing, visibility handling, or presentation feedback. The existing safe
FIFO/MAILBOX default selection stays intact (including MAILBOX fallback for
compositors lacking FIFO/presentation-v2); user swap-mode overrides still apply.
No swapchain recreation is required by a video-sync change.

The default remains `audio`. Other video-sync modes are unchanged. On the target
backend, `pts` differs from `audio` specifically by skipping this additional
callback wait. If `audio` already uses FIFO without that wait, the two may behave
identically. This is intentional: ordinary audio-sync already schedules by PTS.

## What this does not prove

This is a submission-pacing experiment, not a VRR enablement protocol. Enable
adaptive sync in the compositor/driver and monitor separately. Fullscreen/direct
scanout requirements depend on the compositor. FIFO is still allowed to exert
queue backpressure; fixed-refresh displays can still quantize physical scanout.
MAILBOX may replace queued frames under load. Do not infer VRR from the number
of mpv submissions, the selected video-sync mode, or `display-sync-active=no`.

Only gpu-next/waylandvk is the initial target. In particular, the older `vo=gpu`
may call a presenting submit function while drawing, before the VO's target-time
wait. Other contexts are unverified; warnings identify them. Use
`--interpolation=no` and do not use `--untimed`. A non-display-sync frame cannot
use display-sync interpolation. Normal OSD/window redraws may still submit
outside the video timeline and are separately marked in trace logs.

Missing or discontinuous PTS, startup, seeks, pauses, frame stepping, speed
changes, EOF and audio drift follow existing audio-sync behavior. Late frames
follow existing framedrop configuration. This experiment adds no periodic
software repeats or software low-framerate compensation (LFC). Below the VRR
range, LFC is up to the driver/display; above it, queueing and frame loss remain
possible. Audio passthrough is not deliberately retimed.

## Build and run

```sh
meson setup build-pts-vrr -Dtests=true -Dlibmpv=true \
    -Dwayland=enabled -Dvulkan=enabled
meson compile -C build-pts-vrr
env -u LANGUAGE -u LC_ALL -u LC_MESSAGES \
    meson test -C build-pts-vrr --print-errorlogs
python3 TOOLS/pts-vrr-check.py --mpv build-pts-vrr/mpv
# Requires a working Wayland display and Vulkan device; opens short test windows:
python3 TOOLS/pts-vrr-check.py --mpv build-pts-vrr/mpv --backend waylandvk

build-pts-vrr/mpv --no-config --vo=gpu-next --gpu-api=vulkan \
    --gpu-context=waylandvk --video-sync=pts --interpolation=no VIDEO
```

Use an existing movie or generate deterministic test material:

```sh
ffmpeg -f lavfi -i 'testsrc2=size=640x360:rate=24000/1001:duration=20' \
    -f lavfi -i 'sine=frequency=440:sample_rate=48000:duration=20' \
    -c:v ffv1 -c:a pcm_s16le -shortest cfr-23976.mkv
ffmpeg -f lavfi -i 'testsrc2=size=640x360:rate=120:duration=20' \
    -vf "select='if(lt(t,10),not(mod(n,2)),not(mod(n,3)))'" \
    -fps_mode vfr -c:v ffv1 vfr-60-40.mkv
```

Repeat with 24/60 fps and a rate inside the monitor's actual VRR range. Test
23.976 fps both with and without audio; it is often below the native VRR minimum.
Matroska timestamp rounding can introduce small PTS variation even in CFR files.
For controls, replace `pts` with `audio` and `display-resample`, leaving all other
settings unchanged. To exercise the extra callback wait specifically, compare
`audio` and `pts` with `--wayland-internal-vsync=yes` (the normal default path may
already omit that wait). Do not force immediate/tearing presentation.

## Diagnostics

```sh
build-pts-vrr/mpv --no-config --vo=gpu-next --gpu-api=vulkan \
    --gpu-context=waylandvk --video-sync=pts --interpolation=no \
    --msg-level=vo=trace --log-file=pts-vrr.log VIDEO
```

Trace entries:

- `PTS flip`: frame id, media PTS in seconds, monotonic target/start/late in
  nanoseconds, and repeat/redraw/still flags. `start` is sampled immediately
  before the flip trace and call, not a GPU completion or scanout timestamp.
  Negative late values can occur during live resize where the existing VO wait
  is intentionally bypassed. A still flag includes frame stepping.
- `PTS flip-return`: return time and duration of the whole flip call in
  nanoseconds. Includes libplacebo/driver queue throttling, not just GPU work.
- `PTS feedback`: backend display-time feedback **before** mpv's fallback
  timestamp is generated. A nonpositive display time means unknown. Feedback
  may describe an older queued frame, so the accompanying id is the current
  submission, not a presentation-feedback correlation identifier.
- `PTS drop`: a scheduled frame was dropped before drawing/submission.
- `PTS redraw`: an OSD/window redraw outside normal PTS-paced playback.
- Existing stats gain `pts-submit-late` in seconds.

Compare consecutive normal-frame targets with differences in media PTS divided
by playback speed. They should not be quantized to a fixed monitor interval;
audio-clock correction can legitimately move the targets. Trace output itself
adds overhead; use short captures and disable it for smoothness comparisons.
Actual VRR needs **both** presentation observations and monitor/compositor VRR
telemetry. Submissions alone cannot establish it.

## Validation checklist

1. Run the full Meson test suite. The libmpv options test covers initialization
   with `pts`, runtime roundtrips through all existing modes, and invalid values.
   If locale selection tests fail, remove inherited `LANGUAGE`/`LC_*` overrides
   as in the build example; these tests deliberately set `LANG=C`.
   `TOOLS/pts-vrr-check.py` additionally checks CFR/VFR target differences,
   accumulated phase error, frame counts and 0.5x/2x speed scaling with a forced
   144 Hz display hint. It does not check physical presentation timestamps.
2. With/without audio and with passthrough: CFR 23.976/24/60, VFR, 0.5x/2x,
   seek, pause/resume, frame stepping and EOF. Check A/V drift and frame counts.
3. Switch `audio` -> `pts` -> `display-resample` -> `pts` via IPC/console during
   playback; verify display-sync state and speed corrections recover normally.
   `TOOLS/pts-vrr-smoke.lua` automates active mode switching, pause/step/resume,
   seek, speed restoration, 2x playback and EOF on a seekable clip >=3 seconds:

   ```sh
   build-pts-vrr/mpv --no-config --osc=no --video-sync=pts --interpolation=no \
       --script=TOOLS/pts-vrr-smoke.lua --vo=null --vo-null-fps=144 --ao=null VIDEO
   ```

   This simulates a fixed-refresh display and an audio clock. Use `--audio=no`
   to cover the no-audio path. For actual backend checks replace the null VO
   with `--vo=gpu-next --gpu-api=vulkan --gpu-context=waylandvk` and set
   `--wayland-internal-vsync=yes`. Hidden-window/queue behavior still requires
   manual testing on the relevant compositor.
4. Resize, fullscreen, hide/minimize/restore, close while hidden. Check for hangs,
   busy loops and unbounded queues. Use software rendering/expensive shaders to
   exercise late-frame dropping and decoder starvation.
5. Test FIFO default, fallback MAILBOX, and explicit non-tearing swap modes on
   capable compositors. Preserve a fixed-refresh control test.
6. On a real VRR monitor, verify actual refresh behavior with compositor/monitor
   indicators. Record compositor, driver, GPU, display range, presentation mode,
   direct-scanout state, and whether LFC is active.

Build/automated results and any incomplete manual checks must be reported
separately; successful compilation does not establish hardware VRR support.

## Initial validation results

- Built successfully with GCC 16.2.1, Meson 1.12.1, libplacebo 7.360.1,
  FFmpeg 9.0.2, Wayland 1.26.0, wayland-protocols 1.49 and Vulkan headers 1.4.363.
- All 37 enabled Meson tests passed after clearing inherited locale overrides.
  FFmpeg image-reference suites do not yet have reference data for FFmpeg 9 in
  this checkout and were therefore not enabled by the existing build logic.
- Null-backend PTS-target checks passed for CFR 23.976, 24 fps at 0.5x,
  60 fps at 2x, and VFR 60/40 fps. Max per-frame target delta error was below
  0.003 ms in this run. This measures scheduling targets, not physical scanout.
- Active playback smoke tests passed with a simulated 144 Hz null VO, both
  without audio and with the null audio clock, including display-sync activation
  and exit, speed-correction reset, pause, frame-step, seek, 2x playback and EOF.
- Wayland connection succeeded, but actual Vulkan backend playback could not
  initialize a physical device: `vkEnumeratePhysicalDevices` returned
  `VK_ERROR_INITIALIZATION_FAILED`. No hardware VRR, real GPU queue pacing,
  legacy-compositor hidden-window behavior or real audio-device drift has been
  established by this run. Re-run the Wayland checks on a working Vulkan device
  and the manual checklist above before calling this hardware VRR support.
