"""
translate_pdf.py - PDF → 英日二段組 LaTeX (paracol + LuaLaTeX)
"""

import sys
import os
import re
import time
import argparse
import subprocess
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv

load_dotenv()

DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")


def get_translator():
    """DeepL APIキーがあればDeepL、なければGoogleTranslatorを返す"""
    if DEEPL_API_KEY and DEEPL_API_KEY != "your_deepl_api_key_here":
        import deepl
        print("  翻訳エンジン: DeepL（高品質）")
        return "deepl", deepl.Translator(DEEPL_API_KEY)
    else:
        from deep_translator import GoogleTranslator
        print("  翻訳エンジン: Google翻訳（APIキー不要）")
        return "google", GoogleTranslator(source="en", target="ja")

LATEX_PREAMBLE = r"""% !TEX program = lualatex
\documentclass[a4paper,10pt]{article}
\usepackage{luatexja}
\usepackage{paracol}
\usepackage[margin=1in]{geometry}
\usepackage[hidelinks]{hyperref}

\begin{document}
\begin{paracol}{2}
\setlength{\columnsep}{1cm}

"""

LATEX_FOOTER = r"""
\end{paracol}
\end{document}
"""


def is_page_header(text):
    """ページヘッダー/フッターを検出（短いブロックのみ除外）"""
    t = text.strip()
    if len(t) > 120:
        return False
    return bool(re.search(r'(Paper\s*\d+\s*Page\s*\d+|^CHI\s+20\d{2}\s+Paper)', t))


def is_section_header(text):
    """セクション見出しを検出（例: "ABSTRACT", "EARBUDDY DESIGN", "STUDY 2: DATA COLLECTION"）"""
    t = text.strip()
    if len(t) > 80 or len(t) < 4:
        return False
    # 明示的キーワード（単語）
    if t in ('ABSTRACT', 'INTRODUCTION', 'CONCLUSION', 'REFERENCES',
             'ACKNOWLEDGMENTS', 'ACKNOWLEDGEMENT', 'DISCUSSION', 'RESULTS'):
        return True
    # ALL-CAPS 複数語（スペース含む）
    if re.match(r'^[A-Z][A-Z\s:0-9]+$', t) and ' ' in t:
        return True
    return False

def is_math_heavy(text):
    """数式主体のテキストを検出（翻訳スキップ）"""
    if len(text) == 0:
        return False
    math_chars = sum(1 for c in text if c in r'=+−×÷∑∫∂∇αβγδεζθλμπσφωΩΓΔ')
    return (math_chars / len(text) > 0.08) or (text.count('\\') > 3)

