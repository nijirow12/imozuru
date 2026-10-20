#!/usr/bin/env python3
"""抜き書き（extracts.json）を、原文どおりか確かめてから読むためのページ（report.md）にする。

原文を40本落としても、英語の PDF や1時間の字幕のままでは読まれずに終わる。
かといってエージェントが要約すると「早すぎる閉じ」に戻る。間を取るのが Ellis の
extracting＝抜き書き。主張ごとに原文をそのまま切り出して並べ、エージェントが書くのは
見出し・「見つからなかった」・英語の訳だけにする。

言い換えが紛れ込まないよう、抜き書きの文が原文の指定の行にそのまま在るかを機械で確かめる。
1つでも見つからなければ report.md は作らず、どれが合わないかを出して止まる。

使い方:
    report.py <topic_dir>         … <topic_dir>/extracts.json → <topic_dir>/report.md
    report.py <topic_dir> --check … 照合だけして report.md は作らない

extracts.json の形（例はリポジトリの examples/demo/extracts.json）:
    {"title", "question", "stopped",
     "claims": [{"claim", "status": "付いた|一部だけ|付かなかった", "note"?,
                 "quotes": [{"file", "line", "end"?, "text", "ja"?, "mark"?, "context_note"?}]}],
     "disagreements"?: [{"topic", "quotes": [{..., "who"}]}],
     "unreached"?: ["..."]}
"""

import json
import re
import sys
from pathlib import Path, PureWindowsPath

# 前後に見せる行数。抜き書きがどんな流れの中にあったかを、ページの中で開いて確かめられるように。
CONTEXT_LINES = 6


