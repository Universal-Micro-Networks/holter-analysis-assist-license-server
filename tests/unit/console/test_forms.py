from datetime import UTC, date, datetime

import pytest

from license_server.console.forms import (
    FormInvalid,
    parse_after_id,
    parse_date_range,
    parse_issue,
    parse_limit,
    parse_memo,
    parse_month,
    parse_page,
    parse_search,
    parse_sort,
)
from license_server.domain.types import LicenseSort, LicenseStatus, Period, SortOrder

NOW = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)


def errors_of(call, *args) -> dict[str, str]:  # type: ignore[no-untyped-def]
    with pytest.raises(FormInvalid) as raised:
        call(*args)
    return raised.value.errors


class TestLimit:
    @pytest.mark.parametrize(("value", "expected"), [("0", 0), ("1000000", 1_000_000), (" 25 ", 25), ("007", 7)])
    def test_accepts_integers_in_range(self, value: str, expected: int) -> None:
        assert parse_limit({"monthly_limit": value}) == expected

    def test_missing_value_asks_for_input(self) -> None:
        assert errors_of(parse_limit, {"monthly_limit": " "}) == {"monthly_limit": "月間上限回数を入力してください。"}
        assert errors_of(parse_limit, {}) == {"monthly_limit": "月間上限回数を入力してください。"}

    @pytest.mark.parametrize("value", ["-1", "1000001", "1.5", "abc", "+5", "1e3", "１０", "99999999999999999999"])
    def test_out_of_range_or_non_integer_is_rejected(self, value: str) -> None:
        assert errors_of(parse_limit, {"monthly_limit": value}) == {
            "monthly_limit": "月間上限回数は 0 以上 1,000,000 以下の整数で入力してください。"
        }


class TestMemo:
    def test_strips_surrounding_whitespace(self) -> None:
        assert parse_memo({"memo": "  山田病院  "}) == "山田病院"

    def test_empty_or_missing_is_allowed(self) -> None:
        assert parse_memo({"memo": ""}) == ""
        assert parse_memo({}) == ""

    def test_200_characters_is_allowed(self) -> None:
        assert parse_memo({"memo": "あ" * 200}) == "あ" * 200

    def test_201_characters_is_rejected(self) -> None:
        assert errors_of(parse_memo, {"memo": "あ" * 201}) == {"memo": "ライセンシーは 200 文字以内で入力してください。"}

    @pytest.mark.parametrize("value", ["一行目\n二行目", "a\rb", "tab\there", "nul\x00", "del\x7f", "line\u2028sep"])
    def test_newlines_and_control_characters_are_rejected(self, value: str) -> None:
        assert errors_of(parse_memo, {"memo": value}) == {"memo": "ライセンシーに改行や制御文字は使えません。"}


class TestIssue:
    def test_parses_limit_and_memo(self) -> None:
        form = parse_issue({"monthly_limit": "10", "memo": " 契約A "})
        assert (form.monthly_limit, form.memo) == (10, "契約A")

    def test_reports_every_invalid_field(self) -> None:
        errors = errors_of(parse_issue, {"monthly_limit": "", "memo": "x" * 201})
        assert set(errors) == {"monthly_limit", "memo"}

    def test_absent_limit_means_unlimited(self) -> None:
        assert parse_issue({"memo": "契約B"}).monthly_limit == 0


class TestSearch:
    def test_parses_query_and_status(self) -> None:
        assert parse_search({"q": " 山田 ", "status": "suspended"}) == ("山田", LicenseStatus.SUSPENDED)

    def test_empty_values_clear_the_filter(self) -> None:
        assert parse_search({"q": " ", "status": ""}) == (None, None)
        assert parse_search({}) == (None, None)

    def test_query_up_to_100_characters(self) -> None:
        assert parse_search({"q": "a" * 100}) == ("a" * 100, None)
        assert errors_of(parse_search, {"q": "a" * 101}) == {"q": "検索語は 100 文字以内で入力してください。"}

    def test_unknown_status_is_rejected(self) -> None:
        assert errors_of(parse_search, {"status": "deleted"}) == {"status": "状態の指定が正しくありません。"}


