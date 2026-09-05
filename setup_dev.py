#!/usr/bin/env python3
"""DEV 启动器 App 的 py2app 配置：alias 模式，引用源码，本地调试专用。

产物是 build/dev/WhisperCppCmdDev.app，不进 /Applications，不提交 git，
不用于分发。bundle id 与正式版隔离，可与正式版共存。
正式分发仍走 setup.py + package_app.sh + ship_app.sh，那套不动。
"""

from __future__ import annotations

import os

from setuptools import setup
from config.version import APP_BUNDLE_VERSION


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEV_APP_NAME = "WhisperCppCmdDev"
DEV_BUNDLE_ID = "com.mkbm.whispercppcmd.dev"
ICON_PATH = os.path.join(PROJECT_DIR, ".py2app-assets", "WhisperCppCmd.icns")

APP = ["app_bootstrap.py"]
RESOURCES = [
    os.path.join(PROJECT_DIR, "icons"),
    os.path.join(PROJECT_DIR, "VERSION"),
]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": ICON_PATH if os.path.exists(ICON_PATH) else None,
    "includes": [
        "AppKit",
        "Foundation",
        "objc",
        "ApplicationServices",
        "Quartz",
        "PyObjCTools",
        "PyObjCTools.AppHelper",
    ],
    "packages": [
        "app",
        "config",
        "core",
        "ui",
        "numpy",
        "opencc",
        "pynput",
        "sounddevice",
        "_sounddevice_data",
    ],
    "resources": RESOURCES,
    "plist": {
        "CFBundleName": DEV_APP_NAME,
        "CFBundleDisplayName": "Whisper CPP CMD DEV",
        "CFBundleIdentifier": DEV_BUNDLE_ID,
        "CFBundleShortVersionString": APP_BUNDLE_VERSION,
        "CFBundleVersion": APP_BUNDLE_VERSION,
        "LSUIElement": True,
        "NSAppleEventsUsageDescription": "WhisperCppCmdDev needs to paste transcribed text into other applications.",
        "NSMicrophoneUsageDescription": "WhisperCppCmdDev needs microphone access for speech transcription.",
    },
}

if OPTIONS["iconfile"] is None:
    OPTIONS.pop("iconfile")

setup(
    app=APP,
    name=DEV_APP_NAME,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
