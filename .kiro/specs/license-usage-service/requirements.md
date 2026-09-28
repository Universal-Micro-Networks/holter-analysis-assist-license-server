# Requirements Document

## Project Description (Input)
ローカルにインストールしたアプリのライセンスと、そこで実行された推論回数を確認するためのサービスを作る。
実行環境は Cloudflare で、Python + Flask、データベースは D1 の構成とする。

データベースの設計案は以下のとおり。

1. licenses テーブル（基本情報）
   「現在の回数」は持たず、代わりに「月間上限回数」などのプラン情報を持つ。
   - license_key：ライセンスキー
   - monthly_limit：1ヶ月に使える最大回数
2. usage_logs テーブル（利用履歴）
   推論が実行されるたびに、ここに1行データを追加する。
   - id：自動採番ID
   - license_key：どのライセンスで使われたか
   - used_at：利用した日時（タイムスタンプ）

## Introduction
License Usage Service は、利用者の端末にインストールされたアプリ（以下「クライアントアプリ」）のライセンスが有効かを判定し、そのライセンスで実行された推論の回数を記録・集計するサーバーサービスである。
ライセンスごとに月間上限回数（プラン情報）を持ち、推論1回ごとに利用履歴を1件記録する。当月の利用回数は、保持している「現在の回数」ではなく利用履歴から算出する。

### 前提（要確認事項）
以下は初版での仮置きであり、レビューで確定させる。
- 「1ヶ月」は暦月（毎月1日 00:00 〜 月末 23:59:59）とし、月の区切りは日本時間（Asia/Tokyo）で判定する。
- 当月の利用回数が月間上限に達している場合、推論の利用記録を拒否する（クライアントアプリは推論を実行しない）。
- ライセンスの発行・変更・無効化は、サービス運営者が管理用の手段で行う。

## Boundary Context
- **In scope**: ライセンスの有効性判定、推論利用の記録、月間上限による利用可否の判定、当月利用状況の照会、運営者によるライセンス管理、利用履歴の参照
- **Out of scope**: クライアントアプリ側の推論処理そのもの、課金・決済、ライセンスの購入画面、エンドユーザー向けの管理画面、クライアントアプリがオフラインのときの利用制御
- **Adjacent expectations**: クライアントアプリは推論を実行する前に本サービスへ利用を申請し、許可された場合のみ推論を実行する。推論の入力データ・結果（心電図データ等）は本サービスに送信しない。

## Requirements

### Requirement 1: ライセンスの有効性判定
**Objective:** As a クライアントアプリ, I want ライセンスキーが有効かどうかを確認したい, so that 正規のライセンスでのみアプリを利用可能にできる

#### Acceptance Criteria
1. When クライアントアプリが登録済みかつ有効なライセンスキーで有効性の確認を要求する, the License Usage Service shall 有効であることと、そのライセンスの月間上限回数を返す
2. If 登録されていないライセンスキーで確認が要求された, then the License Usage Service shall ライセンスが無効であることを示すエラーを返す
3. If 無効化されたライセンスキーで確認が要求された, then the License Usage Service shall ライセンスが利用停止中であることを示すエラーを返す
4. If ライセンスキーが指定されていない、または形式が不正な要求を受けた, then the License Usage Service shall 要求が不正であることを示すエラーを返す
5. The License Usage Service shall 無効なライセンスキーに対する応答で、他のライセンスの存在や内容を推測できる情報を返さない

### Requirement 2: 推論利用の記録
**Objective:** As a クライアントアプリ, I want 推論を実行するたびに利用を記録したい, so that ライセンスごとの推論回数を正確に把握できる

#### Acceptance Criteria
1. When 有効なライセンスで当月の利用回数が月間上限未満の状態で推論利用の記録が要求される, the License Usage Service shall 利用履歴にライセンスキーと利用日時を含む記録を1件追加する
2. When 推論利用の記録が成功する, the License Usage Service shall 利用が許可されたことと、記録後の当月利用回数および残り回数を返す
3. The License Usage Service shall 利用日時をサーバー側の時刻で記録し、クライアントアプリが申告した時刻を利用回数の集計に用いない
4. If 無効なライセンスキーまたは利用停止中のライセンスで記録が要求された, then the License Usage Service shall 利用履歴を追加せずにエラーを返す
5. If 利用履歴の保存に失敗した, then the License Usage Service shall 利用を許可せず、一時的な障害であることを示すエラーを返す
6. The License Usage Service shall 利用履歴に推論の入力データや推論結果を保存しない

### Requirement 3: 月間上限による利用制限
**Objective:** As a サービス運営者, I want ライセンスごとに月間の推論回数を上限までに制限したい, so that プランに応じた利用量を守らせることができる