def clean_block(text):
    """URL・メールアドレス・DOIを除去し、残ったテキストを返す"""
    # URL
    text = re.sub(r'https?://\S+', '', text)
    # DOI
    text = re.sub(r'doi[:.]?\s*\S+', '', text, flags=re.IGNORECASE)
    # メールアドレス（{xxx}@domain 形式含む）
    text = re.sub(r'\{[^}]*\}@[\w.]+', '', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.\w+', '', text)
    # 連続スペースを整理
    text = re.sub(r'\s{2,}', ' ', text).strip()
    # カンマ・セミコロンの連続も整理
    text = re.sub(r'[,;]\s*[,;]', ',', text)
    text = re.sub(r'[,;\s]+$', '', text)
    text = re.sub(r'\s*,\s*,', ',', text)
    return text.strip()


def is_metadata_block(text):
    """メアド・URL主体のメタデータブロックを検出"""
    email_count = len(re.findall(r'@[\w.-]+', text))
    url_count = len(re.findall(r'https?://', text))
    if email_count + url_count >= 3:
        return True
    return False


def is_reference_entry(text):
    """参考文献エントリを検出"""
    return bool(re.match(r'^\[\d+\]', text.strip()))

def is_figure_caption(text):
    """図・表のキャプションを検出"""
    return bool(re.match(r'^(Figure|Fig\.|Table|TABLE)\s*\d+', text.strip()))

def split_into_sentence_chunks(text, max_chars=600):
    """長いテキストを文単位で max_chars 以下のチャンクに分割する"""
    sentences = re.split(r'(?<=[a-z0-9][.!?])\s+(?=[A-Z"])', text)
    chunks = []
    current = ""
    for sent in sentences:
        if not sent.strip():
            continue
        if len(current) + len(sent) + 1 <= max_chars:
            current = (current + " " + sent).strip()
        else:
            if current:
                chunks.append(current)
            current = sent
    if current:
        chunks.append(current)
    return chunks if chunks else [text]


def estimate_avg_line_height(lines):
    """平均行高を推定"""
    heights = []
    for line in lines:
        tops = [w['top'] for w in line]
        bottoms = [w['bottom'] for w in line]
        h = max(bottoms) - min(tops)
        if h > 0:
            heights.append(h)
    return sum(heights) / len(heights) if heights else 10.0


def words_to_blocks(words, gap_ratio=1.2):
    """単語リストを行間隔基準で段落ブロックに変換"""
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: (round(w['top'], 1), w['x0']))

    # Y座標で行にまとめる
    lines = []
    current_line = [words_sorted[0]]
    for word in words_sorted[1:]:
        if abs(word['top'] - current_line[-1]['top']) < 4:
            current_line.append(word)
        else:
            lines.append(current_line)
            current_line = [word]
    if current_line:
        lines.append(current_line)

    avg_h = estimate_avg_line_height(lines)

    blocks = []
    current_block = []
    for i, line in enumerate(lines):
        line_text = ' '.join(w['text'] for w in sorted(line, key=lambda w: w['x0']))
        if i == 0:
            current_block.append(line_text)
            continue
        prev_bottom = max(w['bottom'] for w in lines[i - 1])
        curr_top = min(w['top'] for w in line)
        gap = curr_top - prev_bottom
        if gap > avg_h * gap_ratio:
            if current_block:
                blocks.append(' '.join(current_block))
            current_block = [line_text]
        else:
            current_block.append(line_text)
    if current_block:
        blocks.append(' '.join(current_block))
    return [b for b in blocks if len(b) > 15]


def detect_column_split(words, page_width):
    """ページが2段組か判定し、境界のx座標を返す。単段の場合はNone。"""
    BUCKETS = 24
    bw = page_width / BUCKETS
    counts = [0] * BUCKETS
    for w in words:
        mid = (w['x0'] + w['x1']) / 2
        b = min(int(mid / bw), BUCKETS - 1)
        counts[b] += 1
    # 中央30〜70%の範囲で最小のバケットを探す
    lo, hi = int(BUCKETS * 0.30), int(BUCKETS * 0.70)
    center = counts[lo:hi]
    if not center:
        return None
    avg = sum(counts) / BUCKETS
    min_val = min(center)
    # 中央の最小値が全体平均の30%未満ならカラムギャップあり
    if avg > 0 and min_val < avg * 0.30:
        idx = lo + center.index(min_val)
        return (idx + 0.5) * bw
    return None


def extract_page_blocks(page):
    """1ページからカラム構造を自動検出してブロック抽出"""
    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    if not words:
        return []
    col_split = detect_column_split(words, page.width)
    if col_split:
        # 2段組: 左右別々に処理
        left = [w for w in words if w['x1'] <= col_split + 5]
        right = [w for w in words if w['x0'] >= col_split - 5]
        result = []
        for col in (left, right):
            result.extend(words_to_blocks(col))
        return result
    else:
        # 単段: 全体をまとめて処理
        return words_to_blocks(words)


def split_header_from_body(blocks):
    """ブロック内にセクション見出しがあれば分割する（先頭・途中どちらも対応）"""
    # Pass 1: ブロック途中のヘッダーで分割
    MID_HEADER_RE = re.compile(
        r'([.!?])\s+'
        r'(ABSTRACT|INTRODUCTION|CONCLUSION|REFERENCES|'
        r'ACKNOWLEDGMENTS?|RELATED WORK|DISCUSSION|RESULTS|'
        r'[A-Z][A-Z]{2,}(?:[\s:0-9]+[A-Z]{2,})+)'
        r'\s+(?=[A-Za-z])'
    )
    pass1 = []
    for block in blocks:
        t = block.strip()
        parts = MID_HEADER_RE.split(t)
        if len(parts) > 1:
            i = 0
            while i < len(parts):
                if i == 0:
                    if parts[0].strip():
                        pass1.append(parts[0].strip())
                    i += 1
                else:
                    header = parts[i + 1].strip() if i + 1 < len(parts) else ''
                    if header:
                        pass1.append(header)
                    i += 2
        else:
            pass1.append(t)

    # Pass 2: ブロック先頭のヘッダーを分離
    START_HEADER_RE = re.compile(
        r'^(ABSTRACT|INTRODUCTION|CONCLUSION|REFERENCES|'
        r'ACKNOWLEDGMENTS?|RELATED WORK|DISCUSSION|RESULTS|'
        r'[A-Z][A-Z]{2,}(?:[\s:0-9]+[A-Z]{2,})+)'
        r'\s+([A-Za-z].+)', re.DOTALL
    )
    result = []
    for block in pass1:
        t = block.strip()
        m = START_HEADER_RE.match(t)
        if m and len(m.group(2)) > 30:
            result.append(m.group(1).strip())
            result.append(m.group(2).strip())
        else:
            result.append(t)
    return result


