# Wayland / KWin VRR 实机采集操作手册

目的：在 **不修改 PTS scheduler、不重复视频帧** 的条件下，区分客户端额外提交、KWin 光标/无关重绘，以及低于 VRR 下限的行为。

本次首先采集四组：mpv 固定 25 Hz（光标静止/移动），原生 Wayland VRRTest 固定 25 FPS（光标静止/移动）。两程序的基线均保持光标可见；隐藏光标是后续独立对照，避免一开始就改变 cursor plane 条件。

## 0. 测试纪律

- 所有命令在**实机有 GPU 的桌面会话终端**中执行，不是在没有 `/dev/dri` 的隔离环境里执行。
- 使用同一个显示输出、同一 KWin Adaptive Sync 设置。不要同时运行两个测试窗口。
- 保持真正全屏、窗口获得焦点。测试中不要 Alt+Tab、打开终端覆盖窗口、调音量/亮度、显示通知、打开录屏或性能叠加层。
- “静止”组启动后把鼠标放到屏幕中部，然后不再移动；不要放在会触发热角的位置。
- “移动”组在屏幕中部持续画小圈，不点击、不滚轮，不移动到边缘。
- 此手册不要求改 KWin 配置、重启 KWin、关闭合成器、开启 tearing 或修改应用标识。
- vbltest 使用**你之前已经测成功的同一命令和参数**。不要为这次实验更换 CRTC、驱动或测量方式。

## 1. 准备：在仓库根目录执行

```bash
pwd
# 应位于 /home/xianhongtao/Repositories/mpv

git branch --show-current
# 应为 experiment/pts-vrr

meson compile -C build-pts-vrr

CAP="build-pts-vrr/vrr-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$CAP"
export CAP
printf '本轮日志目录：%s\n' "$CAP"

qdbus6 org.kde.KWin /KWin supportInformation > "$CAP/kwin-support.txt"
./build-pts-vrr/mpv --version > "$CAP/mpv-version.txt"
```

后续命令请在**同一终端**中执行，保留 `CAP` 变量。

## 2. 设置 vbltest 测量命令

先定义这个函数。如果此前成功运行的命令带参数或使用 sudo，请将函数内部 `vbltest` 部分替换成**此前的完整命令**，保留前面的 `timeout -k 2s 12s`。例如原来是 `sudo vbltest 参数`，这里就写 `timeout -k 2s 12s sudo vbltest 参数`。timeout 仅限制测量进程时长，不改变 vbltest 参数或提交视频帧。

```bash
measure_vblank() {
    timeout -k 2s 12s vbltest
}
```

如果需要 sudo，先在启动测试前完成认证，例如 `sudo -v`。不能让后台采集在全屏播放时等待密码。不要自行增加 sudo，只有原来测量需要它才使用。

定义 mpv 采集函数：

```bash
capture_mpv() {
    name="$1"
    speed="$2"
    cursor="$3"

    # 等待启动/全屏动画结束，再运行你原来的 vbltest。
    (sleep 5; measure_vblank > "$CAP/$name-vbl.log" 2>&1) &
    vb_pid=$!

    WAYLAND_DEBUG=client ./build-pts-vrr/mpv --no-config \
      --vo=gpu-next --gpu-api=vulkan --gpu-context=waylandvk \
      --video-sync=pts --interpolation=no --fullscreen \
      --osc=no --osd-level=0 --audio=no \
      --speed="$speed" --cursor-autohide="$cursor" --length=32 \
      --msg-level=vo=trace --log-file="$CAP/$name-mpv.log" \
      25fps.mp4 > "$CAP/$name-stdout.log" 2> "$CAP/$name-wayland.log"
    mpv_status=$?

    wait "$vb_pid"
    vb_status=$?
    printf '%s: mpv_exit=%s vbltest_exit=%s\n' "$name" "$mpv_status" "$vb_status" \
      | tee "$CAP/$name-status.txt"
}
```