#### Acceptance Criteria
1. The License Usage Service shall 当月の利用回数を、当月内に記録された利用履歴の件数として算出する
2. If 当月の利用回数が月間上限回数に達している状態で推論利用の記録が要求された, then the License Usage Service shall 利用履歴を追加せず、月間上限に達したことを示すエラーを返す
3. When 同一ライセンスで同時に複数の推論利用の記録が要求される, the License Usage Service shall 当月の利用回数が月間上限回数を超えないように記録を受け付ける
4. When 新しい月が始まる, the License Usage Service shall 前月までの利用履歴を当月の利用回数に含めずに判定する
5. When 運営者が月間上限回数を変更する, the License Usage Service shall 以降の利用可否の判定に変更後の上限回数を用いる

### Requirement 4: 当月利用状況の照会
**Objective:** As a クライアントアプリ, I want 当月の利用回数と残り回数を確認したい, so that 利用者に残り回数を表示できる

#### Acceptance Criteria
1. When 有効なライセンスキーで利用状況の照会が要求される, the License Usage Service shall 当月の利用回数、月間上限回数、残り回数、および集計対象期間を返す
2. The License Usage Service shall 利用状況の照会によって利用履歴を追加しない
3. If 無効なライセンスキーまたは利用停止中のライセンスで照会が要求された, then the License Usage Service shall 利用状況を返さずにエラーを返す

### Requirement 5: ライセンスの管理
**Objective:** As a サービス運営者, I want ライセンスを発行・変更・無効化したい, so that 契約状況に合わせてライセンスを運用できる

#### Acceptance Criteria
1. When 運営者が月間上限回数を指定してライセンスの発行を要求する, the License Usage Service shall 他と重複しないライセンスキーを持つライセンスを登録し、そのライセンスキーを返す
2. When 運営者が既存ライセンスの月間上限回数の変更を要求する, the License Usage Service shall そのライセンスの月間上限回数を更新する
3. When 運営者がライセンスの無効化を要求する, the License Usage Service shall そのライセンスを利用停止状態にし、以降の利用記録を拒否する
4. When 運営者が無効化されたライセンスの再有効化を要求する, the License Usage Service shall そのライセンスを有効状態に戻す
5. If 月間上限回数に0未満の値または整数以外の値が指定された, then the License Usage Service shall 登録・更新を行わずにエラーを返す
6. The License Usage Service shall ライセンスを無効化しても、そのライセンスの利用履歴を削除しない

### Requirement 6: 利用履歴の参照
**Objective:** As a サービス運営者, I want ライセンスごとの利用履歴と月別の利用回数を確認したい, so that 利用実績の把握や問い合わせ対応ができる

#### Acceptance Criteria
1. When 運営者がライセンスキーと期間を指定して利用履歴を要求する, the License Usage Service shall 指定期間内の利用日時の一覧を返す
2. When 運営者がライセンスキーと対象月を指定して月別の利用回数を要求する, the License Usage Service shall その月の利用回数を返す
3. The License Usage Service shall 記録済みの利用履歴を変更・削除する手段を、クライアントアプリに提供しない

### Requirement 7: アクセス制御
**Objective:** As a サービス運営者, I want クライアントアプリ向けの機能と管理機能へのアクセスを区別したい, so that 第三者による不正な利用や改ざんを防げる

#### Acceptance Criteria
1. If 運営者としての認証情報を持たない要求が管理機能（ライセンス管理・利用履歴の参照）に対して行われた, then the License Usage Service shall 処理を行わずに認証エラーを返す
2. The License Usage Service shall ライセンスキーおよび認証情報を暗号化された通信経路でのみ受け付ける
3. If 短時間に同一の送信元から不正なライセンスキーによる要求が繰り返された, then the License Usage Service shall 一定時間その送信元からの要求を制限する
4. The License Usage Service shall ログにライセンスキーや認証情報を平文のまま出力しない

### Requirement 8: 応答と障害時の振る舞い
**Objective:** As a クライアントアプリの開発者, I want 一貫した形式で結果とエラー理由を受け取りたい, so that クライアントアプリで状況に応じた処理と表示ができる

#### Acceptance Criteria
1. The License Usage Service shall すべての応答で、成功・失敗と、失敗時には理由を識別できるエラー種別を一貫した形式で返す
2. The License Usage Service shall 「ライセンス無効」「利用停止中」「月間上限到達」「要求不正」「認証エラー」「一時的な障害」を互いに区別できるエラー種別として返す
3. If データベースに接続できない、または内部エラーが発生した, then the License Usage Service shall 内部の詳細情報を含めずに一時的な障害であることを示すエラーを返す
