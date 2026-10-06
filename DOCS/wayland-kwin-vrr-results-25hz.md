# 第一轮实机结果：25 Hz 下 mpv 与原生 Wayland VRRTest 均约 60 Hz

## 本轮结论

这轮匹配实验改变了调查方向：**当前证据不支持 mpv 特有的 surface 或 Vulkan WSI 提交行为造成 60 Hz；原生 Wayland VRRTest 在固定 25 FPS 下同样约 60 Hz。**

PTS scheduler 保持不变。没有修改 feedback estimator、KWin 设置或提交重复视频帧。后述缓存问题只做了独立复现，没有把修复混进这轮实验。

实机原始日志位于 `build-pts-vrr/vrr-20261006-231244/`。数据由用户在有 GPU 的会话中采集；分析环境本身没有 `/dev/dri`。

## 1. 四组固定 25 Hz 对照

统计窗口：mpv 自首条协议记录起 5–17 秒，VRRTest 15–25 秒。

| 组别 | buffer/damage commit | 间隔中位数 | callback-only / other commit | vbltest |
|---|---:|---:|---:|---:|
| mpv，鼠标静止 | 300，24.99865 Hz | 39.994 ms | 0 / 0 | 约 60 Hz |
| mpv，鼠标移动 | 300，24.99897 Hz | 40.005 ms | 0 / 0 | 约 60 Hz |
| 原生 Wayland VRRTest，鼠标静止 | 250，24.99929 Hz | 40.000 ms | 0 / 0 | 约 60 Hz |
| 原生 Wayland VRRTest，鼠标移动 | 250，24.999 Hz | 40.003 ms | 0 / 0 | 约 60 Hz |

两组 mpv 的视频/OSD/cursor 子 surface 在稳态窗口没有额外 commit。主 surface#5 的所有 commit 都属于 buffer/damage commit。VRRTest 的 surface#46 同样没有额外 callback-only commit。

vbltest 的首个样本略有启动/采样误差，其余样本基本为 59.98–60.01 Hz。鼠标移动确实产生大量 pointer 协议事件，但没有改变四组约 60 Hz 的结果。它不是本轮稳定 60 Hz 的必要条件；这不意味着 KWin 的 cursor VRR-delay 路径不存在。

两程序均正常退出；测量退出码 124 是手册中 timeout 的限时结束，不是应用失败。

## 2. 实际 surface / WSI 差异

协议已经证明 VRRTest 使用原生 Wayland，不是只根据环境变量推测。它使用 app_id `love`、1920x1080 opaque region、桌面全屏请求。

mpv 使用 app_id `mpv`、video content type、主 surface#5；video#6、OSD#7 为子 surface。实际 swapchain 请求为 FIFO、depth=2、alpha_bits=0，稳态 opaque region / viewport destination 均为 1920x1080，应用 configure 显示 fullscreen=1、activated=1。

dmabuf 区别：

- mpv：fourcc 808665665（AB30，10-bit alpha RGB）；alpha 请求为 0，surface 被标为 opaque。
- VRRTest：fourcc 875713112（XB24，8-bit opaque RGB）。
- 两者日志中的 modifier 高/低字为 16777216 / 8，均导入 1920x1080 buffer。
- mpv 的 Vulkan WSI 使用 `wp_fifo_v1.set_barrier/wait_barrier`；这份 VRRTest GL trace 没有对应 FIFO barrier。

这些是真实差异，**但它们没有解释本轮输出差异，因为本轮输出并没有区别：固定 25 Hz 时都约 60 Hz。** 在较高帧率匹配实验出现差异以前，不应把格式、色彩或 FIFO 直接称作根因。

Direct scanout 仍未由日志证明或否定。KWin 6.7.5 不设置 ZERO_COPY，不能拿 absence 当作 composition 证据。

## 3. display_time=-1 已定位到转换缓存，不是 feedback 缺失

mpv presentation clock 为 CLOCK_MONOTONIC（id=1），版本 2，接受且启用。

全程结果：

- 静止组：feedback 请求 400、latched 399、presented 399、discarded 0。
- 移动组：feedback 请求 401、latched 400、presented 400、discarded 0。
- 各有一个末尾未 latched 的请求；不能要求未提交的 pending 请求产生 presented 事件。

例如静止组应用日志中：

```text
0.599s WL feedback-presented: ... timestamp=10148742844000 ... sequence=0 flags=0x7 ...
0.599s WL feedback-cache: enabled=1 stamped=1 ready=0 invalid=2 returned_display_time=-1
```

原始 presented 有效到达，但缓存没有生成 ready slot。该状态随后反复出现。

### 实际缓存丢失机制

