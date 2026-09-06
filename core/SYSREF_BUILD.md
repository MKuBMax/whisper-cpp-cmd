# sysref_capture 构建说明

产物 `core/sysref_capture` 是 Swift 二进制，不入库，需本地构建。

## 构建

```bash
SDK=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk
swiftc -o core/sysref_capture -sdk $SDK -target arm64-apple-macosx13.0 \
  core/sysref_capture.swift core/sysref_main.swift \
  -framework ScreenCaptureKit -framework AVFoundation \
  -framework CoreMedia -framework Foundation
```

SDK 路径随 Xcode 版本变化，用 `ls /Library/Developer/CommandLineTools/SDKs/` 找最新 MacOSX*.sdk。

## 冒烟

```bash
printf 'stop\n' | ./core/sysref_capture --sysref > /tmp/sysref.bin 2>/tmp/sysref.err
```

正常输出 40 字节：ready 加 stopped 两帧控制消息。

## 说明

- 只抓系统输出音频，不抓画面，但 Apple 把系统内录放在 ScreenCaptureKit，
  首次使用会弹屏幕录制权限。
- 源码 `core/sysref_capture.swift` 用 `excludesCurrentProcessAudio` 排除自身音频。
- 正式包发布时若产物存在，`setup.py` 会随包发布；不存在则跳过，运行时回退原声。
