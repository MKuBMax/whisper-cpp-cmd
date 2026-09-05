"""结果胶囊停留时长回归：松手后的成功/失败提示统一 1.0 秒消失。"""
import inspect

from ui.feedback import FeedbackController


def test_result_capsule_hides_after_one_second():
    params = inspect.signature(FeedbackController.show_message).parameters
    assert params["timeout"].default == 1.0
