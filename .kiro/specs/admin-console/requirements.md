# Requirements Document

## Project Description (Input)
ライセンス利用サービス（license-usage-service）の運営者向け管理画面をきちんと作る。初版では対象外としていた「管理用 Web UI」を、既存の管理 API（`/v1/admin/*`）の上に構築する。運営者がブラウザから、ライセンスの発行・一覧/検索・詳細確認・月間上限の変更・停止・再開、当月および指定月の利用状況の確認、利用履歴の期間指定での閲覧を行えるようにする。実行環境は既存と同じ Cloudflare Workers（Python + Flask）＋ D1 とし、ローカルでは docker compose で動作確認できること。ライセンスキーや管理用の認証情報を URL・ログに残さないという既存のセキュリティ方針を守ること。

## Introduction
Admin Console は、License Usage Service の運営者（社内の限られた担当者）がブラウザから利用する管理画面である。これまで API を直接呼び出して行っていたライセンスの発行・変更・停止・再開と、利用状況・利用履歴の確認を、画面上の操作で安全に行えるようにする。
ライセンスの有効性判定や利用記録の規則（月間上限、暦月の区切り、履歴を削除しないこと）は License Usage Service の既存の規則に従い、Admin Console はそれを変更しない。

### 前提（要確認事項）
以下は初版での仮置きであり、レビューで確定させる。
- 利用者は社内の運営担当者のみ（数名〜十数名）で、一般の利用者や顧客には公開しない。
- 運営者は一人ひとり個別に識別できる方法でサインインし、誰がどの操作をしたかを後から確認できるようにする（現在の共有管理トークンだけでは操作者を区別できないため）。
- 運営者がライセンスを見分けられるよう、ライセンスに「メモ」（顧客名・施設名・契約番号など自由記述）を登録できるようにする。現在のライセンスにはこの情報がないため、License Usage Service 側のデータ追加を伴う。
- 画面の表示言語は日本語、日時の表示は日本時間とする。対象はパソコンの最新ブラウザとし、スマートフォン表示は必須としない。

## Boundary Context
- **In scope**: 運営者のサインイン・サインアウト、ライセンスの一覧・検索・詳細表示、ライセンスの発行、月間上限の変更、停止・再開、メモの登録・変更、当月および指定月の利用状況の表示、期間指定での利用履歴の閲覧、運営者の操作記録
- **Out of scope**: 顧客・エンドユーザー向けの画面、課金・決済、ライセンスや利用履歴の削除、運営者アカウント自体の管理画面（追加・削除は画面外の手段で行う）、グラフやレポートの出力、スマートフォン向けの最適化
- **Adjacent expectations**: License Usage Service のクライアント向け機能（有効性確認・利用記録・利用状況照会）の振る舞いと応答形式は変わらない。ただし、画面から発行するライセンスを上限なしにするため、月間上限 0 を「上限なし」として扱う規則（License Usage Service 要件 3.6）を追加した。既存の管理機能を API から直接呼び出す運用も、引き続き利用できる（月間上限の設定・変更は管理 API で行う）。
- **月間上限の扱い（実装後の変更）**: 画面では月間上限を扱わない。画面から発行するライセンスは月間上限 0（上限なし）で登録し、月間上限に関わる項目（入力欄、一覧の列と並べ替え、詳細の上限・残り回数、上限変更フォーム、操作記録の上限の値）は表示しない。以下の要件のうち月間上限に関わる項目（3.1・3.3 の上限、4.1 の上限と残り回数、4.2、4.6、5.1 の上限と残り回数）は画面に表示しない。

## Requirements

### Requirement 1: 運営者のサインインとセッション
**Objective:** As a 運営者, I want 自分の認証情報で管理画面にサインインしたい, so that 権限のない人に管理操作をされないようにできる

