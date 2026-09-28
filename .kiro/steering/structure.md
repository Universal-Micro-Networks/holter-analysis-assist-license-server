# Project Structure

## Organization Philosophy

レイヤー構成。依存は一方向で、下のレイヤーは上を知らない。

`domain` → `config` → `repository` → `services` → `http` / `console` → `wiring` → `app` → `worker.py`

- `http`（API）と `console`（管理画面）は互いに import しない。例外として `console` は `http.rate_limit`・`http.access_log`・`http.responses` だけを使う。
- 両方を知るのは本番の依存を組み立てる `wiring` と、アプリを組み立てる `app` だけ。

## Directory Patterns

### Domain（`src/license_server/domain/`）
**Purpose**: 外部に依存しない型・規則・エラー。データクラス、列挙、暦月の計算、キーの生成と検証。
**Example**: `period.py` は JST の暦月を UTC の半開区間 `Period` に変換する。

### Repository（`src/license_server/repository/`）
**Purpose**: 永続化。`base.py` に Protocol と例外、`sql.py` に全 SQL、`d1.py` に D1 実装。
**Example**: 新しいクエリは `sql.py` に定数として追加し、`d1.py` とテスト用フェイク（`tests/fakes/sqlite_repository.py`）の両方から使う。

### Services（`src/license_server/services/`）
**Purpose**: ユースケースと入力検証。Flask に依存せず、`ServiceError(ErrorCode)` で失敗を返す。時計とキー生成は引数で受け取る。

### HTTP API（`src/license_server/http/`）
**Purpose**: Blueprint（`/v1`、`/v1/admin`）、認証・レート制限のガード、JSON エンベロープ、アクセスログ。

### Console（`src/license_server/console/`）
**Purpose**: 管理画面の Blueprint（`/console`）、Access JWT の検証、セッション、CSRF、フォーム解析、日本語メッセージ、テンプレート（`templates/console/`）。静的ファイルは `public/console/assets/`。

### Migrations（`migrations/`）
**Purpose**: D1 のスキーマ。連番の SQL（`NNNN_説明.sql`）を追加するだけで、既存のファイルは変更しない。

### Tests（`tests/`）
**Purpose**: `unit/`（レイヤーごと）、`contract/`（リポジトリ契約）、`integration/`（Flask テストクライアント）、`fakes/`（SQLite・D1・Access のフェイク）。

### Scripts（`scripts/`）
**Purpose**: 開発サーバーに対する確認スクリプト。ライセンスキーは出力しない（出す場合は `lk_<redacted>` に伏せる）。

## Naming Conventions

- **Files / modules**: snake_case（`console_service.py`）
- **Classes**: PascalCase（`ConsoleService`、`D1Repository`）
- **Functions / variables**: snake_case。モジュール内部用は先頭に `_`
- **Constants**: UPPER_SNAKE_CASE（`LICENSE_PAGE_SIZE`、SQL 定数 `INSERT_USAGE_WITHIN_LIMIT`）
- **Tests**: `test_<対象>.py`、テスト名は振る舞いを文で書く（`test_zero_limit_is_unlimited`）

## Import Organization

```python
from license_server.domain.types import License, LicenseStatus  # パッケージ名からの絶対 import
from license_server.repository.base import ConsoleRepository
```

- 相対 import は使わない。`src/` が import パス（pytest は `pythonpath = ["src", "."]`）。

## Code Organization Principles

- ルートは薄く保つ。入力の解析 → サービス呼び出し → 応答の組み立てだけを行う。
- 依存は `Dependencies` / `ConsoleDependencies` のファクトリで要求ごとに生成し、`current()` / `current_console()` で取り出す。テストは SQLite フェイクと固定時計のファクトリを渡す。
- ユーザーに見える文言は、API は英語の固定文言、管理画面は `console/messages.py` の日本語に集約する。
- コメントはコードから読み取れない制約だけを書く。

---
_ファイルの一覧ではなくパターンを書く。パターンに沿った新しいファイルでは更新不要_
