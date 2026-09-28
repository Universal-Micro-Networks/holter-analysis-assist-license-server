# Research & Design Decisions

## Summary
- **Feature**: `admin-console`
- **Discovery Scope**: Extension（既存の License Usage Service への画面追加）。ただし運営者認証というセキュリティ上重要な要素を含むため、認証部分はフル調査の観点で扱う。
- **Key Findings**:
  - 既存のサービス層（`LicenseService` / `UsageService`）は画面からそのまま再利用できる。足りないのは「一覧・検索」「メモ」「操作記録」「運営者の識別」の 4 点で、いずれもデータ追加を伴う。
  - 既存の管理 API は共有トークン方式のため、ブラウザから直接呼ぶ構成（トークンをブラウザに置く）は要件 1・6・7 を満たせない。画面は同じ Worker 内でサーバー側描画し、サービスを直接呼ぶ構成が最も既存資産を活かせる。
  - D1 は 50 バイトを超える LIKE パターンを拒否するため、日本語のメモ検索（1 文字 3 バイト）は LIKE ではなく `instr()` で行う必要がある。

## Gap Analysis（`/kiro/validate-gap`）

### 現状の資産と規約
- レイヤー: `domain` → `config` → `repository` → `services` → `http` → `worker.py`。依存は左から右の一方向。
- Flask アプリは `create_app(dependencies)` で組み立て、依存は `Dependencies`（サービス・レート制限・管理トークン）をリクエスト単位で生成する。Blueprint は `client_api`（`/v1`）と `admin_api`（`/v1/admin`）。
- SQL は `repository/sql.py` に集約し、D1 実装とテスト用 SQLite 実装で共有する。スキーマは `migrations/` が正本。
- 応答はすべて JSON エンベロープ。共通エラーハンドラが `ServiceError`・`HTTPException`・`RepositoryUnavailable` を JSON に変換する。
- アクセスログは `after_request` で JSON 1 行。ライセンスキーはフィンガープリントのみ。
- テストは pytest（unit / contract / integration）。実ランタイム確認は `scripts/` と docker compose。
- Flask 3.1.3 に Jinja2 3.1.6・itsdangerous 2.2.0・MarkupSafe が同梱済みで、サーバー側描画と署名付き Cookie は追加依存なしで使える。

### 要件と既存資産の対応

| 要件 | 既存資産 | ギャップ |
|------|----------|----------|
| 1 サインイン・セッション | 管理 API の共有トークン認証（`require_admin`） | **Missing**: 運営者の個別識別、ブラウザ用セッション、サインイン画面。**Constraint**: 共有トークンでは操作者を区別できない |
| 2 一覧・検索 | なし（単一キーの取得のみ） | **Missing**: 一覧・検索・状態絞り込み・ページ分けの SQL とサービス、当月利用回数の一括集計。**Constraint**: D1 の LIKE 50 バイト制限 |
| 3 発行 | `LicenseService.issue`、上限値検証 | **Missing**: メモの入力と保存、フォームの項目別エラー表示 |
| 4 詳細・変更 | `get` / `update_limit` / `suspend` / `activate`、`current_summary` 相当 | **Missing**: メモ更新、確認画面、上限を利用回数未満にする場合の警告 |
| 5 利用状況・履歴 | `monthly_count`、`list_logs`（カーソル方式）、`parse_timestamp` / `parse_month` | **Missing**: 日本時間での表示・入力変換、期間未指定時の当月既定 |
| 6 操作の記録 | なし（アクセスログのみ） | **Missing**: 操作記録テーブル、変更前後の値、詳細画面での表示。サインイン記録（6.4）は認証方式に依存 |
| 7 セキュリティ | キーを URL に載せない方針、HTTPS 前提、ログのフィンガープリント化 | **Missing**: CSRF 対策、埋め込み禁止ヘッダー、テンプレートの自動エスケープ確認。**Constraint**: 画面の URL で個別ライセンスを指す識別子が必要（キーは使えない） |
| 8 表示・障害時 | 共通エラーハンドラ（JSON）、`RepositoryUnavailable` | **Missing**: HTML 用のエラー表示、成功メッセージ、二重送信防止。**Constraint**: 既存の JSON エラーハンドラを画面側に適用しないこと（8.6） |

### 実装方針の選択肢