#### Acceptance Criteria
1. When サインインしていない利用者が管理画面のいずれかのページを開く, the Admin Console shall ページの内容を表示せずにサインインを求める
2. When 登録済みの運営者が正しい認証情報でサインインする, the Admin Console shall 管理画面のトップページを表示し、画面上にサインイン中の運営者を表示する
3. If 運営者として登録されていない、または誤った認証情報でサインインが試みられた, then the Admin Console shall サインインを拒否し、アカウントの存在有無を推測できないメッセージを表示する
4. When 運営者がサインアウトする, the Admin Console shall セッションを終了し、以降の操作にサインインを求める
5. While 運営者が一定時間（初期値 8 時間）操作していない, the Admin Console shall セッションを失効させ、次の操作時にサインインを求める
6. If 短時間に同一の送信元からサインインの失敗が繰り返された, then the Admin Console shall 一定時間その送信元からのサインインを制限する

### Requirement 2: ライセンスの一覧と検索
**Objective:** As a 運営者, I want ライセンスを一覧し、目的のライセンスをすばやく探したい, so that 問い合わせや契約変更に迅速に対応できる

#### Acceptance Criteria
1. When 運営者がライセンス一覧を開く, the Admin Console shall 各ライセンスのメモ、状態（有効・停止中）、当月の利用回数、発行日時を一覧表示する（月間上限回数は表示しない）
2. When 運営者がメモの一部またはライセンスキーの一部を入力して Enter キーを押す, the Admin Console shall 条件に一致するライセンスだけを表示する（検索ボタンは置かない）
3. When 運営者が状態を選ぶ, the Admin Console shall 入力中の検索語とあわせて検索し、指定した状態のライセンスだけを表示する
4. When 一覧の件数が 1 ページの表示件数（20 件）を超える, the Admin Console shall ページを分けて表示し、前後のページと番号を指定したページへ移動できるようにし、全件数と表示中の範囲を示す
5. If 条件に一致するライセンスがない, then the Admin Console shall 該当するライセンスがないことを表示する
6. The Admin Console shall 一覧を初期状態では新しく発行されたライセンスから順に表示する
7. When 運営者が当月の利用回数・発行日時のいずれかの見出しを選ぶ, the Admin Console shall その項目で一覧を並べ替え、同じ見出しをもう一度選ぶと昇順と降順を切り替える。並び順はページを移動しても、検索し直しても保たれる

### Requirement 3: ライセンスの発行
**Objective:** As a 運営者, I want 画面からライセンスを発行したい, so that 新しい契約にすぐにライセンスを渡せる

#### Acceptance Criteria
1. When 運営者がメモを入力して発行を実行する, the Admin Console shall 月間上限回数 0（上限なし）でライセンスを発行し、発行されたライセンスキーを表示する
2. When ライセンスキーが表示されている, the Admin Console shall ライセンスキーをクリップボードへコピーする操作を提供する
3. If 月間上限回数が送信され、それが 0 未満、整数以外、または上限（1,000,000）を超える値である, then the Admin Console shall 発行しない（画面の発行フォームには月間上限の入力欄を置かない）
4. If メモが上限の文字数（初期値 200 文字）を超えている, then the Admin Console shall 発行せずに入力内容の誤りを表示する
5. When 発行が完了する, the Admin Console shall 発行したライセンスの詳細を表示できるようにする

### Requirement 4: ライセンスの詳細表示と変更
**Objective:** As a 運営者, I want 1 件のライセンスの状態を確認し、契約に合わせて変更したい, so that 契約変更や不正利用に対応できる

#### Acceptance Criteria
1. When 運営者がライセンスの詳細を開く, the Admin Console shall ライセンスキー、メモ、状態、当月の利用回数、発行日時、最終更新日時を表示する（月間上限回数と残り回数は表示しない）
2. When 運営者が新しい月間上限回数を入力して変更を実行する, the Admin Console shall 月間上限回数を更新し、更新後の内容を表示する
3. When 運営者が有効なライセンスの停止を実行する, the Admin Console shall 実行前に確認を求め、確認後にライセンスを停止状態にする
4. When 運営者が停止中のライセンスの再開を実行する, the Admin Console shall 実行前に確認を求め、確認後にライセンスを有効状態に戻す
5. When 運営者がメモを変更して保存する, the Admin Console shall メモを更新し、更新後の内容を表示する
6. If 月間上限回数の変更で、現在の当月利用回数より小さい値が入力された, then the Admin Console shall 当月の残り回数が 0 になり以降の利用が拒否されることを示して確認を求める
7. If 変更内容が不正な値である, then the Admin Console shall 変更を行わずに入力内容の誤りを表示する
8. The Admin Console shall ライセンスおよび利用履歴を削除する操作を提供しない

