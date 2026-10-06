# 第二轮实机结果：40 Hz VRR 正常；25 Hz 低于面板声明的 30 Hz 下限

## 摘要

- mpv 唯一视频帧提交约 40 Hz，vbltest 约 40 Hz，实际活动 CRTC 的 VRR_ENABLED=1。
- 原生 Wayland VRRTest 固定 40 FPS 同样约 40 Hz，VRR_ENABLED=1。
- 再测原生 VRRTest 固定 25 FPS，VRR_ENABLED=1，但 vbltest 约 **37.5 Hz**，不是第一轮的约60 Hz。
- EDID 声明的垂直频率范围为 **30–60 Hz**。KWin 6.7.5 确实将该下限读作 minVrrRefreshRateHz。
- 两种应用在采样快照时均没有直接扫描原始客户端 buffer，但40Hz仍正常跟随。

**PTS scheduler、反馈缓存行为和系统设置均未修改，没有客户端软件重复帧。** 这轮实际验证了 experimental branch 的40Hz VRR能力；不能将它夸大为已经解决25FPS下的LFC或已经找到第一轮持续60Hz的确切重绘源。

## 1. 测量和实际提交

用户在同一采集目录新增了三组。退出码均为应用0、timeout测量124。

| 组 | 稳态 buffer/damage commit | 中位间隔 | vbltest 稳态 | DRM VRR_ENABLED 开始/结束 |
|---|---:|---:|---:|---:|
| 原生 VRRTest25，补 DRM 状态 | 24.99921 Hz | 39.994 ms | 37.49–37.51 Hz | 1 / 1 |
| 原生 VRRTest40 | 39.99830 Hz | 24.995 ms | 39.99–40.00 Hz | 1 / 1 |
| mpv40（25fps视频1.6倍速） | 39.99888 Hz | 25.000 ms | 39.98–40.01 Hz | 1 / 1 |

各组第一个vbltest样本略偏高，表中范围使用后续稳态样本。

窗口：VRRTest自首条协议记录起15–25秒，mpv为5–17秒。buffer/damage commit分别为250、400、480次；callback-only commit均为0。mpv其他surface没有稳态额外commit。

因此40Hz结果不是仅看target FPS或播放器的flip调用，而是客户端协议和DRM vblank测量同时相符。

## 2. 活动 connector → CRTC 关联

六份DRM快照（三组，各有开始和结束两份）均关联到：

```text
/dev/dri/card1, driver=i915
connector=588, type=14 (eDP), connected
connector CRTC_ID=171
CRTC=171, ACTIVE=1, VRR_ENABLED=1
connector vrr_capable=1
mode=1920x1080, nominal vrefresh=60
```

没有把其他闲置CRTC的VRR_ENABLED=0误作当前输出状态。start/end.err均为空、JSON有效。快照是采样前后状态，不是整个12秒期间每一次提交的完整时间序列。

## 3. 面板下限是30Hz，不是25Hz

从DRM JSON的EDID blob解码得到：

```text
EDID: 128 bytes, version1.4, checksum0
panel text: BOE CQ / NV156FHM-N69
range descriptor: 000000fd001e3c1e5311010a202020202020
vertical min=30Hz, max=60Hz
```

这里不是只拿名义模式60Hz推断范围；是有效EDID的range-limits descriptor。EDID的range descriptor本身不是所有显示器上通用的完整Adaptive-Sync认证信息，但本机还明确报告vrr_capable=1，且KWin使用这项下限：