| 案 | 内容 | 長所 | 短所 |
|----|------|------|------|
| A 既存の管理 API を拡張し、ブラウザの JavaScript から呼ぶ | 静的な画面から `/v1/admin/*` を呼ぶ | サーバー側の画面ロジックが不要 | 管理トークンをブラウザに置くことになり 7 に反する。操作者を識別できず 1・6 を満たせない |
| B 同じ Worker に画面用 Blueprint を新設し、サービスを直接呼ぶ | Flask＋Jinja2 のサーバー側描画。`/console/*` | 既存のサービス・検証・テスト基盤をそのまま使える。追加依存なし。キーをブラウザ側のコードに渡さない | 画面・セッション・CSRF を自前で持つ。Worker の責務が増える |
| C 画面を別の Worker（または Pages）に分け、サービスバインディングで API を呼ぶ | フロントを分離 | デプロイと責務が分かれる | API の追加（一覧・メモ・操作記録）と、Worker 間の運営者情報の受け渡しが必要。構成が複雑 |

- 推奨は **B（既存の拡張と新規コンポーネントの組み合わせ）**。サービス層・リポジトリ・SQL は拡張し、画面・セッション・運営者認証・操作記録は新しいコンポーネントとして分ける。
- **工数**: M〜L（3〜8 日）。画面数は多くないが、認証・CSRF・操作記録・データ追加が重なる。
- **リスク**: Medium。サーバー側描画とデータ追加は既存の型に沿うが、運営者認証の方式と Pyodide 上での署名検証に不確実性がある。

### 設計フェーズへ持ち越す調査項目
- 運営者認証の方式（下記「運営者認証の方式」）と、ローカル開発での代替手段。
- 画面でライセンスを指す識別子（キーを URL に載せないため）。
- 二重送信防止の方式と、D1 の `batch()` によるデータ更新と操作記録の原子性。
- 一覧で当月利用回数を一括で求める SQL の性能。

## Research Log

