"""WhisperCliBackend 命令构造单测。

不启动真实 whisper-server，只验证 _build_server_cmd 拼出的启动参数
（model.py 之前零测试，本文件补命令构造层）。
"""

from core.model import WhisperCliBackend


def _make_backend():
    """构造 WhisperCliBackend（不启动 server），便于测 _build_server_cmd。"""
    b = WhisperCliBackend(cli_path='/fake/bin/whisper-cli', language='zh')
    b._model_path = '/fake/model.bin'
    b._server_port = 12345
    return b


def test_build_server_cmd_includes_suppress_nst():
    """-sns 默认开：抑制静音/噪音段非语音 token 幻觉（如「。」、「谢谢观看」），听写更干净。"""
    cmd = _make_backend()._build_server_cmd()
    assert '-sns' in cmd


def test_build_server_cmd_core_flags():
    """核心 flag 仍在：-m 模型 / -l 语言 / -nt 无时间戳 / --port。"""
    cmd = _make_backend()._build_server_cmd()
    assert '-m' in cmd and '/fake/model.bin' in cmd
    assert '-l' in cmd and 'zh' in cmd
    assert '-nt' in cmd
    assert '--port' in cmd and '12345' in cmd


def test_build_server_cmd_prompt_when_set():
    """initial_prompt 非空时追加 --prompt（glossary/风格 prompt 注入路径）。"""
    b = _make_backend()
    b._initial_prompt = '术语：Terraform'
    cmd = b._build_server_cmd()
    assert '--prompt' in cmd
    assert '术语：Terraform' in cmd
