"""アプリのアイコン(assets/icon.ico)を作る。Pillow が必要: python -m pip install pillow"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icon.ico"
OUT.parent.mkdir(exist_ok=True)

S = 512  # 大きく描いてから縮小する(縁がきれいになる)
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 金色の角丸の四角(画面左上のロゴと同じ配色)
top, bottom = (240, 196, 92), (176, 128, 36)
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for y in range(S):
    t = y / (S - 1)
    gd.line([(0, y), (S, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([16, 16, S - 16, S - 16], radius=110, fill=255)
img.paste(grad, (0, 0), mask)

# 文字「HS」
font = None
for name in ("arialbd.ttf", "segoeuib.ttf", "YuGothB.ttc", "meiryob.ttc"):
    try:
        font = ImageFont.truetype(name, 250)
        break
    except OSError:
        continue
font = font or ImageFont.load_default()
text = "HS"
box = d.textbbox((0, 0), text, font=font)
w, h = box[2] - box[0], box[3] - box[1]
d.text(((S - w) / 2 - box[0], (S - h) / 2 - box[1] - 6), text, font=font, fill=(27, 21, 3, 255))

img.save(OUT, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
print("wrote", OUT, OUT.stat().st_size, "bytes")