### 運営者認証の方式
- **Context**: 要件 1（個別サインイン、失敗時の制限、8 時間の無操作で失効）と 6（操作者の記録、サインインの記録）。
- **Sources Consulted**:
  - [Validate JWTs · Cloudflare One docs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
  - [Application token · Cloudflare One docs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/application-token/)
- **Findings**:
  - Cloudflare Access を画面のパスの前段に置くと、IdP（Google Workspace、Microsoft Entra ID、ワンタイム PIN など）でのサインインを Access が担い、オリジンには `Cf-Access-Jwt-Assertion` ヘッダーで RS256 署名の JWT が届く。`email`・`sub`・`aud`・`iss`・`exp` を含む。
  - Worker 側でも JWT の署名・`aud`・`iss`・`exp` の検証が必須（ヘッダーの有無だけでは偽装を防げない）。公開鍵は `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`（現行鍵と直前の鍵の 2 本）。
  - Access を使う場合、サインインの失敗制限・サインイン記録・多要素認証は Access と IdP 側の機能で満たせる。一方、Access のセッションは有効期間の上限で切れる方式で、「無操作 8 時間」は画面側のセッションで補う必要がある。
  - Access はローカルの docker compose 環境には存在しないため、ローカル専用の代替（固定の開発用運営者）を明示的な設定でのみ有効にする必要がある。
  - 自前のアカウント方式（D1 に運営者とパスワードハッシュを保存）も可能。Pyodide の `hashlib.pbkdf2_hmac` が使えるが、Workers の CPU 時間制限の中で十分な反復回数を確保する必要があり、多要素認証も自前になる。
- **Implications**: 認証方式でコンポーネント構成・運用手順・満たし方が大きく変わるため、設計前に方式を確定させる。

### Python Workers での JWT 署名検証
- **Context**: Access 方式を採る場合、RS256 の署名検証を Pyodide 上で行う必要がある。
- **Sources Consulted**:
  - [Work with JavaScript objects, methods, functions and globals from Python Workers](https://developers.cloudflare.com/workers/languages/python/ffi/)
- **Findings**:
  - Python Workers は `js` モジュール経由でランタイムの Web Crypto（`crypto.subtle.importKey` / `verify`）を呼べる。JWK 形式の RSA 公開鍵をそのまま取り込めるため、追加の Python パッケージは不要。
  - 公開鍵の取得は `fetch` を `run_sync` で同期化して行い、Worker のインスタンス内で一定時間キャッシュする。
- **Implications**: 署名検証は小さなアダプタの裏に隠し、テストでは偽の検証器を差し込む。実ランタイムでの検証は実装タスクで行う。

### D1 の LIKE 制限と検索
- **Context**: 要件 2.2（メモ・キーの部分一致検索）。
- **Findings**: D1 は 50 バイトを超える LIKE / GLOB パターンを拒否する（license-usage-service で確認済み）。`instr(haystack, needle) > 0` にはこの制限がない。英字の大文字小文字を区別しない検索は `lower()` を併用する。
- **Implications**: 検索は `instr()` で行い、`sql.py` の既存方針（LIKE を使わない）を維持する。

### D1 の batch とトランザクション
- **Context**: 要件 6.1（操作記録）と 8.5（二重送信防止）を、データ更新と同時に確実に満たす必要がある。
- **Findings**: D1 の `batch()` に渡した文は 1 つのトランザクションとして順に実行され、いずれかが失敗すると全体が取り消される。`INSERT … SELECT` で変更前の値を同じ batch 内で読める。JSON1 の `json_object()` が使える。
- **Implications**: 操作記録の `request_id` に一意制約を付け、記録の挿入とライセンスの更新を同じ batch に入れる。二重送信は一意制約違反として全体が取り消され、ライセンスは 1 回しか変わらない。

### テンプレートと静的ファイルの配信（タスク 1.1）
- **Context**: 画面のテンプレート（`.html`）が Python Workers のバンドルに含まれるかが未確認だった。
- **Findings**:
  - `src/license_server/console/templates/console/*.html` は、`wrangler.jsonc` に `rules` を追加しなくても `wrangler dev` で `jinja2.PackageLoader("license_server.console", "templates")` から読めた。
  - `uv run pywrangler deploy --dry-run --outdir` の出力にも `license_server/console/templates/...html` が含まれ、デプロイ時も同梱される。
  - `"assets": {"directory": "public"}` を追加すると、`/console/assets/console.css` が Worker を通らずに配信された（`Content-Type: text/css`）。
- **Implications**: テンプレートはファイルのまま `PackageLoader` で読む。CSS と JavaScript は `public/console/assets/` に置く。

### 実装時の実ランタイム確認（タスク 2〜5）
- **Context**: テストの SQLite フェイクでは確かめられない D1・Workers の挙動を、docker compose の `wrangler dev` とローカル D1 で確認した。
- **Findings**:
  - D1 の `batch()`: 同じ `request_id` で 2 回目を送ると操作記録の一意制約違反で batch 全体が取り消され、ライセンスは 1 回しか変わらない（`scripts/console_d1_check.py`、`scripts/console_runtime_check.py` の再送・5 並列の同時送信）。例外文は `UNIQUE constraint failed: console_audit_logs.request_id` を含み、これで二重送信とキー重複を区別できる。
  - 操作記録の数値は、束縛値の型に依存せず整数として保存するため `json_object()` 内で `CAST(? AS INTEGER)` を使った。D1 でも `{"monthly_limit":7}` のように整数のまま保存されることを確認した。
  - `NULLIF(?, '')` で接続元 IP が空なら NULL になる。日本語メモの `instr()` 検索は D1 でも動く。
  - Python Workers からの外部 `fetch`（`js.fetch`＋`run_sync`）で `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` を取得できた。Access 設定を入れた一時的な `wrangler dev` に偽のトークンを送ると、取得した実際の公開鍵で検証して `bad_signature` で 403 になる（`scripts/console_access_fetch_check.py`）。
  - ローカルの `wrangler dev` も `CF-Connecting-IP` を付ける（docker では `192.168.65.1`）。操作記録の `source_ip` とレート制限はこの値を使う。
  - コンテナ内で `getPlatformProxy` を使う Node スクリプトは終了せずに止まったため、D1 の確認は `wrangler d1 execute` で行った。
  - `wrangler dev` の要求行ログにはパス（公開 ID を含む）が出るが、アプリのアクセスログは URL ルールのテンプレートと `operator` だけでキー・公開 ID・トークン・Cookie の値は出ない。
  - 実ブラウザでの確認（実装完了後、利用者の報告で発覚）: 当初の `Referrer-Policy: no-referrer` では、Chrome も WebKit もフォーム送信時に `Origin: null` を送り `Referer` も送らないため、送信元の検証ですべての POST（発行・検索・サインアウト）が 403 になっていた。確認スクリプトは `Origin` を自前で付けていたため気づけなかった。`same-origin` に変更し、同一サイトへは送信元が届き、外部サイトへは送らないようにした。
  - WebKit（Safari）は `http://localhost` で `Secure` Cookie を保存しない（Chrome は保存する）。ループバックへの `http` 接続に限り、`Secure` なしの `console-local` Cookie を使うようにした。本番（`https`）は `__Host-console` のまま。
  - これらを再発させないため、ブラウザに Cookie と送信元の扱いを任せる `scripts/console_browser_check.py`（Playwright、Chrome と WebKit）を追加した。
- **Implications**: 二重送信防止と操作記録の原子性は D1 上でも設計どおり成立する。画面の確認は、HTTP クライアントのスクリプトに加えて実ブラウザでも行う。実際の Access トークンでの検証だけはデプロイ後に行う。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| 同一 Worker のサーバー側描画 | Flask Blueprint＋Jinja2、サービスを直接呼ぶ | 既存資産の再利用、追加依存なし、キーをブラウザのコードに渡さない | Worker の責務が増える | 採用 |
| 静的画面＋既存管理 API | ブラウザの JavaScript から `/v1/admin/*` | サーバー側の画面ロジック不要 | 管理トークンをブラウザに置く、操作者を識別できない | 不採用 |
| 別 Worker のフロント | サービスバインディングで API を呼ぶ | 責務の分離 | API 追加と運営者情報の受け渡しが必要 | 規模に対して過剰 |

## Design Decisions

### Decision: 運営者のサインインを Cloudflare Access に任せる
- **Context**: 要件 1（個別サインイン・失敗制限・無操作失効）と 6（操作者の記録）。
- **Alternatives Considered**:
  1. Cloudflare Access — IdP 連携、多要素認証、失敗制限、サインイン記録を Access が担う
  2. 自前のアカウント — D1 に運営者とパスワードハッシュ、サインイン画面・失敗制限・記録を自作
- **Selected Approach**: 1（ユーザー決定、2026-09-28）。`/console` と `/console/*` に Access を適用し、Worker は `Cf-Access-Jwt-Assertion` を検証して `email`・`sub` で運営者を識別する。
- **Rationale**: 医療系サービスの管理画面として多要素認証を自前で持たずに済み、パスワードを保存しない。運営者は 50 人未満で Zero Trust の無料枠に収まる。
- **Trade-offs**: 無操作失効（1.5）は Access のセッション方式では満たせないため、画面側セッションで補い、失効時は Access のログアウトへ送る。ローカル開発には Access がないため、ループバック限定の開発用運営者を用意する。
- **Follow-up**: デプロイ後に実トークンで検証する。Access のセッション期間を運用で設定する。

### Decision: RS256 の署名検証を純 Python で行う
- **Context**: Access JWT の検証に RS256 が必要。Pyodide には `cryptography` を追加するか、`js` 経由で Web Crypto を呼ぶ選択肢がある。
- **Alternatives Considered**:
  1. Web Crypto（`crypto.subtle.verify`）を FFI で呼ぶ — プラットフォーム実装を使える
  2. 純 Python — `pow(signature, e, n)` で復元し、期待する EMSA-PKCS1-v1_5 符号化と全体一致で比較する（RFC 8017 §8.2.2）
- **Selected Approach**: 2。
- **Rationale**: 通常の pytest で本番コードそのものを検証でき（固定の RSA 鍵ペアで署名を生成）、FFI のモックが不要。検証のみで秘密鍵を扱わず、符号化の全体一致比較は署名解析を伴わないため、PKCS#1 v1.5 の典型的な偽造手法の影響を受けない。公開指数 65537 のべき乗は高速。
- **Trade-offs**: 自前実装であり、レビューで比較方式（全体一致・長さ一致・`alg` 固定）を重点確認する必要がある。
- **Follow-up**: 公開鍵の取得（`js.fetch`）は実ランタイムで確認する。

### Decision: 画面でライセンスを指す識別子として `public_id` を追加する
- **Context**: 要件 7.1（キーを URL に含めない）。詳細ページの URL にライセンスを指す値が必要。
- **Alternatives Considered**:
  1. SQLite の `rowid` — `INTEGER PRIMARY KEY` を持たないテーブルでは `VACUUM` で変わり得る
  2. POST だけで詳細を表示 — 再読み込み・戻る操作・リンク共有ができない
  3. ランダムな `public_id` 列を追加
- **Selected Approach**: 3。`lic_` ＋ 16 桁の 16 進数を SQL の `randomblob(8)` で生成し、一意索引を付ける。API で発行したライセンスにも自動で付く。
- **Rationale**: 既存のリポジトリのインターフェイスを変えずに、すべてのライセンスに安定した識別子を付けられる。
- **Trade-offs**: 列の追加が必要。`public_id` は秘密ではないが推測困難で、キーの代わりにはならない。

### Decision: 検索条件は POST で受け取りセッションに保存する
- **Context**: 検索語にキーを入力した場合でも URL に残さない（7.1）。
- **Selected Approach**: 検索フォームは POST、条件をセッションに保存して一覧へリダイレクトし、一覧の URL にはページ番号と並び順だけを載せる。
- **Trade-offs**: タブ間で検索条件が共有される。

### Decision: 一覧は 20 件ごとの番号付きページャーと、見出しによる並べ替えにする
- **Context**: 実装後の利用者の要望で、1 ページを 20 件にし、ページャーを設け、月間上限回数・当月の利用回数・発行日時で並べ替えられるようにすることになった（要件 2.4・2.7）。
- **Alternatives Considered**:
  1. 当初どおり 21 件目の有無で「次へ」だけを判定する — 総件数を数えずに済むが、番号付きのページャーと「全 N 件」を出せない
  2. `COUNT(*)` で一致件数を数え、`LIMIT`・`OFFSET` でページを取る
  3. 並べ替え列の値によるキーセット方式 — 当月利用回数は保存された列ではなく集計値のため、条件の組み立てが複雑になる
- **Selected Approach**: 2。絞り込み条件の SQL を件数と検索で共有し、`ORDER BY` は列・向きの対応表からだけ組み立てる。同値は発行日時の新しい順、最後にキーで順序を固定する。並び順は秘密を含まないため URL のクエリ（`sort`・`order`）に載せ、ページ移動・再検索で引き継ぐ。
- **Trade-offs**: 一覧の表示ごとに D1 の往復が 1 回増える。当月利用回数での並べ替えは一致する全行を集計してから並べるため、ライセンス数が大きく増えた場合は月別の集計表などを検討する。

### Decision: 月間上限 0 を「上限なし」とし、画面では月間上限を扱わない
- **Context**: 実装後の利用者の要望で、画面から発行するライセンスの上限の初期値を 0 とし、上限に関わる項目をすべて非表示にすることになった。従来の規則では上限 0 は「1 回も記録できない」ため、そのままでは画面から発行したライセンスが使えない。
- **Alternatives Considered**:
  1. 0 を「上限なし」に変える — 利用記録 API の振る舞いが変わる
  2. 0 の意味は変えずに発行する — 画面から発行したライセンスでは利用記録が常に拒否される
  3. 初期値を最大値（1,000,000）にする — 利用者の指定（初期値 0）と異なる
- **Selected Approach**: 1（利用者が選択）。利用記録の条件付き INSERT を `monthly_limit = 0 OR 当月件数 < monthly_limit` にし、`UsageSummary.remaining` は上限なしのとき `None`（JSON では `null`）にする。画面は上限の入力・表示・変更フォームをなくし、上限変更の経路と管理 API は残す。
- **Trade-offs**: 利用状況・記録結果の `remaining` が `null` になり得るため、クライアントアプリは `null` を「上限なし」として扱う必要がある。応答が変わるのは上限 0 のライセンスだけで、それらは従来は利用記録が常に拒否されていた（実用上は使われていなかった）。上限を付けたいライセンスは管理 API で上限を設定する（画面には表示されない）。

### Decision: 画面セッションは Flask 標準のセッションではなく自前の署名付き Cookie にする
- **Context**: 設計では Flask の署名付き Cookie セッションを想定していたが、Workers では `CONSOLE_SESSION_SECRET` を要求ごとの環境からしか読めず、アプリ生成時に `secret_key` を固定できない。
- **Selected Approach**: itsdangerous の `URLSafeSerializer`（Flask の同梱依存）で署名した `__Host-console` Cookie を、ガードで読み込み、`after_request` で保存する `ConsoleSession` を実装した。
- **Trade-offs**: Flask の `session`・`flash` は使わない。代わりにフラッシュと検索条件も `ConsoleSession` が持つ。5xx の応答では保存しない。

### Decision: 画面の CSS フレームワークに Bootstrap 5.3 を同梱して使う
- **Context**: 実装後の利用者の要望で、モダンな CSS フレームワークで画面を作り直し、ページの大きな見出しをなくし、発行はハンバーガーメニューから開くことになった。
- **Alternatives Considered**:
  1. Bootstrap 5.3 — offcanvas でハンバーガーメニューを追加の実装なしに作れる。ビルド不要
  2. Tailwind CSS — ビルド工程（CLI）が必要になり、Workers の開発フローに手順が増える
  3. Pico CSS などのクラスレス系 — 軽量だが、メニューやカードの部品は自前になる
- **Selected Approach**: 1。npm の `bootstrap@5.3.8` の `dist` から `bootstrap.min.css`・`bootstrap.bundle.min.js`・`LICENSE` を `public/console/assets/vendor/bootstrap/` に置き、Static Assets で配信する（ソースマップの参照行は削除）。
- **Trade-offs**: CSP は `'self'` のみのため CDN は使えず、ファイルを同梱する（約 310 KB、Access の保護範囲内）。Bootstrap の CSS は SVG アイコン（メニュー、選択欄の矢印、閉じるボタン）を `data:` 画像として埋め込んでいるため、`img-src` に `data:` を追加した。スクリプト・スタイルの制限は変えていない。Bootstrap の JavaScript は CSSOM でスタイルを操作するため、`style-src 'self'` のままでもメニューが動くことを Chrome と WebKit で確認した（ブラウザのコンソールにエラーなし）。
- **Follow-up**: Bootstrap を更新するときは同じ手順でファイルを差し替え、`scripts/console_browser_check.py` を実行する。

### Decision: 開発用運営者は Access の設定が 1 つでもあれば無効にする
- **Context**: 設計では「`ACCESS_AUD` 未設定」を条件にしていた。
- **Selected Approach**: `ACCESS_TEAM_DOMAIN`・`ACCESS_AUD` のどちらかが設定されていれば開発用運営者を作らない。片方だけの設定は検証器も作らず、画面は 503（未設定）になる。
- **Rationale**: 設定の途中状態でも開発用の迂回路が開かないようにする（フェイルクローズ）。

### Decision: 操作記録は画面からの操作だけを対象にする
- **Context**: 要件 6.1。管理 API には運営者を識別する情報がない。
- **Selected Approach**: `ConsoleService` 経由の変更だけを `console_audit_logs` に記録する。管理 API の操作は既存のアクセスログ（フィンガープリント付き）で追う。
- **Trade-offs**: API 経由の変更は操作者が残らない。運用上は管理 API の利用を緊急時に限る。

## Risks & Mitigations
- テンプレートが Workers のバンドルに含まれない — 最初のタスクで実ランタイム確認。含まれなければ Python モジュールへの埋め込みに切り替える。
- 自前の RS256 検証の誤り — 改ざん・`alg` 差し替え・長さ違いを網羅する単体テストと、比較方式のレビュー。
- 開発用運営者が本番で有効になる — `ACCESS_AUD` 未設定かつループバックに限定し、設定テストで `wrangler.jsonc` に含まれないことを確認。
- Access の設定漏れで `/console` が公開される — Worker 側で JWT を必須検証するため、Access がなくても画面は表示されない（403）。

## References
- [Validate JWTs · Cloudflare One docs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/) — JWT 検証の要件と公開鍵エンドポイント
- [Application token · Cloudflare One docs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/application-token/) — JWT のクレーム
- [Python Workers FFI](https://developers.cloudflare.com/workers/languages/python/ffi/) — `js` モジュール経由の fetch・Web Crypto
- [RFC 8017 §8.2.2](https://www.rfc-editor.org/rfc/rfc8017#section-8.2.2) — RSASSA-PKCS1-v1_5 の検証手順
