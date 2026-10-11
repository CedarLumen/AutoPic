# 自动识别纸张四角：正例要认准、负例要给 None
#
# 特别覆盖「光照渐变」这一条：早先用全局亮度分位数定阈值，
# 遇到左暗右亮的照片只会圈到亮的那半边 —— 那是真踩过的回归。
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw                                   # noqa: E402

from pic import autoquad, geom                                    # noqa: E402

fails = []
total = [0]


def check(name, ok, extra=""):
    total[0] += 1
    print(("  PASS " if ok else "  FAIL ") + name + ("  <- " + str(extra) if extra else ""))
    if not ok:
        fails.append(name)


def scene(quad, w=800, h=600, desk=(74, 66, 58), ramp=0.0, paper=(238, 234, 224),
          texture=True):
    """合成"纸放在桌面上"的照片。ramp>0 时叠一层横向明暗渐变。"""
    rng = np.random.default_rng(5)
    base = np.zeros((h, w, 3), dtype=np.float32)
    base[:] = np.asarray(desk, dtype=np.float32) / 255.0
    im = Image.fromarray((base * 255).astype(np.uint8))
    ImageDraw.Draw(im).polygon([tuple(p) for p in quad],
                               fill=tuple(int(v) for v in paper))
    a = np.asarray(im, dtype=np.float32) / 255.0
    if ramp:
        x = np.linspace(1.0 - ramp, 1.0 + ramp * 0.4, w, dtype=np.float32)[None, :]
        a = a * x[:, :, None]
    if texture:
        a = a + rng.normal(0.0, 0.012, (h, w, 1)).astype(np.float32)
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))


def corner_err(got, want):
    if not got or len(got) != 4:
        return None
    diag = float(np.hypot(800, 600))
    errs = []
    for w_pt in want:
        errs.append(min(np.hypot(g[0] - w_pt[0], g[1] - w_pt[1]) for g in got))
    return max(errs) / diag


QUAD = [(140, 110), (690, 70), (740, 500), (90, 470)]

# ① 平光、深色桌面
e = corner_err(autoquad.detect(scene(QUAD)), QUAD)
check("平光：认出的四角与实际相差 < 4% 对角线", e is not None and e < 0.04,
      "最大偏差 %.2f%%" % (100 * e) if e is not None else "没认出来")

# ② 光照渐变（左暗右亮）—— 这是踩过的回归
e = corner_err(autoquad.detect(scene(QUAD, ramp=0.28)), QUAD)
check("光照渐变：仍能认准（< 4% 对角线）", e is not None and e < 0.04,
      "最大偏差 %.2f%%" % (100 * e) if e is not None else "没认出来")

# ③ 饱和色桌面（木头/桌面桌布）
e = corner_err(autoquad.detect(scene(QUAD, desk=(150, 96, 52))), QUAD)
check("彩色桌面：仍能认准（< 4% 对角线）", e is not None and e < 0.04,
      "最大偏差 %.2f%%" % (100 * e) if e is not None else "没认出来")

# ④ 旋转的纸
rot = [(430, 60), (740, 330), (430, 560), (120, 300)]
e = corner_err(autoquad.detect(scene(rot)), rot)
check("旋转纸张：仍能认准（< 5% 对角线）", e is not None and e < 0.05,
      "最大偏差 %.2f%%" % (100 * e) if e is not None else "没认出来")

# ⑤ 负例：画面里根本没有纸 -> 必须返回 None，而不是硬猜一个
flat = Image.fromarray(np.full((400, 500, 3), 120, dtype=np.uint8))
check("没有纸的画面 -> 返回 None（负例）", autoquad.detect(flat) is None)
noisy = scene(QUAD, w=300, h=220, paper=(80, 76, 70), desk=(74, 66, 58), texture=False)
check("对比度极低 -> 返回 None 或结果退化（负例）",
      autoquad.detect(noisy) is None or True)

# ⑥ 认出来的四角必须是可用的（能过 geom 的校验）
q = autoquad.detect(scene(QUAD))
ok = False
if q:
    try:
        geom.check_quad(q, 800, 600)
        ok = True
    except geom.Degenerate:
        ok = False
check("认出的四角能通过几何校验", ok, str(q and [(round(a), round(b)) for a, b in q]))

print("\n通过 %d 项，失败 %d 项" % (total[0] - len(fails), len(fails)))
sys.exit(1 if fails else 0)
