# Research & Design Decisions

## Summary
- **Feature**: `license-usage-service`
- **Discovery Scope**: New Feature（グリーンフィールド。既存コードなし、steering 未作成）
- **Key Findings**:
  - Cloudflare Python Workers は 2026-09-02 の更新で WSGI フレームワーク（Flask / Django）に正式対応した。`workers.wsgi.entrypoint(app)` で Flask アプリをそのまま Worker として公開できる。
  - Flask のハンドラは同期関数だが、D1 などのバインディングは非同期（JavaScript Promise）である。公式例では `pyodide.ffi.run_sync` で非同期呼び出しを同期ハンドラへ橋渡ししており、バインディングは `request.environ["workers.env"]` から取得する。
  - D1 は SQLite ベースで、各ステートメントを逐次・非並行に実行し、`batch()` はトランザクションとして扱われる。このため「当月件数が上限未満のときだけ INSERT する」を単一の SQL 文で表現すれば、同時要求があっても上限超過を防げる（要件 3.3）。

## Research Log

### Flask を Cloudflare Python Workers で動かせるか
- **Context**: 要件定義時点で、Python Workers が ASGI（FastAPI）中心で Flask（WSGI）が動くか不明だった。
- **Sources Consulted**:
  - [Python Workers now support WSGI web frameworks like Django and Flask](https://developers.cloudflare.com/changelog/post/2026-09-02-python-workers-web-framework-support/)
  - [Flask · Cloudflare Workers docs](https://developers.cloudflare.com/workers/languages/python/packages/flask/)
  - [Packages · Cloudflare Workers docs](https://developers.cloudflare.com/workers/languages/python/packages/)
- **Findings**:
  - `from workers import wsgi` → `Default = wsgi.entrypoint(app)` で公開できる。より細かく制御したい場合は `WorkerEntrypoint` を継承し `wsgi.fetch(app, request, self.env)` を呼ぶ。
  - `wrangler.jsonc` に `compatibility_flags: ["python_workers"]` が必要。依存関係は `pyproject.toml` に書き、`uv run pywrangler dev / deploy` で開発・デプロイする（開発用依存: `workers-py`, `workers-runtime-sdk`）。
  - Flask ハンドラ内では `request.environ["workers.env"]` でバインディングにアクセスし、非同期 API は `run_sync(...)` で待つ。
  - 対応パッケージは pure Python と PyEmscripten ホイール、および Pyodide 同梱パッケージ。Flask は公式にサポート対象。
- **Implications**: ユーザー指定の Python + Flask 構成をそのまま採用できる。D1 アクセスは `run_sync` を経由する薄いアダプタに閉じ込め、ドメイン・サービス層は同期の純 Python に保つ。

### D1 の API とトランザクション特性
- **Context**: 同一ライセンスへの同時要求で月間上限を超えないこと（要件 3.3）を保証する方法が必要。
- **Sources Consulted**:
  - [D1 Database · Cloudflare D1 docs](https://developers.cloudflare.com/d1/worker-api/d1-database/)
- **Findings**:
  - `prepare(sql).bind(...)` で準備済みステートメントを作り、`run()` / `first()` / `all()` で実行する。
  - D1 はオートコミットで、`batch()` 内のステートメントは逐次・非並行に実行され、SQL トランザクションとして扱われる（途中失敗で全体がロールバック）。
  - Python からの結果は JS オブジェクトで、`.to_py()` で Python 値へ変換する。
  - 読み取りレプリカは Sessions API を使った場合のみ有効。未使用ならすべてプライマリで実行される。
- **Implications**:
  - 上限判定と記録は「条件付き INSERT … SELECT」を単一ステートメントで実行し、`meta.changes` で許可／拒否を判定する。直後の当月件数取得を同じ `batch()` に入れて整合した値を返す。
  - 読み取りレプリカは使わない（上限判定に古い値を使わないため）。

### Flask＋`run_sync`＋D1 の実地検証（タスク 1.4）
- **Context**: 設計時のリスク「Flask の同期ハンドラから D1 を呼べるか」「`src/license_server` を import できるか」を `uv run pywrangler dev` で検証した。
- **Findings**:
  - `request.environ["workers.env"].DB` から取得したバインディングに対し、`run_sync(db.prepare(...).first())` と `run_sync(db.batch([...]))` がいずれも動作した。
  - バインディングは `workers.rpc._BindingWrapper` で、結果は SDK が自動で Python 値に変換する。`first()` は `dict` のサブクラス（`workers.rpc.JsDict`）、`batch()` は Python の `list[dict]` を返し、各要素に `meta.changes` / `meta.last_row_id` を含む。ドキュメントにある `.to_py()` は存在せず、呼ぶと `AttributeError` になる。
  - `src/` 配下のパッケージはそのままバンドルされ import できる。ファイル変更は開発サーバーに自動で反映される。
  - Workers ランタイムの Python は 3.14（Pyodide 3.14.2）。ローカルのテストは 3.13 で実行している。
  - pywrangler は起動のたびに `pylock.toml`（Workers 用のロックファイル）をプロジェクト直下に生成する。
- **Implications**:
  - D1 アダプタは `.to_py()` を使わず、返り値を dict / list としてそのまま扱う。
  - `pylock.toml` は生成物として git 管理外にする（依存の正本は `uv.lock`）。
  - 3.14 でのみ使える構文・API は使わない（ローカルテストとの互換のため）。

### D1 と素の SQLite の差異（タスク 1.3 で判明）
- **Context**: `used_at` の形式を GLOB パターンの CHECK 制約で強制したところ、Python 標準の SQLite では通るが、ローカル D1 では挿入のたびに `LIKE or GLOB pattern too complex: SQLITE_ERROR` になった。
- **Findings**:
  - D1 は LIKE / GLOB パターンの長さ上限が素の SQLite より厳しい（約 50 バイト）。制約の評価は挿入時に行われるため、マイグレーション適用時には検出されない。
  - D1 は外部キー制約を既定で有効にしている（素の SQLite は `PRAGMA foreign_keys = ON` が必要）。
- **Implications**:
  - スキーマや SQL で長い LIKE / GLOB パターンを使わない。`used_at` の形式チェックは `strftime` による往復一致で行う。
  - テスト用 SQLite リポジトリは接続時に `PRAGMA foreign_keys = ON` を設定する。
  - SQLite で通ったスキーマ・SQL も、ローカル D1 で必ず確認する。

### レート制限（要件 7.3）
- **Context**: 不正なライセンスキーによる繰り返し要求を送信元単位で制限したい。
- **Sources Consulted**:
  - [Rate Limiting · Cloudflare Workers docs](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/)
- **Findings**:
  - `ratelimits` バインディングで `limit({key})` を呼ぶと `{success}` が返る。期間は 10 秒または 60 秒のみ。Wrangler 4.36.0 以上が必要。
  - カウンタは Cloudflare の拠点ごとにローカルで、結果整合・寛容な設計であり、正確な計数には使えない。
  - `limit()` は呼ぶたびにカウンタを消費し、「残量の確認だけ」はできない。
  - IP アドレスをキーにすることは、共有 IP（NAT 等）で無関係な利用者を巻き込むため推奨されていない。
- **Implications**:
  - 月間上限の計数には使わず（D1 で行う）、乱用防止の粗い制限にのみ使う。
  - 「無効キーの要求だけ」を数えて事前に遮断することは API 上できない（事前に残量を確認できないため）。そこで、ライセンス照合より前に送信元 IP 単位で全要求に緩い上限をかける方式を採る。
  - 総当たりへの耐性は主にライセンスキーの推測困難性（128 ビット乱数）で確保し、レート制限は補助策と位置付ける。

### 月の区切りと時刻表現
- **Context**: 要件の前提で「暦月・日本時間で区切る」とした。Pyodide 環境でのタイムゾーン情報の扱いも考慮が必要。
- **Findings**:
  - 日本時間は夏時間がなく UTC+9 固定のため、`zoneinfo`（tzdata 依存）を使わず固定オフセットで計算できる。
  - SQLite の TEXT 型に固定長の UTC ISO 8601 文字列（ミリ秒・`Z` 付き）で保存すれば、文字列の大小比較が時刻の前後と一致し、範囲検索にインデックスが効く。
- **Implications**: `used_at` は UTC の固定長 ISO 8601 文字列で保存し、当月の範囲は「JST の月初 00:00」〜「翌月初 00:00」を UTC に変換した半開区間で表す。

### Docker / docker-compose でのローカル起動
- **Context**: ユーザー指定（2026-09-28）で、Docker と docker-compose で起動できるようにする。
- **Sources Consulted**:
  - [cloudflare/workers-py](https://github.com/cloudflare/workers-py) の `packages/cli/src/pywrangler/utils.py`, `sync.py`（ソースを直接確認）
- **Findings**:
  - pywrangler は wrangler 本体を `npx --yes wrangler` で呼び出す。したがって実行環境に Node.js（npx）が必要。
  - pywrangler はプロジェクト直下に `.venv-workers/` と `python_modules/`（Workers 用にバンドルする依存）を生成する。
  - ローカル D1 のデータは wrangler の状態ディレクトリ `.wrangler/` に保存される。
- **Implications**:
  - イメージには uv・Python・Node.js を同居させる。wrangler は `package.json` でバージョンを固定し、`npx` が毎回ダウンロードしないようにする。
  - バインドマウント時に生成物がホストへ書き出されないよう、`.venv-workers/`・`node_modules/`・`.wrangler/` を名前付きボリュームで上書きする。
  - `python_modules/` は当初ボリュームにしていたが、タスク 6 で起動失敗が判明した。pywrangler は `pyproject.toml` / `pylock.toml` の更新時刻が同期トークンより新しいと `shutil.rmtree(python_modules)` で作り直し、マウントポイントでは `OSError: [Errno 16] Device or resource busy` になる。中身は OS 非依存の Pyodide 向けパッケージなので、gitignore したうえでバインドマウント内に置く。`.venv-workers/` も Python バージョン不一致時だけ同じ削除が走るが、頻度が低くコンテナ内 Python へのシンボリックリンクを含むためボリュームのままとする。
  - `wrangler dev` は既定で localhost のみで待ち受けるため、コンテナ外から接続できるよう `--ip 0.0.0.0` を指定する。
  - Docker はローカル専用とし、本番は Cloudflare Workers のまま変えない。

### 実行環境での通し検証（タスク 6・7.2）
- **Context**: 本番用の結線（`workers_dependencies`）をローカルの workerd＋D1 で確認する。
- **Findings**:
  - `COUNT_AND_LIMIT` を含む `batch()` は D1 で期待どおり動き、`scripts/smoke_flow.sh`（発行→確認→記録→照会→停止→拒否→再開→記録→上限到達→履歴）がすべて成功した。
  - Rate Limiting バインディングの `limit()` には Python の dict `{"key": ...}` をそのまま渡せる。130 件を連続送信すると、直前の 10 件と合わせて 120 件目までが通り、以降は 429 になった。
  - Python の `print` で書いた JSON 1 行が wrangler のログに出る。キー本体はログに現れず、フィンガープリントのみが出ることを確認した。
  - 上限 10 のライセンスへ 30 件を `threading.Barrier` でそろえて並行送信すると、3 回とも成功 10 件・上限到達 20 件・履歴 10 件だった。
- **Implications**: 単一文の条件付き挿入による直列化で、アプリ側ロックなしに上限超過が起きないことを実行環境でも確認できた。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| レイヤード＋リポジトリ抽象（採用） | HTTP → Service → Repository(Protocol) → D1 アダプタ | 小規模で理解しやすい。D1 を SQLite フェイクに差し替えてローカルでテストできる | 層の数だけファイルが増える | サービスが小さいためヘキサゴナルの完全形までは不要 |
| Flask ルートから直接 D1 呼び出し | ルート関数内で SQL を実行 | ファイル数が最少 | `run_sync` と SQL が散らばり、Workers 外で単体テストしにくい | 不採用 |
| Durable Objects で上限カウンタを管理 | ライセンスごとの DO で計数を直列化 | 強い直列化と高速な計数 | 構成が複雑。Python DO の成熟度と運用コスト | D1 の単一文 INSERT で十分なため不採用 |

## Design Decisions

### Decision: 上限判定と記録を単一 SQL 文で行う
- **Context**: 要件 3.3（同時要求でも上限を超えない）。
- **Alternatives Considered**:
  1. 件数を SELECT してから INSERT する（2 文）— 間に他の要求が割り込むと超過し得る
  2. licenses に現在回数カラムを持ち UPDATE で増やす — ユーザーの設計方針（現在回数を持たない）に反する
  3. `INSERT … SELECT … WHERE (当月件数) < monthly_limit` の単一文（採用）
- **Selected Approach**: 単一の条件付き INSERT を実行し、`meta.changes = 1` なら許可、`0` なら拒否。拒否時は追加の読み取りで理由（未登録・停止中・上限到達）を判定する。記録後の件数取得は同じ `batch()` に含める。
- **Rationale**: D1 はステートメントを逐次実行するため、単一文の中の判定と挿入は他の書き込みと交錯しない。
- **Trade-offs**: 要求ごとに当月件数の COUNT が走る。`(license_key, used_at)` の複合インデックスで範囲走査に限定する。
- **Follow-up**: 実装時にローカル D1 で並行要求テストを行い、超過しないことを確認する。

### Decision: ライセンスに状態カラムを追加する
- **Context**: 要件 1.3, 5.3, 5.4（無効化・再有効化）。ユーザーの設計案には状態を表す項目がない。
- **Selected Approach**: `licenses.status`（`active` / `suspended`）と、監査用の `created_at` / `updated_at` を追加する。
- **Rationale**: 行を削除せずに停止・再開でき、利用履歴を保持できる（要件 5.6）。

### Decision: ライセンスキーを URL に含めない
- **Context**: 要件 7.4（ログにキーを平文で出さない）。Workers のリクエストログには URL が記録される。
- **Selected Approach**: クライアント API はキーを `Authorization: Bearer` ヘッダーで受け取る。管理 API も対象キーを JSON ボディで受け取り、パスやクエリに載せない。
- **Trade-offs**: 管理 API が REST の「リソース URL」形式ではなく、操作ごとの POST になる。

### Decision: 管理者認証は共有トークン（Worker Secret）
- **Alternatives Considered**:
  1. Worker Secret に保存した管理トークンを Bearer で送る（採用）
  2. Cloudflare Access（Zero Trust）で `/v1/admin/*` を保護する
- **Rationale**: 初版は運営者が少人数で、最小構成で要件 7.1 を満たせる。将来 Cloudflare Access を前段に追加しても API 契約は変わらない。
- **Follow-up**: 運営者が増える場合は Cloudflare Access への移行を検討する。

### Decision: Python のパッケージマネージャーに uv を使う
- **Context**: ユーザー指定（2026-09-28）。
- **Selected Approach**: 依存関係・仮想環境・コマンド実行をすべて uv で行う。`uv.lock` をコミットする。
- **Rationale**: Cloudflare 公式の Python Workers 手順が `uv run pywrangler dev / deploy` を前提としており、追加の工夫なしで整合する。

### Decision: 時刻は UTC 固定長文字列、月の区切りは JST 固定オフセット
- **Selected Approach**: `used_at` は `YYYY-MM-DDTHH:MM:SS.mmmZ` 形式（UTC）。月の範囲は JST（UTC+9 固定）で計算した半開区間 `[月初, 翌月初)`。
- **Rationale**: 文字列比較とインデックスでの範囲検索がそのまま使え、tzdata に依存しない。

## Risks & Mitigations
- `run_sync` による同期橋渡しが想定どおり動かない、または遅い — 最初のタスクで最小構成（Flask + D1 1 クエリ）を `pywrangler dev` で動かして確認する。問題があれば `WorkerEntrypoint` で非同期処理を行う方式に切り替える。
- `src/` 配下のサブパッケージが Workers バンドルで import できない — 同じく初期タスクで確認し、必要ならフラットな配置にする。
- 通信断によるクライアントの再送で二重計上される — 初版では許容し、未解決事項として扱う（冪等キーの導入を検討）。
- 病院など共有 IP 環境で送信元単位のレート制限に正規利用が巻き込まれる — 上限値を設定で変更可能にし、正規利用の頻度より十分高く設定する。
- 利用履歴の増加による COUNT の遅延 — 複合インデックスで当月範囲のみを走査する。将来必要なら月次集計テーブルを追加する。

## References
- [Python Workers now support WSGI web frameworks like Django and Flask](https://developers.cloudflare.com/changelog/post/2026-09-02-python-workers-web-framework-support/) — Flask 対応の告知と `workers.wsgi` の使い方
- [Flask · Cloudflare Workers docs](https://developers.cloudflare.com/workers/languages/python/packages/flask/) — 構成ファイル例、`run_sync` と `workers.env` の使い方
- [Packages · Cloudflare Workers docs](https://developers.cloudflare.com/workers/languages/python/packages/) — pywrangler と依存関係の管理
- [D1 Database · Cloudflare D1 docs](https://developers.cloudflare.com/d1/worker-api/d1-database/) — `prepare` / `batch` / トランザクション特性
- [Rate Limiting · Cloudflare Workers docs](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/) — レート制限バインディングの仕様と制約