### Requirement 5: 利用状況と利用履歴の閲覧
**Objective:** As a 運営者, I want ライセンスごとの利用状況と利用履歴を確認したい, so that 利用実績の把握や問い合わせへの回答ができる

#### Acceptance Criteria
1. When 運営者がライセンスの詳細で対象月を選ぶ, the Admin Console shall その月の利用回数を表示する
2. When 運営者が期間を指定して利用履歴を表示する, the Admin Console shall 指定期間内の利用日時を古い順に一覧表示する
3. When 利用履歴の件数が 1 ページの表示件数（初期値 100 件）を超える, the Admin Console shall 続きを順に表示できるようにする
4. If 期間の開始が終了より後である、または日時の形式が不正である, then the Admin Console shall 履歴を表示せずに入力内容の誤りを表示する
5. The Admin Console shall 日時を日本時間で表示し、集計の月の区切りを License Usage Service と同じ日本時間の暦月とする
6. When 運営者が利用履歴の期間を指定しない, the Admin Console shall 当月の利用履歴を表示する

### Requirement 6: 操作の記録
**Objective:** As a サービス運営の責任者, I want 誰がいつどのライセンスに何をしたかを確認したい, so that 誤操作や不正な操作を後から追跡できる

#### Acceptance Criteria
1. When 運営者がライセンスの発行、月間上限の変更、停止、再開、メモの変更を行う, the Admin Console shall 操作した運営者、操作日時、操作の種類、対象ライセンス、変更前後の値を記録する
2. When 運営者がライセンスの詳細を開く, the Admin Console shall そのライセンスに対する操作の記録を新しい順に表示する
3. The Admin Console shall 操作の記録を画面から変更・削除する手段を提供しない
4. The Admin Console shall サインインの成功と失敗を、日時と送信元とともに記録する

### Requirement 7: セキュリティ
**Objective:** As a サービス運営者, I want 管理画面を通じた情報漏えいや不正操作を防ぎたい, so that ライセンスと利用実績を安全に管理できる

#### Acceptance Criteria
1. The Admin Console shall ライセンスキーをページの URL（パスおよびクエリ）に含めない
2. The Admin Console shall 暗号化された通信経路でのみ利用でき、暗号化されていない接続では画面を表示しない
3. If 管理画面の外部から誘導された変更操作の要求を受けた, then the Admin Console shall その操作を実行しない
4. The Admin Console shall ライセンスキー、認証情報、セッション情報をログに平文のまま出力しない
5. The Admin Console shall 管理画面のページを他のサイトに埋め込んで表示させない
6. The Admin Console shall 運営者が入力したメモなどの値を、画面上でプログラムとして実行されない形で表示する

### Requirement 8: 表示と障害時の振る舞い
**Objective:** As a 運営者, I want 操作の結果と失敗の理由をわかりやすく知りたい, so that 迷わずに次の対応ができる

#### Acceptance Criteria
1. When 変更操作が成功する, the Admin Console shall 成功したことと操作の内容を画面に表示する
2. If 操作対象のライセンスが存在しない, then the Admin Console shall ライセンスが見つからないことを表示する
3. If データの保存や読み込みに失敗した, then the Admin Console shall 内部の詳細情報を含めずに一時的な障害であることを表示し、操作が完了していないことを明示する
4. If 送信元単位の要求制限を超えた, then the Admin Console shall しばらく待ってから再度操作するよう表示する
5. When 同じ変更操作が短時間に二重に送信される, the Admin Console shall 操作を二重に実行しない
6. The Admin Console shall 管理画面の追加によって、クライアント向け機能の応答形式と振る舞いを変えない