def extract_blocks(pdf_path, max_chars=600):
    """PDFからテキストブロック（段落単位）を抽出する"""
    all_blocks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            all_blocks.extend(extract_page_blocks(page))

    # ページヘッダー/フッターを除外
    all_blocks = [b for b in all_blocks if not is_page_header(b)]

    # セクション見出しを本文から分離
    all_blocks = split_header_from_body(all_blocks)

    # URL・メアド・DOI を全ブロックから除去（見出しはそのまま保持）
    all_blocks = [clean_block(b) for b in all_blocks]
    all_blocks = [b for b in all_blocks if len(b) >= 20 or is_section_header(b)]

    final = []
    for block in all_blocks:
        if len(block) <= max_chars:
            final.append(block)
        else:
            final.extend(split_into_sentence_chunks(block, max_chars))
    return final


_LATEX_ESCAPE = [
    ('&', r'\&'),
    ('%', r'\%'),
    ('$', r'\$'),
    ('#', r'\#'),
    ('_', r'\_'),
    ('{', r'\{'),
    ('}', r'\}'),
    ('~', r'\textasciitilde{}'),
    ('^', r'\^{}'),
]

def escape_latex(text):
    """LaTeX 特殊文字をエスケープ（$は数式として残す）"""
    for char, escaped in _LATEX_ESCAPE:
        text = text.replace(char, escaped)
    return text

def format_block_latex(en_text, ja_text):
    """英日ペアを paracol ブロックとして整形"""
    en_clean = escape_latex(en_text)
    ja_clean = escape_latex(ja_text)

    if is_section_header(en_text):
        return (
            f"\\switchcolumn*\n\\vspace{{0.8em}}{{\\large\\textbf{{{en_clean}}}}}\\vspace{{0.3em}}\n\n"
            f"\\switchcolumn\n\\vspace{{0.8em}}{{\\large\\textbf{{{ja_clean}}}}}\\vspace{{0.3em}}\n\n"
        )
    else:
        # {} で switchcolumn の直後に [number] がオプション引数と解釈されるのを防止
        en_line = f"{{}}\n{en_clean}" if en_clean.startswith('[') else en_clean
        ja_line = f"{{}}\n{ja_clean}" if ja_clean.startswith('[') else ja_clean
        return (
            f"\\switchcolumn*\n{en_line}\n\n"
            f"\\switchcolumn\n{ja_line}\n\n"
        )



def translate_one(text, engine, translator):
    """1ブロックを翻訳する（エンジン種別に応じて呼び分け）"""
    if engine == "deepl":
        result = translator.translate_text(text, target_lang="JA", source_lang="EN")
        return result.text
    else:  # google
        from deep_translator import GoogleTranslator
        # GoogleTranslatorは5000文字制限があるため長い場合は分割
        if len(text) <= 4500:
            return translator.translate(text)
        # 分割翻訳
        chunks = [text[i:i+4500] for i in range(0, len(text), 4500)]
        return ''.join(GoogleTranslator(source='en', target='ja').translate(c) for c in chunks)


def translate_blocks(blocks, engine, translator):
    """ブロック一覧を日本語に翻訳（数式・図キャプションはスキップ）"""
    results = []
    skip_count = 0

    for i, block in enumerate(blocks):
        print(f"  翻訳中... {i+1}/{len(blocks)}", end='\r', flush=True)

        if is_math_heavy(block) or is_figure_caption(block):
            results.append(block)
            skip_count += 1
            continue

        try:
            result = translate_one(block, engine, translator)
            results.append(result)
            time.sleep(0.3 if engine == "google" else 0.05)  # Googleは少し長めに待つ
        except Exception as e:
            print(f"\n  Warning: ブロック {i+1} の翻訳失敗: {e}")
            results.append(block)

    print(f"\n  翻訳完了（スキップ: {skip_count} ブロック）")
    return results



