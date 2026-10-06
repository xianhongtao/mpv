# Investigation: 25 Hz mpv submissions versus KWin/DRM at ~60 Hz

**Second matched hardware results:** mpv and native Wayland VRRTest both track
40 Hz with active-CRTC VRR_ENABLED=1. The captured panel EDID declares a 30–60 Hz
vertical range, used by KWin as its VRR minimum. Native VRRTest25 now measures
37.5 Hz, not the first run's 60 Hz; those runs must not be conflated. Primary
framebuffer snapshots show AB4H rather than either client's original format.
See the [40 Hz verification and below-range findings](<wayland-kwin-vrr-results-40hz.md>).

**First matched hardware results are now available:** native Wayland VRRTest at
fixed 25 FPS also measures ~60 Hz, with both stationary and moving cursor.
Neither client makes extra steady-state callback-only commits. Raw mpv feedback
arrives; an independently reproduced present-sync cache backlog explains the
converted `display_time=-1`. See the [hardware results and next discriminating
test](<wayland-kwin-vrr-results-25hz.md>). The source hypotheses below should not
be mistaken for a confirmed physical-output root cause.

## Scope and evidence status

The PTS scheduler is deliberately unchanged. No frame repetition, extra surface
commit, callback wait, present-mode override, VRR policy or compositor setting
is introduced by these diagnostics.

User hardware observations:

- 25 fps CFR media PTS, submission targets and `flip_page()` calls are ~40 ms
  apart; lateness ~0.06–0.15 ms.
- `vkQueuePresentKHR` returns after ~0.2–0.4 ms; client-side FIFO backpressure
  does not explain the measured discrepancy.
- DRM `VRR_ENABLED=1`, but vbltest reports ~60 Hz during mpv playback.
- VRRTest on the same system tracks 30–60 Hz and engages LFC.
- mpv's converted `display_time` remains -1.

Local facts independently collected:

- KWin 6.7.5-1; output eDP-1, nominal 60.003 Hz, 1920x1080, scale 1,
  adaptive sync Automatic; Intel Tiger Lake / Mesa OpenGL compositor.
- The provided video is 854x450, exactly 25/1 fps, duration 683.04 s.
- VRRTest 2.1.1-2 uses LÖVE 11.5, SDL2-compat 2.32.74 and SDL3 3.4.18.
- The harness can connect to the real Wayland socket and query KWin via DBus,
  but has **no /dev/dri**. Its Vulkan loader cannot enumerate a GPU. Thus its
  capture reaches initial surface configuration only, not video buffer commits.
  Hardware reproduction, missing-feedback origin and actual repaint source
  cannot honestly be claimed as established here.
- Diagnostic additions compiled successfully; all 38 enabled Meson tests passed,
  including four offline analyzer cases (new/old trace formats, callbacks,
  feedback discard/cancellation, midnight rollover). The supplied video played
  through the null VO for three seconds with steady 40 ms targets; this is a
  scheduler regression check, not a physical VRR test.

## Most important KWin source findings

Research used upstream **v6.7.5**, matching the installed version; downstream
patches have not been ruled out.

### A real 30 Hz boundary — not the VRR enable threshold

