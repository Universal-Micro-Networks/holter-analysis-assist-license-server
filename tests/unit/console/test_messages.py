import pytest

from license_server.console import messages
from license_server.domain.errors import ErrorCode


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_error_code_has_japanese_text_without_internal_detail(code: ErrorCode) -> None:
    text = messages.error_message(code)
    assert text
    assert not text.isascii()


def test_key_messages_explain_what_to_do() -> None:
    assert messages.error_message(ErrorCode.LICENSE_NOT_FOUND) == "ライセンスが見つかりません。"
    assert "操作は完了していません" in messages.error_message(ErrorCode.TEMPORARY_FAILURE)
    assert "しばらく待って" in messages.error_message(ErrorCode.RATE_LIMITED)
    assert "サインイン" in messages.NOT_SIGNED_IN
    assert "実行されていません" in messages.CSRF_REJECTED
    assert messages.DUPLICATE_SUBMISSION == "この操作はすでに実行されています。"
    assert messages.NOT_CONFIGURED == "管理画面が設定されていません。"


def test_success_messages_describe_the_operation() -> None:
    assert messages.issued() == "ライセンスを発行しました。"
    assert messages.limit_updated(1_000) == "月間上限回数を 1,000 回に変更しました。"
    assert messages.suspended() == "ライセンスを停止しました。"
    assert messages.activated() == "ライセンスを再開しました。"
    assert messages.memo_updated() == "ライセンシーを変更しました。"
