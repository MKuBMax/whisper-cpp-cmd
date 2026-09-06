"""single_instance.sh 匹配规则测试：只停本项目两个 App，停不掉阻止启动。

TDD RED：scripts/single_instance.sh 尚不存在，fixture 会因文件缺失失败。
"""

import os
import subprocess

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "single_instance.sh",
)


def _read():
    with open(SCRIPT, "r", encoding="utf-8") as f:
        return f.read()


def test_script_exists_and_is_executable():
    assert os.path.isfile(SCRIPT), f"缺少 {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} 不可执行"


def test_matches_formal_app():
    body = _read()
    assert "/Applications/WhisperCppCmd.app/Contents/MacOS/WhisperCppCmd" in body


def test_matches_dev_app():
    body = _read()
    assert "WhisperCppCmdDev.app/Contents/MacOS/WhisperCppCmdDev" in body


def test_does_not_match_bare_python_main():
    body = _read()
    assert "main.py" not in body, "裸 Python 已下线，匹配规则不应再含 main.py"


def test_stop_failure_blocks_start():
    body = _read()
    assert "exit 1" in body, "停不掉必须返回非零，阻止新实例启动"


def test_shell_syntax_valid():
    result = subprocess.run(
        ["bash", "-n", SCRIPT], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