说明：`--length=32` 是媒体时长；这两组速度为 1，所以约 32 秒后自动结束。vbltest 的采样开始于启动后约 5 秒，最多持续 12 秒，因此正常采样期间视频仍处于全屏。`mpv_exit=0` 表示正常退出；`vbltest_exit=124` 表示被 timeout 正常限时结束，并不自动表示测量失败——仍须确认日志中有有效数据。若 mpv 本身启动失败，这一组无效，保留错误日志。

## 3. mpv 两组测试

### A. 25 Hz，光标可见但静止

```bash
capture_mpv mpv25-still 1 no
```

操作：视频全屏后，马上把鼠标放到屏幕中部，然后直到视频退出都不动。不要切回终端。退出后确认 `mpv25-still-vbl.log` 包含有效测量，而不是权限错误、设备打开错误或空文件。

### B. 25 Hz，光标可见并持续移动

```bash
capture_mpv mpv25-moving 1 no
```

操作：从全屏后约 2 秒开始，到视频退出前，持续在屏幕中部画小圈。不要点击或触发 OSC。退出后同样检查 vbltest 日志。

注意：这两组只有鼠标运动不同，mpv 的选项完全一致。

## 4. VRRTest 两组测试

定义函数。vbltest 在启动后约 15 秒开始，给你时间把目标调到 25 FPS。

```bash
capture_vrrtest() {
    name="$1"

    (sleep 15; measure_vblank > "$CAP/$name-vbl.log" 2>&1) &
    vb_pid=$!

    SDL2COMPAT_DEBUG_LOGGING=1 SDL_VIDEODRIVER=wayland WAYLAND_DEBUG=client \
      vrrtest > "$CAP/$name-stdout.log" 2> "$CAP/$name-wayland.log"
    app_status=$?

    wait "$vb_pid"
    vb_status=$?
    printf '%s: vrrtest_exit=%s vbltest_exit=%s\n' "$name" "$app_status" "$vb_status" \
      | tee "$CAP/$name-status.txt"
}
```

### C. 原生 Wayland，固定 25 FPS，光标静止

```bash
capture_vrrtest vrrtest25-still
```

启动后按以下顺序操作，**在 10 秒内完成**：

1. 看屏幕上 `target FPS`。按 **↓** 每次减 1，按 **↑** 每次加 1，将它调到 **25**。不是 Ctrl+↓，也不是左右方向键。
2. 确认屏幕显示：
   - `fullscreen: true`；若不是，按 **Ctrl+F**。
   - `vsync: false`；若是 true，按 **S** 一次。
   - `fluctuating: false`；若是 true，按 **F** 一次。
   - `random stutter: false`；若是 true，按 **R** 一次。
   - `busy wait: false`；若是 true，按 **B** 一次。
3. 保持同一测试场景，不按数字键切换场景。
4. 将鼠标放在屏幕中部，然后静止。`actual FPS` 可能略低于 25；如明显不接近 25，请记录实际数值。
5. 总计等待约 **30 秒**，然后按 **Q** 或 **Esc** 退出。

如果 10 秒内没来得及设好 25，请注明调好时刻，或重跑这一组；不要把调节过程中 55→25 FPS 的区间当作固定 25 FPS 测量。

### D. 原生 Wayland，固定 25 FPS，光标移动

```bash
capture_vrrtest vrrtest25-moving
```

重复上面的设置。调好 25 FPS 后持续在屏幕中部画小圈，直到约 30 秒后按 Q/Esc 退出。设置、场景、输出和测试时长都与 C 相同。

若强制 Wayland 后 VRRTest 启动失败，保留失败日志并停止该组。**不要偷偷改成 X11 再称作 Wayland 对照**。

## 5. 分析和整理