未改动的 [present_sync.c](<../video/out/present_sync.c#L71-L126>) 具有以下组合语义：

1. `present_sync_swap()` 对没有 UST 的 slot 设置 `queue_display_time=-1`。
2. `present_sync_update_values()` 找第一个没有 UST 的 slot，**不排除已经标为 -1 的 slot**，所以新收到的有效 UST 可以写入一个旧的 invalid slot。
3. 下一次 `present_sync_swap()` 跳过所有非零 queue_display_time，包括这个 -1 slot，转而把后面的空 slot 再标为 -1。
4. `present_sync_get_info()` 取第一个非零 queue_display_time，包括 -1，返回 -1，然后清空整个 slot。刚收到的有效 UST 随之丢失。
5. 只要残留一个未消费的 invalid slot，以上循环可以持续：`stamped=1 ready=0 invalid=2` → 消费后 `stamped=0 ready=0 invalid=1`。

实机启动阶段可见第二次 swap 是全屏/resize redraw，没有对应 PTS get-vsync/cache 记录；它期间收到初始 callback、启用 presentation 并生成 unknown slot。随后正常帧第一次读取缓存时已有两个 invalid slot。这个初始化序列与上述机制吻合。

使用**实际未改动的 C 实现**编译并运行了独立 probe（在 ignored build/research 目录），不是只写 Python 模型：

```text
before get_info (unknown backlog): stamped=1 ready=0 invalid=2
after get_info (timestamp discarded): stamped=0 ready=0 invalid=1
# 连续四次复现同一循环
no unknown backlog, msc=0: stamped=1 ready=1 invalid=0
PASS: actual unmodified present_sync.c reproduces permanent -1 with unknown backlog; zero sequence alone does not cause it.
```

因此 KWin 的 sequence=0 **不能单独解释 queue display time 永远为 -1**；没有 backlog 的相同代码在 msc=0 时仍能返回正的 display time。sequence=0 当然限制基于 MSC 的 vsync-duration 估计，这是另一个问题。

此缓存问题不是物理 60 Hz 根因的证明：VRRTest 不请求这套 presentation feedback，固定 25 FPS 时仍约 60 Hz。修正观测缓存也不能被宣传为修复 VRR 输出。

## 4. 回调/反馈 cadence 不能当作视频或物理帧率

mpv 约 25 Hz 提交，而应用 callback receipt 约 12.5 Hz（每约 80 ms）；它从 callback 中请求 feedback，因此反馈只覆盖部分 commit。

静止组原始 presentation 时间戳间隔主要为约 66.656 / 83.332 ms，即约 4 / 5 个名义 60 Hz tick；这与 vbltest 的约 60 Hz 结果相符。不是 12.5 FPS 视频播放的证据，也不是把回调率当作物理频率。

VRRTest 同样有约 80 ms 的多数 callback receipt 间隔，不能借此声称其视频提交变成 12.5 Hz；其实际 buffer/damage commit 已独立测得约 25 Hz。

## 5. 可以排除什么，仍缺什么

本轮可以排除/削弱：

- 客户端没有按 25 Hz 提交：协议证据不支持。
- mpv 在稳态偷偷进行额外 callback-only commit：未发现。
- 只有 mpv 的子 surface 或 FIFO 导致 25 Hz 下 60 Hz：相同现象出现在原生 GL VRRTest。
- 鼠标持续移动是稳定 60 Hz 的必要条件：静止组仍然 60 Hz。
- `display_time=-1` 代表 KWin 没有 presented：原始反馈明确到达。

**尚未确认本轮每组采样时 DRM VRR_ENABLED 的实际值。** supportInformation 只有 Automatic 策略及名义 60.003 Hz，不能代替每组的 kernel 状态。此前用户观察过 VRR_ENABLED=1，但不能无条件当作这四组的同步证据。

物理 60 Hz 的最终原因仍待区分：

- 本次原生 Wayland 测试的实际 VRR enable/policy 路径；
- 25 Hz 低于有效 VRR 范围时的 KWin/驱动/面板处理及 LFC；
- 低于 KWin >=30 Hz damage-cadence 分界时的其他重绘源；
- 早先 VRRTest 30–60 FPS 成功测试是否走 Xwayland、是否同样 fullscreen/focus/output。

KWin 的 >=30 Hz 保护是真实源码事实，**本轮尚不能把它定为 60 Hz 根因**。它不禁用 VRR，也不自动制造持续 60 Hz 重绘。

## 6. 最小下一轮：只做两个固定 40 FPS 静止组

无需重做四组 25 Hz。沿用手册同一终端中的函数和 CAP：

```bash
# 25fps 输入以 1.6 倍速播放：唯一视频帧提交速率约 40 Hz。
# 这是诊断，不是 25 FPS 的修复，也没有重复帧。
capture_mpv mpv40-still 1.6 no

capture_vrrtest vrrtest40-still
# 10 秒内将 target FPS 改为 40；其余开关与上一轮完全一致。
# 鼠标静止，运行约 30 秒后 Q 退出。
```

分别记录采样期间实际 VRR_ENABLED（若目前无法读，说明即可），保留所有新增日志。

判读：

- 两者都跟随约 40 Hz：优先追 25 Hz 的低端 VRR/LFC/低帧率政策分支，而不是 mpv client cadence。
- 原生 VRRTest 约40、mpv仍约60：才有证据继续对照 Vulkan FIFO 与 GL、buffer format/color/scanout 条件。
- 两者仍约60：先确认实际 VRR enable、fullscreen/focus 与原生后端；再与明确强制 X11 的 VRRTest40 对照。

暂不混入 app_id/content-type、present-mode、tearing、effect 开关或缓存修复，以保持单因素对照。
