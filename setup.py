#!/usr/bin/env python3

from __future__ import annotations

import os

from setuptools import setup
from config.version import APP_BUNDLE_VERSION


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "WhisperCppCmd"
ICON_PATH = os.path.join(PROJECT_DIR, ".py2app-assets", f"{APP_NAME}.icns")

APP = ["app_bootstrap.py"]
RESOURCES = [
    os.path.join(PROJECT_DIR, "icons"),
    os.path.join(PROJECT_DIR, "distribution", "update_app.sh"),
    os.path.join(PROJECT_DIR, "VERSION"),
]

# sysref_capture 二进制在构建时若存在则随包发布；源码开发模式直接用 core/ 下的产物。
_SYSREF_BIN = os.path.join(PROJECT_DIR, "core", "sysref_capture")
if os.path.isfile(_SYSREF_BIN):
    RESOURCES.append(_SYSREF_BIN)

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
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "com.mkbm.whispercppcmd",
        "CFBundleShortVersionString": APP_BUNDLE_VERSION,
        "CFBundleVersion": APP_BUNDLE_VERSION,
        "LSUIElement": True,
        "NSAppleEventsUsageDescription": "WhisperCppCmd needs to paste transcribed text into other applications.",
        "NSMicrophoneUsageDescription": "WhisperCppCmd needs microphone access for speech transcription.",
    },
}

if OPTIONS["iconfile"] is None:
    OPTIONS.pop("iconfile")

setup(
    app=APP,
    name=APP_NAME,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
