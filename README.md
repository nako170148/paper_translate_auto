# survey_auto

論文の PDF を渡すだけで、**英語（左）／日本語（右）の二段組 PDF** を自動生成するツール。

```
論文.pdf  →  translate_pdf.py  →  論文_bilingual.pdf
```

---

## セットアップ

### 1. 依存パッケージのインストール

```bash
pip3 install pdfplumber deepl python-dotenv deep-translator
```

### 2. 翻訳エンジンの選択

#### パターン A：Google 翻訳（APIキー不要・すぐ使える）

設定不要。そのまま使えます。

#### パターン B：DeepL（高品質・月50万文字無料）

1. [DeepL API Free](https://www.deepl.com/ja/pro-api) で無料登録してAPIキーを取得
2. `.env` ファイルを作成してキーを設定：

```bash
cp .env.example .env
# .env を開いて DEEPL_API_KEY=取得したキー に書き換える
```

> `.env` が未設定の場合は自動的に Google 翻訳にフォールバックします。

---

## 使い方

```bash
cd /path/to/survey_auto
```

### 通常（.tex + PDF を一括生成）

```bash
python3 translate_pdf.py ~/path/to/論文のpdf
```

### .tex ファイルだけ生成（手動で調整してからコンパイルしたい場合）

```bash
python3 translate_pdf.py ~/path/t0/論文のpdf --no-compile
```

### DeepL の残り利用量を確認（DeepL 使用時のみ）

```bash
python3 translate_pdf.py dummy --check-quota
```

出力ファイルは `output/` フォルダに保存されます：

```
output/
├── paper_bilingual.tex
└── paper_bilingual.pdf
```

---

## 制限・注意事項

| 項目 | 内容 |
|------|------|
| 翻訳品質 | DeepL の方が学術文に向いている |
| 数式 | 数式主体の行は自動でスキップ（翻訳しない） |
| 図・表 | キャプションは自動スキップ |
| Google 翻訳 | 1リクエスト 4500 文字制限あり（自動分割） |
| PDF 構造 | 2段組 PDF はテキスト抽出がずれることがあります |
| コンパイル | LuaLaTeX が必要（`lualatex` コマンドが使えること） |

---

## ファイル構成

```
survey_auto/
├── translate_pdf.py   # メインスクリプト
├── requirements.txt   # 依存パッケージ
├── .env.example       # APIキー設定のテンプレート
├── .env               # 実際のAPIキー（Git管理しない）
├── README.md          # このファイル
└── output/            # 生成された .tex / .pdf（自動作成）
```