def norm(s: str) -> str:
    """照合用に空白・改行・箇条書きの印をそろえる。文字そのものは変えない。"""
    s = re.sub(r"(?m)^\s*[-*]\s+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def verify(src_dir: Path, q: dict) -> tuple[bool, list[str], int, int]:
    """抜き書きが原文の指定の行（前後2行の余裕つき）にそのまま在るか。前後の文脈も返す。"""
    relative = Path(q["file"])
    if relative.is_absolute() or PureWindowsPath(q["file"]).is_absolute() or ".." in relative.parts:
        return False, [], 0, 0
    try:
        root = src_dir.resolve()
        topic = src_dir.parent.resolve()
        path = (src_dir / relative).resolve()
    except (OSError, RuntimeError, ValueError):
        return False, [], 0, 0
    if root == topic or not root.is_relative_to(topic):
        return False, [], 0, 0
    if not path.is_relative_to(root) or not path.is_file():
        return False, [], 0, 0
    # splitlines() はページ区切り（\f）も改行に数え、grep・sed と行番号がずれる。改行だけで数える
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    start = max(1, q["line"] - 2)
    end = min(len(lines), q.get("end", q["line"]) + 2)
    region = norm(" ".join(lines[start - 1:end]))
    # 2段組の PDF は左右の段が1行に混ざり、何行かにまたがる文はそのままでは照合できない
    # （Yoshida 2008 で起きた）。抜き書きを「…」で区切れば、区切りごとに順に照らす。
    # 区切りはページにも「…」として残るので、省いたことが読む人に見える。
    pieces = [norm(p) for p in q["text"].split("…") if norm(p)]
    ok, pos = bool(pieces), 0
    for piece in pieces:
        at = region.find(piece, pos)
        if at < 0:
            ok = False
            break
        pos = at + len(piece)
    c0 = max(1, q["line"] - CONTEXT_LINES)
    c1 = min(len(lines), q.get("end", q["line"]) + CONTEXT_LINES)
    return ok, lines[c0 - 1:c1], c0, c1


def callout(lines: list[str], depth: int = 1) -> list[str]:
    """Obsidian の callout の中身にする。空行にも「>」を付けて、callout が途中で切れないようにする。"""
    return [("> " * depth + line).rstrip() for line in lines]


def fence_for(lines: list[str]) -> str:
    """原文にバッククォートの並びがあってもコードブロックが閉じないよう、それより長い囲みを使う。"""
    longest = max((len(m) for line in lines for m in re.findall(r"`+", line)), default=0)
    return "`" * max(3, longest + 1)


def quote_md(q: dict, ctx: list[str], c0: int) -> list[str]:
    loc = f"{q['file']}:{q['line']}" + (f"-{q['end']}" if q.get("end") else "")
    hit = range(q["line"], q.get("end", q["line"]) + 1)
    out = [f"> [!quote] {q['who']}" if q.get("who") else "> [!quote]"]
    # 原文が md の見出し（# …）だと callout の中で見出しに化けるので、行頭の # だけ打ち消す（表示は原文のまま）
    out += callout([re.sub(r"^(\s*)#", r"\1\\#", line) for line in q["text"].split("\n")])
    if q.get("ja"):
        out += callout([""] + f"〔訳〕{q['ja']}".split("\n"))
    meta = [f"[{loc}](<sources/{q['file']}>)"]
    if q.get("mark"):
        meta.append(q["mark"])
    if q.get("context_note"):
        meta.append(q["context_note"])
    out += callout(["", " ・ ".join(meta), ""])
    # 空の行は飛ばす（行番号は残るので位置は追える）。抜き書きの行には ▶ を付ける
    numbered = [f"{'▶' if c0 + i in hit else ' '}{c0 + i:>5}  {t}" for i, t in enumerate(ctx) if t.strip()]
    fence = fence_for(numbered)
    out += callout([f"[!note]- 前後を読む（{c0}〜{c0 + len(ctx) - 1}行）", fence, *numbered, fence], depth=2)
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    topic = Path(sys.argv[1]).expanduser()
    check_only = "--check" in sys.argv
    data = json.loads((topic / "extracts.json").read_text(encoding="utf-8"))
    src = topic / "sources"

    bad, rendered = [], {}
    groups = [("claims", c) for c in data["claims"]] + [("dis", d) for d in data.get("disagreements", [])]
    for _, g in groups:
        for q in g.get("quotes", []):
            ok, ctx, c0, _ = verify(src, q)
            if not ok:
                bad.append(f"{q['file']}:{q['line']}  「{q['text'][:40]}…」")
            rendered[id(q)] = (ctx, c0)
    if bad:
        print("原文に見つからない抜き書きがある（言い換えか、行番号のずれ）:")
        print("\n".join("  " + b for b in bad))
        return 1
    total = sum(len(g.get("quotes", [])) for _, g in groups)
    print(f"照合 OK: 抜き書き {total} 件がすべて原文のとおり")
    if check_only:
        return 0

    body = [f"# {data['title']}", "",
            f"- 問い: {data['question']}",
            f"- 止めた理由: {data['stopped']}", "",
            "> ここに並ぶ引用は、すべて原文をそのまま切り出したもの（機械で照合済み）。"
            "エージェントが書いたのは見出し・状態・注記・「訳」だけ。要約はしていない。", ""]
    for c in data["claims"]:
        body += [f"## {c['claim']}（{c['status']}）", ""]
        if c.get("note"):
            body += [c["note"], ""]
        for q in c.get("quotes", []):
            ctx, c0 = rendered[id(q)]
            body += quote_md(q, ctx, c0) + [""]
    for d in data.get("disagreements", []):
        body += [f"## 食い違い: {d['topic']}", "", "どれが正しいかは書かない。並べるだけ。", ""]
        for q in d.get("quotes", []):
            ctx, c0 = rendered[id(q)]
            body += quote_md(q, ctx, c0) + [""]
    if data.get("unreached"):
        body += ["## 届かなかったもの", ""] + [f"- {u}" for u in data["unreached"]] + [""]
    # 絶対パスはページを人に渡すと手元のユーザー名を漏らすので、ページからの相対で書く
    body += ["---", "", f"imozuru の抜き書き・原文はこのページと同じ場所の {src.name}/"]

    out = topic / "report.md"
    out.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    # 抜き書きの頭を出すので、端末の文字コードに無い字（cp932 の à など）で落ちないようにする
    sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