```bash
python3 TOOLS/wayland-vrr-trace.py \
  "$CAP/mpv25-still-wayland.log" \
  "$CAP/mpv25-moving-wayland.log" \
  "$CAP/vrrtest25-still-wayland.log" \
  "$CAP/vrrtest25-moving-wayland.log" \
  > "$CAP/protocol-summary.txt"

python3 TOOLS/wayland-vrr-trace.py --json \
  "$CAP/mpv25-still-wayland.log" \
  "$CAP/mpv25-moving-wayland.log" \
  "$CAP/vrrtest25-still-wayland.log" \
  "$CAP/vrrtest25-moving-wayland.log" \
  > "$CAP/protocol-summary.json"

# 稳态 cadence：mpv 取启动后 5–17 秒；VRRTest 取 15–25 秒。
# 对象关联仍解析完整日志，request/feedback 计数仍是全程计数。
python3 TOOLS/wayland-vrr-trace.py --start-seconds=5 --duration-seconds=12 \
  "$CAP/mpv25-still-wayland.log" "$CAP/mpv25-moving-wayland.log" \
  > "$CAP/mpv-steady-summary.txt"
python3 TOOLS/wayland-vrr-trace.py --start-seconds=15 --duration-seconds=10 \
  "$CAP/vrrtest25-still-wayland.log" "$CAP/vrrtest25-moving-wayland.log" \
  > "$CAP/vrrtest-steady-summary.txt"

printf '采集目录：%s\n' "$CAP"
ls -lh "$CAP"

tar -czf "${CAP}.tar.gz" -C "$(dirname "$CAP")" "$(basename "$CAP")"
printf '请提供：%s.tar.gz\n' "$CAP"
```

请提供整包，不要只提供 `protocol-summary`，因为定位 `display_time=-1` 需要 mpv 原始应用日志与协议日志相互核对。

同时补充这个结果表，可以直接粘贴在聊天中：

| 组 | 实际测试条件 | vbltest 大致频率/变化 | VRRTest actual FPS（适用时） | 异常 |
|---|---|---|---|---|
| mpv25-still | 25 Hz，光标静止 |  | — |  |
| mpv25-moving | 25 Hz，光标移动 |  | — |  |
| vrrtest25-still | 原生 Wayland，固定 25，光标静止 |  |  |  |
| vrrtest25-moving | 原生 Wayland，固定 25，光标移动 |  |  |  |

补充说明：是否确认每组播放期间 DRM `VRR_ENABLED=1`；是否在采样期间出现通知、录屏、弹窗、焦点切换；原来 vbltest 的完整命令是什么。不要启动会覆盖测试窗口的 DRM 状态查看界面。

## 6. 暂时不要据此误判

- 25 FPS 如果显示约 **50 Hz**，可能是 2× LFC，不能判失败。
- 30 FPS 如果显示约 **60 Hz**，也可能是 2× LFC，不能单凭这个数字判固定刷新。
- 25 Hz 提交但 60 Hz 输出，只能在匹配条件后继续分辨额外重绘和驱动/面板行为。
- frame callback 的频率不是物理扫描频率。
- KWin 6.7.5 的 feedback 不设置 ZERO_COPY，缺少它不能证明未走 direct scanout；feedback 的 `refresh` 也不是即时物理刷新率。
- `display_time=-1` 不等于没有原始 presentation feedback。新版日志会区分请求、presented/discarded 与转换缓存。

## 7. 可选第二轮：第一轮交回后再决定

不必一开始就做这些。若第一轮指向 KWin 的 ≥30 Hz 分界，再用同一个视频测唯一帧提交速率变化：

```bash
# 仅用于诊断，改变播放速度；不是正常 25 FPS 播放的修复。
# 每个输入视频帧仍只提交一次，没有软件重复帧。
capture_mpv mpv30-still 1.2 no
capture_mpv mpv40-still 1.6 no

# 光标隐藏作为独立因素，与 mpv25-still 对照。
capture_mpv mpv25-hidden 1 always
```

再将 VRRTest 固定目标分别调成 30、40 FPS 做匹配对照。FIFO/MAILBOX、OpenGL/Vulkan、content-type/app_id 和 direct scanout 条件都放到后续单因素实验；现在不要同时改多个选项。
