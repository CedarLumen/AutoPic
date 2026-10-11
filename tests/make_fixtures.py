# 生成界面测试要用的两张图（纸照片 + 手写稿），路径由命令行给出
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def make_paper(path, w=820, h=580):
    rng = np.random.default_rng(21)
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
    base = np.stack([0.70 + 0.24 * x, 0.68 + 0.24 * x, 0.63 + 0.24 * x], axis=2)
    arr = np.clip(base + rng.normal(0, 0.012, (h, w, 1)).astype(np.float32), 0, 1)
    im = Image.fromarray((arr * 255).astype("uint8"))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w - 1, h - 1], outline=(70, 60, 50), width=34)   # 桌面
    d.polygon([(120, 96), (706, 70), (748, 496), (92, 470)],            # 纸
              fill=(238, 233, 222))
    d.line((150, 200, 700, 190), fill=(206, 202, 194), width=2)         # 纸上的横线
    d.line((150, 300, 700, 290), fill=(206, 202, 194), width=2)
    im.save(path)


def make_ink(path, w=460, h=130):
    im = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("consola.ttf", 46)
    except Exception:                                                  # noqa: BLE001
        f = ImageFont.load_default()
    d.text((14, 12), "AutoPic sample", fill=12, font=f)
    d.line((16, 92, 420, 98), fill=45, width=4)
    d.line((16, 112, 260, 108), fill=70, width=3)
    im.convert("RGB").save(path)


if __name__ == "__main__":
    make_paper(sys.argv[1])
    make_ink(sys.argv[2])
    print("已生成测试图：%s / %s" % (sys.argv[1], sys.argv[2]))