def generate_latex(blocks_en, blocks_ja, title="", title_ja=""):
    """英日対訳 paracol LaTeX ドキュメントを生成"""
    content = LATEX_PREAMBLE

    if title:
        title_en_escaped = escape_latex(title)
        title_ja_str = escape_latex(title_ja) if title_ja else title_en_escaped
        content += f"\\switchcolumn*\n{{\\Large\\textbf{{{title_en_escaped}}}}}\n\n"
        content += f"\\switchcolumn\n{{\\Large\\textbf{{{title_ja_str}}}}}\n\n"

    for en, ja in zip(blocks_en, blocks_ja):
        content += format_block_latex(en, ja)

    content += LATEX_FOOTER
    return content



def main():
    parser = argparse.ArgumentParser(
        description="PDF → 英日二段組 LaTeX/PDF 自動生成"
    )
    parser.add_argument("pdf", help="入力PDFファイルのパス")
    parser.add_argument("--no-compile", action="store_true",
                        help=".tex ファイルのみ生成（コンパイルしない）")
    parser.add_argument("--check-quota", action="store_true",
                        help="DeepL の残り利用量を確認して終了")
    args = parser.parse_args()

    engine, translator = get_translator()

    # クォータ確認モード
    if args.check_quota:
        if engine != "deepl":
            print("⚠️  --check-quota は DeepL 使用時のみ有効です。")
            return
        usage = translator.get_usage()
        used = usage.character.count
        limit = usage.character.limit
        remaining = limit - used
        print(f"DeepL 利用状況: {used:,} / {limit:,} 文字")
        print(f"残り: {remaining:,} 文字（論文約 {remaining // 50000} 本分）")
        return

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ファイルが見つかりません: {pdf_path}")
        sys.exit(1)

    basename = pdf_path.stem
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    output_tex = output_dir / f"{basename}_bilingual.tex"
    output_pdf = output_dir / f"{basename}_bilingual.pdf"

    print(f"処理開始: {pdf_path.name}")

    # Step 1: テキスト抽出
    print("\nStep 1: PDF からテキストを抽出中...")
    blocks = extract_blocks(pdf_path)
    char_count = sum(len(b) for b in blocks)
    print(f"  {len(blocks)} ブロック / 約 {char_count:,} 文字")

    # DeepL の場合のみクォータチェック
    if engine == "deepl":
        usage = translator.get_usage()
        remaining = usage.character.limit - usage.character.count
        print(f"  DeepL 残り: {remaining:,} 文字")
        if char_count > remaining:
            print("月次クォータを超える可能性があります。")

    # Step 2: 翻訳
    print(f"\nStep 2: 日本語に翻訳中...")
    title_en = basename.replace('_', ' ').replace('-', ' ')
    # タイトルも翻訳対象に含める
    all_blocks = [title_en] + blocks
    all_blocks_ja = translate_blocks(all_blocks, engine, translator)
    title_ja = all_blocks_ja[0]
    blocks_ja = all_blocks_ja[1:]

    # Step 3: LaTeX 生成
    print("\nStep 3: LaTeX ファイルを生成中...")
    latex = generate_latex(blocks, blocks_ja, title_en, title_ja)

    with open(output_tex, 'w', encoding='utf-8') as f:
        f.write(latex)
    print(f"  保存: {output_tex}")

    # Step 4: コンパイル
    if args.no_compile:
        print(f" .tex ファイルを生成しました: {output_tex}")
        print(f"   コンパイル: lualatex {output_tex}")
        return

    print("\nStep 4: lualatex でコンパイル中...")
    result = subprocess.run(
        ['lualatex', '-interaction=nonstopmode',
         '-output-directory', str(output_dir),
         str(output_tex)],
        capture_output=True, text=True, cwd=Path.cwd()
    )

    if output_pdf.exists():
        print(f" 完了！  {output_pdf}")
    else:
        print(" コンパイルに問題が発生しました。.tex ファイルを確認してください。")
        print(result.stdout[-3000:])


if __name__ == "__main__":
    main()