- [KWin v6.7.5 Edid读取min_vert_rate_hz](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/utils/edid.cpp#L249)。
- [DrmOutput将其写入minVrrRefreshRateHz](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/backends/drm/drm_output.cpp#L76)。

25Hz的一帧周期是40ms，长于30Hz下限允许的约33.33ms。因此本机不能直接把物理扫描降到25Hz；若要保持25FPS内容的平稳显示，必须依赖显示栈的低端处理，例如合适的LFC/重复扫描，而不是要求mpv改变唯一视频帧的PTS提交。

25FPS对应50Hz可以是稳定2×LFC；本轮37.5Hz则是平均1.5倍，**不能叫作已经实现稳定2×LFC**。它可能与最小刷新超时及后续新内容的相位有关，也可能含其他输出更新。当前只有平均频率，没有逐vblank/atomic commit时间序列，不能定案。

## 4. DRM给出了比ZERO_COPY更可靠的扫描buffer证据

三组的active primary plane均为plane35；只有它和cursor plane165处于活动状态。

primary FB_ID属性附带的实际framebuffer数据：

```text
width=1920, height=1080
format=1211384385 = AB4H (ABGR16161616F)
modifier=72057594037927944
plane0 pitch=15360 = 1920 * 8 bytes
```

客户端原始buffer：

- mpv：AB30（ABGR2101010），每像素4字节。
- VRRTest：XR24（XRGB8888），每像素4字节。

实际primary FB的格式与客户端原始buffer不相同。因此**在这些快照时刻**不是把客户端原始buffer直接挂到primary plane，而是在扫描合成/转换后的16-bit float buffer。不能仅据两次快照证明中间每一帧始终走相同路径，但两次快照均如此、且测量期间40Hz跟随正常，进一步支持direct scanout不是VRR生效的前提。

这个结论不依赖KWin不提供的ZERO_COPY标志。

## 5. 如何解释第一轮60Hz与第二轮37.5Hz

事实必须分开：

1. 第一轮两个客户端固定25FPS都约60Hz，但未同步记录实际VRR_ENABLED。
2. 第二轮原生VRRTest25确认VRR_ENABLED=1，结果约37.5Hz。
3. 两轮客户端buffer/damage commit都约25Hz，没有额外callback-only commit。
4. 第二轮mpv和VRRTest40均确认VRR开启并跟随40Hz。

不能把第一轮60Hz无条件说成已验证VRR开启时的固定行为，也不能把第二轮37.5Hz当作同样复现了第一轮60Hz。跨轮有一个尚未被记录的状态/外部重绘差异。

KWin的>=30Hz active-surface repaint保护仍是相关源码线索：25Hz不满足保护，而40Hz满足。其他surface、效果或后台动画产生重绘时，它们更可能在25Hz下独立推动输出；光标静止只排除了持续鼠标运动作为必要条件。此前Harness自动Goal产生后台GUI活动的时间变化也不能单凭时间相关性就定为根因，必须有KWin repaint caller trace才可确认。

[RenderLoop自适应调度](https://invent.kde.org/plasma/kwin/-/blob/v6.7.5/src/core/renderloop.cpp#L100)仍有minimum-refresh TODO。它说明低端处理值得检查，但不证明某个具体vblank来自此TODO，也不证明内核/面板没有自动重复扫描。

## 6. 已经验证与尚未验证

### 已经验证

- experimental mpv PTS提交链路确实能在本机驱动40Hz VRR。
- 原生Wayland GL和Vulkan在匹配40Hz时没有之前怀疑的输出频率差别。
- 直接扫描原始客户端buffer不是本轮40Hz VRR的必要条件。
- 25Hz低于面板声明且KWin读取的30Hz下限，必须由显示栈处理低端刷新。
- `display_time=-1`的反馈缓存问题已单独复现；这次仍未修复它，不能拿它判断实际VRR。

### 尚未验证

- 第一轮25Hz时持续60Hz的确切来源：实际VRR状态、KWin无关重绘或驱动/面板低端行为。
- 第二轮25FPS产生37.5Hz的逐vblank相位及atomic commit来源，是否属于硬件超时重复扫描。
- 当前条件下mpv25FPS是否与新测的原生VRRTest25同样得到37.5Hz；这轮没有新增mpv25同步DRM对照。

## 7. 下一步不是继续改PTS scheduler

无需再扩大客户端提交实验矩阵或用重复帧掩盖问题。若用户要继续精确定位：

1. 可只补一组mpv25静止、同步DRM的对照，确认当前条件下它是60还是37.5Hz。
2. 之后观测KWin scheduleRepaint调用源、selected presentation mode、primary/cursor atomic commit及pageflip完成时间，并与逐vblank时间戳关联。
3. 将frame commit25Hz但额外输出更新的来源分为foreign repaint、KWin低端策略或i915/面板自动刷新，再讨论显示栈层面的修复。

KWin调试/重启和内核trace会改变运行环境、可能需要权限，必须单独征得用户同意。不会自动启用这些操作，不会恢复自动Goal循环。