[RenderLoop::activeWindowControlsVrrRefreshRate()](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/core/renderloop.cpp#L277)
requires the active window to overlap the output, have a surface item, and have
estimated damage intervals <= 1/30 s. Estimation comes from surface damage,
not PTS or nominal media FPS. Recursive estimation includes faster children.

[RenderLoop::scheduleRepaint()](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/core/renderloop.cpp#L256)
uses that result to delay repaints from other items/output layers, giving the
active application's cadence priority. At stable 25 Hz this protection does
not apply; at stable >=30 Hz it can apply. This is a concrete distinction between
the reported mpv test and the reported VRRTest range, **not proof by itself that
KWin schedules a continuous 60 Hz repaint stream**.

[Cursor-plane scheduling](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/compositor.cpp#L586)
also grants a VRR cursor-update delay only when that predicate passes. At 25 Hz,
moving the hardware cursor can independently trigger output updates without
altering mpv's surface cadence. Other windows, notifications, effects, overlays
and compositor animations are additional sources. The next decisive test is
stationary/hidden cursor versus deliberate motion, at matched 25 and 30 Hz.

### VRR enablement and direct scanout are separate

[Compositor adaptive presentation selection](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/compositor.cpp#L712)
follows output capability/policy and active-window preference. Under Automatic,
[Window::wantsAdaptiveSync()](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/window.cpp#L4228)
defaults to fullscreen, with window-rule overrides. The 30 Hz predicate does
not disable VRR here. Direct scanout is not a prerequisite, and the video's
content-type hint is not itself this policy's VRR enable switch.

The [scanout candidate heuristic](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/scene/workspacescene.cpp#L250)
uses a distinct "updates quickly" test (>20 Hz); 25 Hz meets it. Scanout also
requires appropriate opacity, dmabuf format/modifier/import, transform/color
pipeline and plane eligibility. Effects or additional surfaces can defeat it.
See [scanout preparation](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/compositor.cpp#L357)
and [DRM EGL layer checks](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/backends/drm/drm_egl_layer.cpp#L85).

### Presentation feedback has important KWin limitations

[KWin's feedback implementation](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/wayland/presentationtime.cpp#L67)
sets HW_CLOCK/HW_COMPLETION and optionally VSYNC, but **never ZERO_COPY**, even
for direct scanout. The old mpv debug message equating absent ZERO_COPY with
"a copy" was misleading; the diagnostic wording now explicitly warns otherwise.

For adaptive sync, presentation v1 refresh duration is zero; v2 gets a supplied
nominal refresh duration, not instantaneous measured physical refresh. Sequence
is zero in this implementation. None of these values should be converted into
an instantaneous VRR frequency or used to conclude that a zero sequence means
feedback did not arrive.

A raw `presented` event with a valid timestamp is distinct from mpv's legacy
converted `display_time`. New diagnostics preserve that distinction; the
estimator is deliberately not changed.

### Callback-only commits can request compositor work

[KWin SurfaceItemWayland::handleSurfaceCommitted()](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/scene/surfaceitem_wayland.cpp#L115)
schedules a frame for callbacks, FIFO barriers or presentation feedback even
without new damage. Therefore a 25 Hz `vkQueuePresentKHR` log alone is not a
complete count of all surface commits. Additional callback/feedback-only WSI
commits, metadata commits or other surface updates need protocol-level counting.

mpv rearms its callback and requests feedback from inside `frame_callback()`;
those requests are pending until the corresponding surface commit. This code
itself does not commit an extra frame. Vulkan WSI/EGL protocol behavior must be
measured rather than inferred from the number of application presents.

### Below-range VRR/LFC is still a separate branch

[Adaptive render-loop scheduling](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/core/renderloop.cpp#L100)
has an explicit minimum-refresh TODO. The inspected path does not generate a
software LFC repeat stream. `VRR_ENABLED=1` does not prove physical tracking at
25 Hz. VRRTest success at 30–60 Hz and LFC does not establish its behavior at
**fixed 25 Hz**, with identical fullscreen/focus/backend/cursor conditions.
Do not "fix" this investigation by submitting duplicate video frames.

## VRRTest versus mpv: comparisons that must be matched

Installed VRRTest clears/draws a full background and presents once per timed
iteration. It defaults to desktop fullscreen, borderless, vsync=0. It is an
OpenGL application, not Vulkan. LÖVE requests RGBA, but SDL3 marks an ordinary
nontransparent window opaque; RGBA alone is not an alpha-window difference.

The installed SDL3 [backend selection](https://raw.githubusercontent.com/libsdl-org/SDL/release-3.4.18/src/video/wayland/SDL_waylandvideo.c)
prefers Wayland only when `wp_fifo_manager_v1` is advertised; otherwise it can
choose Xwayland. The captured KWin registry advertises FIFO, but this still
is not proof of the backend of a separately launched VRRTest process. Obtain
that proof from the actual test's logs/window information.

[SDL3's native Wayland GL swap path](https://raw.githubusercontent.com/libsdl-org/SDL/release-3.4.18/src/video/wayland/SDL_waylandopengles.c)
sets actual EGL swap interval zero and implements requested nonzero vsync using
its own callback wait. Default VRRTest vsync=0 skips that wait, but it is not
identical to Vulkan FIFO or MAILBOX. Driver-generated protocol requests remain
part of the comparison. [LÖVE's window implementation](https://raw.githubusercontent.com/love2d/love/11.5/src/modules/window/sdl/Window.cpp)
uses SDL_GL_SwapWindow.

mpv defaults to app_id `mpv` and video content type. VRRTest's SDL app ID may
be `love`, but **observe rather than assume** it. KWin app-specific window rules
could differ. Inspect main/video/OSD/cursor surface IDs, subsurface hierarchy,
opaque region, scale/viewport, dmabuf modifiers, color-management state and
tearing hints in both captures. Empty mpv child surfaces alone are not evidence
of a rendered overlay or scanout failure.

## Capture recipe (run on the user's GPU-capable session)

Keep both applications on the same output, truly fullscreen and focused.
Disable mpv's OSC/OSD and avoid notifications, screen recording, GPU overlays or
animated windows. Move the cursor out of view, then leave it stationary. Keep
vbltest measurement identical for every run. Traces add overhead; use short
captures, then confirm behavior again with tracing disabled.

```sh
mkdir -p build-pts-vrr/vrr-captures
qdbus6 org.kde.KWin /KWin supportInformation \
  >build-pts-vrr/vrr-captures/kwin-support.txt

WAYLAND_DEBUG=client ./build-pts-vrr/mpv --no-config \
  --vo=gpu-next --gpu-api=vulkan --gpu-context=waylandvk \
  --video-sync=pts --interpolation=no --fullscreen \
  --osc=no --osd-level=0 --audio=no --cursor-autohide=always --length=12 \
  --msg-level=vo=trace --log-file=build-pts-vrr/vrr-captures/mpv25.log \
  25fps.mp4 2>build-pts-vrr/vrr-captures/mpv25-wayland.log

# Set FIXED 25 FPS in VRRTest; fluctuations/random stutter off.
# Exit it manually after the short measurement interval.
SDL2COMPAT_DEBUG_LOGGING=1 SDL_VIDEODRIVER=wayland WAYLAND_DEBUG=client \
  vrrtest 2>build-pts-vrr/vrr-captures/vrrtest25-wayland.log

python3 TOOLS/wayland-vrr-trace.py \
  build-pts-vrr/vrr-captures/mpv25-wayland.log \
  build-pts-vrr/vrr-captures/vrrtest25-wayland.log
# Machine-readable output includes individual raw feedback records:
python3 TOOLS/wayland-vrr-trace.py --json \
  build-pts-vrr/vrr-captures/mpv25-wayland.log
```

The analyzer supports both Wayland 1.26 `object#id`/wall-time and older
`object@id`/millisecond logs. Counts commit intervals, commits with buffer attach
or damage, callback/feedback-only commits, frame-callback completions and raw
feedback replies/cancellations. It records roles/fullscreen/app-ID/opacity,
viewport/FIFO/tearing/content hints for comparison. Its callback-only class
means no buffer attach or damage in the visible pending requests, not proof
that the compositor did no work. It cannot recover dropped/truncated logs,
unlogged protocol operations or correlate IDs across separate connections.
Registry count warnings indicate possible separate-connection ambiguity, not
proof that there were multiple connections. Restrict measurements to steady
state before computing physical conclusions.

mpv trace records added by this change:

| Record | Purpose |
|---|---|
| `WL surfaces` | Map surface roles, callback surface, FIFO/presentation capabilities and app ID |
| `WL presentation-clock` | Actual protocol clock, whether accepted, protocol version and option |
| `WL toplevel-state` | Actual fullscreen/activated/suspended configure state |
| `WL opaque-region` | Alpha request, region dimensions and scale |
| `WL frame-done` / `WL frame-request` | Callback arrival cadence and pending rearm |
| `WL feedback-request` | Object/serial/surface and local request receipt clock |
| `WL feedback-output` | Output linked to a feedback object |
| `WL feedback-presented` | Raw timestamp/delta/refresh/flags/sequence and request-to-receipt age |
| `WL feedback-discarded/evicted/cancelled` | Distinguish compositor discard from client pool loss/reconfigure |
| `WL swap-boundary` | Link presents to callback count and presentation enable state |
| `WL feedback-cache` | Raw timestamp slots versus converted ready/invalid slots and returned display time |
| `Vulkan swapchain request` | Requested mode/depth/alpha; libplacebo may still choose a fallback |
| `WL app-commit` | Known non-video configuration commits; WSI commits require protocol trace |

## How to localize display_time=-1

1. `WL presentation-clock`: accepted? `wl_present` enabled? No supported clock
   or disabled option explains why mpv does not enable its feedback path.
2. `WL frame-done`: does the initial callback arrive? mpv requests subsequent
   feedback from this callback; no callback means no application feedback request.
3. `WL feedback-request` plus protocol `feedback(surface, object)`: is the
   intended surface used? Did that request get latched by a later commit?
4. Raw `presented`/`discarded` events: none versus explicit discard versus client
   eviction/cancellation are different failures. Check the surface buffer/damage
   commit sequence, callback queue and whether logs were complete.
5. Raw presented timestamps exist but `WL feedback-cache` remains invalid or
   returns -1: the problem is then conversion/cache handling, **not proof that
   KWin lacks feedback or that VRR is off**. KWin zero sequence is a known source
   limitation; do not rely on it to measure vblank frequency.

Request age uses mpv's monotonic clock; raw protocol timestamps use the reported
presentation clock. Do not directly subtract the two on MONOTONIC_RAW or a
compositor-specific clock. Event receipt intervals also include dispatch delay.

## Prioritized experimental matrix and interpretation

1. **Matched native 25 Hz, stationary versus moving cursor.** If both programs
   stick near60 at25 but recover at30, the KWin30Hz heuristic/below-range branch
   is stronger than an mpv-specific explanation. If hiding/stopping cursor fixes
   output cadence while buffer commits stay25, investigate cursor-plane updates.
2. **25 versus30/40/50 Hz on the same app/backend.** Use distinct test material
   genuinely recorded/generated at those rates (do not duplicate frames to claim
   a25Hz solution). Test VRRTest at25, not just30–60. Record actual protocol
   cadence; target-FPS settings are not sufficient evidence.
3. **All mpv surface commits versus buffer/damage commits.** If buffer updates
   are25 but another surface or callback-only commits run60, localize the WSI or
   application source first. If *all* client commits are25 and output flips are60,
   shift to foreign repaints/cursor/KWin/driver/LFC rather than PTS scheduling.
4. **Native versus Xwayland VRRTest.** Explicit `SDL_VIDEODRIVER=wayland` and
   `x11`, identical25Hz and focus/fullscreen. Do not use an Xwayland success to
   conclude native Wayland/Vulkan is equivalent.
5. **WSI comparison, one change at a time.** mpv explicit FIFO versus MAILBOX;
   then gpu-next OpenGL/Wayland with `--wayland-internal-vsync=no` and
   `--opengl-swapinterval=0`, matching VRRTest's native free-running GL path.
   No IMMEDIATE/tearing mode is a default proposed fix.
6. **Classification and scanout.** Keep opaque defaults, fullscreen and focus;
   test content-type `none/video/game` independently, then app-ID matching the
   observed VRRTest ID to identify window-rule effects. These are diagnosis,
   not evidence that game content automatically enables VRR in this KWin path.
   Investigate format/modifier/color/rotation/effects if only one app scans out.

Safe KWin read-only probes:

```sh
qdbus6 org.kde.KWin /KWin activeOutputName
busctl --user get-property org.kde.KWin /Effects org.kde.kwin.Effects activeEffects
busctl --user get-property org.kde.KWin /Effects org.kde.kwin.Effects loadedEffects
# Known UUID from KWin's window information/debug console:
qdbus6 org.kde.KWin /KWin getWindowInfo WINDOW_UUID
journalctl --user-unit plasma-kwin_wayland --boot 0 --no-pager
```

`queryWindowInfo` is interactive and `showDebugConsole` opens UI; neither was
executed automatically. SupportInformation reports nominal refresh and policy,
not instantaneous Hz or the current plane assignment. A foreground terminal
used for commands can also change KWin's active-window policy: capture during
full-screen playback, not only after it exits.

If all client commits are25 and the cursor/effects controls do not localize the
extra output updates, the next instrumentation belongs **inside KWin**:

- `RenderLoop::scheduleRepaint`: output, calling item/output layer, active-window
  surface, `activeWindowControlsVrrRefreshRate`, estimated interval, selected
  presentation mode and whether the request was delayed.
- Compositor's cursor-plane repaint lambda: update cause and `maxVrrCursorDelay`.
- `Compositor::composite` / scanout preparation / DRM present: presentation mode,
  new content versus cursor-only/no-new-content update, scanout candidate/reject
  reason, selected buffers and actual flip completion timestamps.

KWin's existing `KWIN_LOG_PERFORMANCE_DATA=1` startup flag writes per-output CSV
with target/pageflip/render times and VRR/tearing flags, but does not identify
all repaint callers. It requires a compositor restart and writes files; no
restart or setting change was performed here. Any KWin instrumentation or
threshold experiment should be separately reviewed and user-run. Do not alter
the mpv scheduler or add duplicate frames to hide the compositor behavior.

## Current conclusion

The PTS timing measurements already rule out the original client-clock problem.
The strongest concrete new lead is KWin's **>=30Hz active-surface repaint/cursor
protection**, together with unmatched25-versus30–60 comparisons. The missing
converted feedback is independently diagnosable with the new raw-event logs.
Neither lead proves the final root cause without GPU-capable matched protocol,
physical-flip and cursor/effect measurements. The supplied diagnostics are meant
to gather those missing discriminating observations, not assert an unmeasured
hardware fix.