class TestMonth:
    def test_defaults_to_current_jst_month(self) -> None:
        assert parse_month(None, datetime(2026, 9, 30, 15, 0, tzinfo=UTC)) == (2026, 10)
        assert parse_month("", NOW) == (2026, 9)

    def test_parses_year_month(self) -> None:
        assert parse_month("2026-08", NOW) == (2026, 8)

    @pytest.mark.parametrize("value", ["2026-13", "2026-8", "abc", "1999-12", "10000-01"])
    def test_invalid_month_is_rejected(self, value: str) -> None:
        assert errors_of(parse_month, value, NOW) == {"month": "対象月は YYYY-MM の形式で指定してください。"}


class TestDateRange:
    def test_defaults_to_current_jst_month(self) -> None:
        start, end, period = parse_date_range(None, None, NOW)
        assert (start, end) == (date(2026, 9, 1), date(2026, 9, 30))
        assert period == Period("2026-08-31T15:00:00.000Z", "2026-09-30T15:00:00.000Z")

    def test_explicit_range_includes_both_days(self) -> None:
        start, end, period = parse_date_range("2026-09-10", "2026-09-10", NOW)
        assert (start, end) == (date(2026, 9, 10), date(2026, 9, 10))
        assert period == Period("2026-09-09T15:00:00.000Z", "2026-09-10T15:00:00.000Z")

    def test_missing_side_defaults_to_current_month_bound(self) -> None:
        assert parse_date_range("2026-08-01", "", NOW)[:2] == (date(2026, 8, 1), date(2026, 9, 30))
        assert parse_date_range(None, "2026-09-05", NOW)[:2] == (date(2026, 9, 1), date(2026, 9, 5))

    def test_start_after_end_is_rejected(self) -> None:
        assert errors_of(parse_date_range, "2026-09-10", "2026-09-09", NOW) == {
            "from": "開始日は終了日以前の日付を指定してください。"
        }

    @pytest.mark.parametrize("value", ["2026-9-1", "2026-02-30", "yesterday", "2026/09/01", "1999-12-31"])
    def test_invalid_dates_are_rejected_per_field(self, value: str) -> None:
        assert errors_of(parse_date_range, value, "2026-09-10", NOW) == {"from": "開始日は YYYY-MM-DD の形式で入力してください。"}
        assert errors_of(parse_date_range, "2026-09-01", value, NOW) == {"to": "終了日は YYYY-MM-DD の形式で入力してください。"}


@pytest.mark.parametrize(("value", "expected"), [(None, 1), ("", 1), ("3", 3), ("0", 1), ("-2", 1), ("x", 1), ("999999999", 1)])
def test_page_falls_back_to_first_page(value: str | None, expected: int) -> None:
    assert parse_page(value) == expected


@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        (None, None, (LicenseSort.CREATED_AT, SortOrder.DESC)),
        ("monthly_limit", "asc", (LicenseSort.MONTHLY_LIMIT, SortOrder.ASC)),
        ("used", "desc", (LicenseSort.USED, SortOrder.DESC)),
        ("created_at", "asc", (LicenseSort.CREATED_AT, SortOrder.ASC)),
        ("used", None, (LicenseSort.USED, SortOrder.DESC)),
        ("memo", "asc", (LicenseSort.CREATED_AT, SortOrder.ASC)),
        ("monthly_limit; DROP TABLE licenses", "asc", (LicenseSort.CREATED_AT, SortOrder.ASC)),
        ("used", "sideways", (LicenseSort.USED, SortOrder.DESC)),
    ],
)
def test_sort_accepts_only_known_columns_and_orders(sort: str | None, order: str | None, expected: tuple[LicenseSort, SortOrder]) -> None:
    assert parse_sort(sort, order) == expected


@pytest.mark.parametrize(("value", "expected"), [(None, None), ("", None), ("42", 42), ("0", None), ("-1", None), ("x", None)])
def test_after_id_is_a_positive_integer_or_none(value: str | None, expected: int | None) -> None:
    assert parse_after_id(value) == expected
