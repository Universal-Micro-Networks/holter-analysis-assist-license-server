# holter-analysis-assist-license-server

ホルター心電図解析支援アプリのライセンスと推論利用回数を管理するサーバーです。Cloudflare Workers（Python Workers）上の Flask アプリで、データは Cloudflare D1 に保存します。

- クライアント向け API: ライセンスの有効性確認、推論利用の記録（月間上限による制限付き）、当月の利用状況の照会
- 管理 API: ライセンスの発行・参照・上限変更・停止・再開、利用履歴と月別利用回数の参照
- 管理画面（`/console`）: 運営者がブラウザでライセンスを発行・検索・停止・再開し、操作記録と利用履歴を確認する画面（Cloudflare Access で保護）

仕様と設計は `.kiro/specs/` にあります。

| spec | 内容 |
|------|------|
| [license-usage-service](.kiro/specs/license-usage-service/) | API・利用回数の規則・データモデル・デプロイ |
| [admin-console](.kiro/specs/admin-console/) | 管理画面・Access 連携・操作記録 |

## 必要なもの

- Docker（ローカル開発はコンテナで行います）
- ホストで直接動かす場合: [uv](https://docs.astral.sh/uv/)、Node.js 24（npm 11）

## ローカル開発

```sh
docker compose up -d app
```

初回起動時に `.dev.vars.example` から `.dev.vars` が作られ、ローカル D1 にマイグレーションが適用されてから `http://localhost:8787` で開発サーバーが起動します。起動の確認は `curl http://localhost:8787/healthz` です。

`.dev.vars` はローカル専用の Secret です（git の管理対象外）。値はローカル用のダミーのままで動きます。

- `ADMIN_API_TOKEN`: 管理 API の Bearer トークン
- `CONSOLE_SESSION_SECRET`: 管理画面のセッション Cookie の署名鍵
- `CONSOLE_DEV_OPERATOR_EMAIL`: ローカル専用。Access なしで管理画面に入るための運営者。`localhost` などループバックのホストでだけ有効で、本番には設定しません

管理画面は `http://localhost:8787/console` を開きます。

## テスト

```sh
uv run pytest                          # ホストで実行
docker compose run --rm test           # コンテナで実行
uv run --with mypy mypy --strict src/license_server src/worker.py
```

テストは SQLite による D1 の代替実装と、本番と同じマイグレーション SQL を使います。開発サーバーに対する確認スクリプトは `scripts/` にあります。

| スクリプト | 確認内容 |
|-----------|---------|
| `scripts/smoke_flow.sh` | API の通し操作（発行 → 確認 → 記録 → 停止 → 再開 → 上限到達 → 履歴）。応答の本文はライセンスキーを伏せて表示します |
| `scripts/concurrency_check.py` | 同じライセンスへの同時要求で月間上限を超えないこと |
| `scripts/console_smoke.py` | 管理画面の主要操作 |
| `scripts/console_runtime_check.py` | 管理画面のセキュリティヘッダー、API が変わらないこと、二重送信の防止 |
| `scripts/console_browser_check.py` | 実ブラウザ（Chrome・WebKit）での管理画面の操作（Playwright が必要） |
| `scripts/console_d1_check.py` | ローカル D1 での SQL の動作 |
| `scripts/console_access_fetch_check.py` | ランタイムから Access の公開鍵を取得できること |

Python スクリプトは `uv run python scripts/<名前>.py` で実行します。

## API

リクエストの形式と応答の詳細は [license-usage-service の設計](.kiro/specs/license-usage-service/design.md) を参照してください。応答はすべて `{"ok": true, "data": ...}` または `{"ok": false, "error": {"code", "message"}}` の形です。

### クライアント向け API

ライセンスキーは `Authorization: Bearer <ライセンスキー>` で送ります（URL には含めません）。

| メソッド | パス | 内容 |
|---------|------|------|
| POST | `/v1/licenses/verify` | ライセンスが有効かを確認し、月間上限回数を返す |
| POST | `/v1/usage` | 推論利用を 1 回記録する。上限に達していれば記録せず拒否する |
| GET | `/v1/usage/current` | 当月の利用回数・月間上限・残り回数・集計期間を返す |

月間上限回数が 0 のライセンスは上限なしです。このとき `remaining` は `null` になります。当月は日本時間の暦月で数えます。

### 管理 API

`Authorization: Bearer <ADMIN_API_TOKEN>` で送ります。参照系も含めてすべて POST で、ライセンスキーは JSON の本文で渡します。

| パス | 内容 |
|------|------|
| `/v1/admin/licenses` | 発行（`monthly_limit`） |
| `/v1/admin/licenses/get` | 参照 |
| `/v1/admin/licenses/update-limit` | 月間上限の変更 |
| `/v1/admin/licenses/suspend` | 停止 |
| `/v1/admin/licenses/activate` | 再開 |
| `/v1/admin/usage/logs` | 期間を指定した利用履歴（ページ送りあり） |
| `/v1/admin/usage/monthly` | 月別の利用回数 |

API と管理画面は、送信元 IP ごとに 60 秒あたり 120 回までに制限されます（両者で共有）。

## 管理画面

- 本番では Cloudflare Access で `/console` を保護し、Worker 側でも Access の JWT を検証します。
- ライセンスはライセンシー（メモ）で管理し、一覧は 20 件ごとのページ送りと、当月の利用回数・発行日時での並べ替えができます。
- 画面から発行するライセンスは月間上限なし（0）です。月間上限の設定・変更は管理 API で行います。
- 変更操作（発行・停止・再開・ライセンシーの変更）はすべて操作記録に残ります。

## デプロイ

手順は各設計の「Deployment」にあります。概要は次のとおりです。

1. `uv run pywrangler d1 create holter-analysis-assist-license` で D1 を作り、`wrangler.jsonc` の `database_id` を置き換える
2. `uv run pywrangler d1 migrations apply DB --remote`
3. `uv run pywrangler secret put ADMIN_API_TOKEN` と `uv run pywrangler secret put CONSOLE_SESSION_SECRET`
4. カスタムドメインの `routes` を追加し、ゾーンで Always Use HTTPS を有効にする
5. Cloudflare Access で `/console` を保護し、`wrangler.jsonc` の `vars` に `ACCESS_TEAM_DOMAIN`・`ACCESS_AUD` を設定する
6. `uv run pywrangler deploy`
