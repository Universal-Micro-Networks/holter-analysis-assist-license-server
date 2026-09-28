"""Fixed Japanese wording shown on console pages. Never include internal details or whether other data exists."""

from license_server.domain.errors import ErrorCode

NOT_SIGNED_IN = "サインインが必要です。ページを再読み込みして、サインインしてください。"
CSRF_REJECTED = "画面を開き直してから操作してください。操作は実行されていません。"
DUPLICATE_SUBMISSION = "この操作はすでに実行されています。"
NOT_CONFIGURED = "管理画面が設定されていません。"
PAGE_NOT_FOUND = "ページが見つかりません。"
FORM_HAS_ERRORS = "入力内容に誤りがあります。各項目のメッセージを確認してください。"

_ERRORS: dict[ErrorCode, str] = {
    ErrorCode.INVALID_REQUEST: "入力内容に誤りがあります。",
    ErrorCode.LICENSE_INVALID: "ライセンスが見つかりません。",
    ErrorCode.LICENSE_SUSPENDED: "ライセンスは停止中です。",
    ErrorCode.MONTHLY_LIMIT_REACHED: "当月の利用回数が上限に達しています。",
    ErrorCode.UNAUTHORIZED: NOT_SIGNED_IN,
    ErrorCode.LICENSE_NOT_FOUND: "ライセンスが見つかりません。",
    ErrorCode.RATE_LIMITED: "要求が多すぎます。しばらく待ってから再度操作してください。",
    ErrorCode.TEMPORARY_FAILURE: "一時的な障害が発生しました。操作は完了していません。しばらくしてから再度お試しください。",
}

LIMIT_REQUIRED = "月間上限回数を入力してください。"
LIMIT_INVALID = "月間上限回数は 0 以上 1,000,000 以下の整数で入力してください。"
MEMO_TOO_LONG = "ライセンシーは 200 文字以内で入力してください。"
MEMO_CONTROL_CHARACTER = "ライセンシーに改行や制御文字は使えません。"
QUERY_TOO_LONG = "検索語は 100 文字以内で入力してください。"
STATUS_INVALID = "状態の指定が正しくありません。"
MONTH_INVALID = "対象月は YYYY-MM の形式で指定してください。"
FROM_INVALID = "開始日は YYYY-MM-DD の形式で入力してください。"
TO_INVALID = "終了日は YYYY-MM-DD の形式で入力してください。"
RANGE_REVERSED = "開始日は終了日以前の日付を指定してください。"


def error_message(code: ErrorCode) -> str:
    return _ERRORS[code]


def issued() -> str:
    return "ライセンスを発行しました。"


def limit_updated(monthly_limit: int) -> str:
    return f"月間上限回数を {monthly_limit:,} 回に変更しました。"


def suspended() -> str:
    return "ライセンスを停止しました。"


def activated() -> str:
    return "ライセンスを再開しました。"


def memo_updated() -> str:
    return "ライセンシーを変更しました。"
