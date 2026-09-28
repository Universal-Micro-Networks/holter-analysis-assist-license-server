# Technology Stack

## Architecture

Cloudflare Workers（Python Workers）上の 1 つの Flask アプリが、クライアント向け API・管理 API・管理画面をすべて提供する。データは D1（SQLite）に置き、管理画面の CSS・JavaScript は Workers Static Assets（`public/`）で Worker より前に配信する。依存（リポジトリ・サービス）はリクエストごとにファクトリで組み立てる。

## Core Technologies

- **Language**: Python 3.13（テスト・型検査）、Workers のバンドルは 3.14（Pyodide）
- **Framework**: Flask 3.1（管理画面は Jinja2 のサーバー側描画）
- **Runtime**: Cloudflare Workers（`python_workers` フラグ）、D1、Rate Limiting バインディング
- **Tooling**: uv、pywrangler（`workers-py`）、Wrangler（`package.json` でバージョン固定）

## Key Libraries

- Flask に同梱の Jinja2・itsdangerous・MarkupSafe 以外の実行時依存を追加しない（Pyodide で動く純 Python に限る。Access JWT の RS256 検証も自前実装）。
- 画面は Bootstrap を `public/` に同梱し、CDN は使わない（CSP を `'self'` に保つため）。

## Development Standards

### Type Safety
- `mypy --strict` を `src/license_server` と `src/worker.py` に適用する。型注釈はすべての関数に付ける。
- 値の受け渡しは `@dataclass(frozen=True)` と `StrEnum`、リポジトリは `Protocol` で抽象化する。

### Security
- ライセンスキー・トークン・Cookie の値をログ・URL・例外メッセージに出さない。ログにはキーのフィンガープリントだけを出す。
- ライセンスキーは `Authorization: Bearer` か POST の本文で受け取る（管理 API は参照系も POST）。
- 秘密値は Worker の Secret（ローカルは `.dev.vars`）に置き、`wrangler.jsonc` の `vars` には入れない。
- エラー応答は固定のコードと文言にし、SQL やスタックトレースを返さない。

### Testing
- pytest。D1 の代わりに標準 `sqlite3` のフェイクを使い、本番と同じマイグレーション SQL と `repository/sql.py` の SQL を流す。
- リポジトリは契約テストでフェイクと D1 実装の意味を揃える。D1 そのものの挙動は開発サーバーに対する確認スクリプト（`scripts/`）で確かめる。
- 設定ファイル（`wrangler.jsonc`・Docker・`.dev.vars.example`・マイグレーション）もテストで検証する。

## Development Environment

### Required Tools
- Docker（開発サーバーとテストの標準環境）
- ホストで動かす場合は uv と Node.js 24（npm 11）

### Common Commands
```bash
# Dev: docker compose up -d app            # http://localhost:8787（ローカル D1 へのマイグレーションも自動）
# Test: uv run pytest                      # または docker compose run --rm test
# Type: uv run --with mypy mypy --strict src/license_server src/worker.py
# Migrate: uv run pywrangler d1 migrations apply DB --local | --remote
# Deploy: uv run pywrangler deploy
```

## Key Technical Decisions

- **月間上限の判定は 1 文の条件付き INSERT**: 件数の確認と記録を同じ SQL で行い、同時要求でも上限を超えない（アプリ側でロックしない）。
- **時刻は UTC の固定形式文字列で保存**: 暦月の境界は日本時間で計算し、UTC の半開区間に変換して比較する。
- **SQL は `repository/sql.py` に集約**: D1 実装とテスト用 SQLite 実装で同じ文を共有する。D1 の制約（LIKE パターン長など）に合わせ、部分一致は `instr()` を使う。
- **管理画面の変更は操作記録と同じ `batch()`**: データ更新と記録を 1 トランザクションで書き、二重送信は `request_id` の一意制約で防ぐ。
- **管理画面は二重の認証**: Cloudflare Access に加えて Worker でも JWT を検証し、設定がなければ 503 で閉じる。

---
_依存の一覧ではなく、判断の基準を書く_
