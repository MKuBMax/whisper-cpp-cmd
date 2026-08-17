# Python 环境

本项目的 pyobjc、pynput、sounddevice、opencc 和 pytest 安装在 .venv-arm64，不能假设 Conda base 环境包含这些依赖。

使用项目解释器：

~~~sh
.venv-arm64/bin/python -m pytest
.venv-arm64/bin/python -c "import AppKit, sounddevice, opencc"
~~~

当前环境是 Python 3.14，解释器指向 Homebrew 的 python@3.14。

全局开发规则可能要求使用 Conda，但本项目的依赖只在 .venv-arm64 中完整可用；运行测试、冒烟检查和项目脚本时以本项目环境为准。

