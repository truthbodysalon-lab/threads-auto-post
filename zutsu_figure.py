#!/usr/bin/env python3
"""頭痛Threads(@truth_nagaoka_zutsuu)の「画像の解説」投稿用の図解カードを描く（2026-11-11 masa指示
「Thredsの投稿を1日10投稿を目標に その際に画像の解説も取り入れて投稿するようにして」）。

AIで絵を生成しない（画像生成はCodex経由のみのルール）。文字と図形だけを Pillow で並べた図解カード。
figure = {"title": "16字まで", "points": ["18字まで" x 2〜4], "foot": "20字まで(任意)"}
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
BG = (250, 246, 238)        # 生成り
NAVY = (31, 52, 92)
ACCENT = (232, 170, 66)     # 黄
CARD = (255, 255, 255)
TEXT = (40, 40, 48)
SUB = (110, 110, 120)
FOOTER = "頭痛改善専門｜整体院トゥルース"

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",          # GitHub Actions(ubuntu, fonts-noto-cjk)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",                  # Mac
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    raise FileNotFoundError("日本語フォントが見つからない（fonts-noto-cjk を入れる）")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    """幅に収まるように1文字単位で折る。行頭に句読点を置かない"""
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= width:
            cur += ch
            continue
        if ch in "、。」）！？" and cur:
            cur += ch
            lines.append(cur)
            cur = ""
            continue
        lines.append(cur)
        cur = ch
    if cur:
        lines.append(cur)
    return lines


def validate_figure(fig) -> list[str]:
    errs = []
    if not isinstance(fig, dict):
        return ["figure が無い"]
    title = str(fig.get("title") or "").strip()
    points = [str(p).strip() for p in (fig.get("points") or []) if str(p).strip()]
    if not title:
        errs.append("figure.title が空")
    elif len(title) > 18:
        errs.append(f"figure.title が長い({len(title)}字・16字まで)")
    if not 2 <= len(points) <= 4:
        errs.append(f"figure.points は2〜4個({len(points)}個)")
    for p in points:
        if len(p) > 22:
            errs.append(f"figure.points「{p[:10]}…」が長い({len(p)}字・18字まで)")
    if len(str(fig.get("foot") or "")) > 24:
        errs.append("figure.foot が長い(20字まで)")
    return errs


def figure_text(fig) -> str:
    """検品用に図の文字を1本の文字列にする"""
    if not isinstance(fig, dict):
        return ""
    return "\n".join([str(fig.get("title") or "")] + [str(p) for p in (fig.get("points") or [])]
                     + [str(fig.get("foot") or "")])


def render(fig: dict, out: Path) -> Path:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # 見出し帯
    d.rectangle([0, 0, W, 330], fill=NAVY)
    d.rectangle([80, 300, 200, 312], fill=ACCENT)
    tf = _font(68)
    title = str(fig["title"]).strip()
    tl = _wrap(d, title, tf, W - 160)
    if len(tl) >= 2:   # 2行になるなら真ん中付近の助詞の後ろで割り、1文字だけの行を作らない
        mid = len(title) // 2
        cut = next((i for i in sorted(range(max(1, mid - 5), min(len(title), mid + 5)), key=lambda i: abs(i - mid))
                    if title[i - 1] in "のとをにがはでもへ、・"), mid)
        tl = [title[:cut], title[cut:]]
    tl = tl[:2]
    y = 165 - (len(tl) * 88) // 2
    for line in tl:
        d.text((80, y), line, font=tf, fill=(255, 255, 255))
        y += 88
    # ポイント
    points = [str(p).strip() for p in fig.get("points") or [] if str(p).strip()][:4]
    pf, nf = _font(46), _font(44)
    top, bottom = 390, H - 210
    gap = 28
    ch = (bottom - top - gap * (len(points) - 1)) // max(len(points), 1)
    for i, p in enumerate(points):
        y0 = top + i * (ch + gap)
        d.rounded_rectangle([60, y0, W - 60, y0 + ch], radius=28, fill=CARD, outline=(226, 220, 208), width=3)
        cx, cy = 140, y0 + ch // 2
        d.ellipse([cx - 40, cy - 40, cx + 40, cy + 40], fill=ACCENT)
        n = str(i + 1)
        d.text((cx - d.textlength(n, font=nf) / 2, cy - 30), n, font=nf, fill=NAVY)
        lines = _wrap(d, p, pf, W - 60 - 220)[:2]
        ty = cy - len(lines) * 31
        for line in lines:
            d.text((210, ty), line, font=pf, fill=TEXT)
            ty += 62
    # 補足と店名
    foot = str(fig.get("foot") or "").strip()
    if foot:
        ff = _font(38)
        d.text(((W - d.textlength(foot, font=ff)) / 2, H - 175), foot, font=ff, fill=SUB)
    d.rectangle([0, H - 100, W, H], fill=NAVY)
    sf = _font(36)
    d.text(((W - d.textlength(FOOTER, font=sf)) / 2, H - 74), FOOTER, font=sf, fill=(255, 255, 255))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


if __name__ == "__main__":
    import sys
    p = render({"title": "締めつけ型の頭痛と画面の位置",
                "points": ["画面は目線のやや下に置く", "20分ごとに20秒、遠くを見る", "首の後ろをホットタオルで温める"],
                "foot": "デスクワークの日の目安"}, Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/zutsu_fig.png"))
    print(p)
