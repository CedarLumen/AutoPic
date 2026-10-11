# 几何单测：单应变换必须把源图的四个角精确送到目标四边形的四个角
#
# 验证强度说明：不是「跑通了没报错」，而是
#   ① 在源图四角画标记点 -> 变换后标记点的重心必须落在四边形角上（定义性验证）
#   ② 平移/恒等这样有已知答案的用例必须逐像素一致
#   ③ 退化输入必须抛出 Degenerate（负例，防止「全通过」的假绿）
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image                                           # noqa: E402

from pic import geom                                            # noqa: E402

fails = []
total = [0]


def check(name, ok, extra=""):
    total[0] += 1
    print(("  PASS " if ok else "  FAIL ") + name + ("  <- " + str(extra) if extra else ""))
    if not ok:
        fails.append(name)


def marked(w=120, h=90, r=4):
    """白底 + 四角各一个黑方块的源图（角点就压在四个角上）"""
    im = Image.new("L", (w, h), 255)
    p = np.asarray(im).copy()
    for cx, cy in ((0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)):
        x0, x1 = max(0, cx - r), min(w, cx + r + 1)
        y0, y1 = max(0, cy - r), min(h, cy + r + 1)
        p[y0:y1, x0:x1] = 0
    return Image.fromarray(p), (w, h)


def blobs(arr, thresh=128):
    """连通域求重心，返回按面积从大到小排序的 [(cx, cy, 面积)]。

    不能按象限分组：标记全落在同一个象限时会被并成一个重心（本测试踩过），
    那样统计出来的位置毫无意义，还会让断言看起来「只是差一点」。
    """
    mask = arr < thresh
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    idx = np.argwhere(mask)
    for sy, sx in idx:
        if seen[sy, sx]:
            continue
        stack = [(int(sy), int(sx))]
        seen[sy, sx] = True
        pts = []
        while stack:
            y, x = stack.pop()
            pts.append((x, y))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        a = np.asarray(pts, dtype=np.float64)
        out.append((float(a[:, 0].mean()), float(a[:, 1].mean()), len(pts)))
    out.sort(key=lambda t: -t[2])
    return out


def warp(src, quad, size, resample=Image.BICUBIC):
    code = geom.perspective_coeffs(quad, src.width, src.height)
    # fillcolor 必须给：L 图默认把四边形之外填成 0（黑），
    # 不填的话重心统计会连整片黑底一起算，结论全是错的
    return src.transform(size, Image.PERSPECTIVE, code, resample, fillcolor=255)


# ---------- ① 定义性验证：四角标记必须落在四边形角上 ----------
W, H = 600, 420

def corner_error(src, quad, size, tol=3.0):
    """变换后每个标记块的重心，对「源标记重心经前向映射得到的期望位置」的误差。

    期望值用 map_points 现算（它由另一条独立断言验证过），所以这里不需要
    拍一个容差数字 —— 标记块的重心本就在角点内侧几像素，放大后会等比放大，
    用固定容差会误判成「偏了 10 像素」。
    """
    sw, sh = src.size
    out = warp(src, quad, size)
    sb, ob = blobs(np.asarray(src)), blobs(np.asarray(out))
    if len(sb) != 4 or len(ob) != 4:
        return None, "标记数不对：源 %d 个、输出 %d 个" % (len(sb), len(ob))
    expect = geom.map_points(quad, sw, sh, [(b[0], b[1]) for b in sb])
    errs = [min(np.hypot(o[0] - e[0], o[1] - e[1]) for o in ob) for e in expect]
    return (max(errs) <= tol), "各角误差 %s" % [round(e, 2) for e in errs]


src, (sw, sh) = marked()

# (a) 平移：四边形就是源矩形平移过去，答案肉眼可验
dx, dy = 137, 96
quad = [(dx, dy), (dx + sw, dy), (dx + sw, dy + sh), (dx, dy + sh)]
ok, detail = corner_error(src, quad, (W, H))
check("平移：四个标记落位正确（误差 <= 3px）", ok, detail)

# (b) 真透视：任意梯形，四个角仍须落准
quad = [(160, 70), (520, 130), (470, 380), (110, 330)]
ok, detail = corner_error(src, quad, (W, H))
check("透视梯形：四个标记落位正确（误差 <= 3px）", ok, detail)

# (b2) 旋转 30 度：另一个方向的非平凡变换
import math as _m                                              # noqa: E402

cx, cy, R = 300.0, 210.0, 150.0
rot = [(cx + R * _m.cos(_m.radians(a)), cy + R * _m.sin(_m.radians(a)))
       for a in (-120, -30, 60, 150)]
ok, detail = corner_error(src, rot, (W, H))
check("旋转四边形：四个标记落位正确（误差 <= 3px）", ok, detail)

# (c) 恒等：四边形 = 源矩形本身 -> 应与原图基本一致
# 约定：源矩形是 (0,0)-(w,h)，像素 i 覆盖 [i, i+1)，所以这里的角点是 w/h 而不是 w-1。
src2, (sw2, sh2) = marked(200, 150)
ident = [(0, 0), (sw2, 0), (sw2, sh2), (0, sh2)]
ok, detail = corner_error(src2, ident, (sw2, sh2))
check("恒等变换：四个标记落回原处（误差 <= 3px）", ok, detail)

# (d) 四边形之外必须保持白底，之内必须真有墨 —— 一负一正，否则断言是空泛的
#     （恒等用例里四边形铺满整图，「之外」是空集，那种写法等于没测）
quad_t = [(dx, dy), (dx + sw, dy), (dx + sw, dy + sh), (dx, dy + sh)]
arr_t = np.asarray(warp(src, quad_t, (W, H)))
mask_out = np.ones_like(arr_t, dtype=bool)
mask_out[dy:dy + sh, dx:dx + sw] = False
check("四边形之外保持白底（fillcolor 生效）",
      int(arr_t[mask_out].min()) == 255, "最暗像素 %d" % int(arr_t[mask_out].min()))
# 正对照要检「墨块」本身：源图只有四角有墨，去采样正中间只会得到白（本测试踩过）
ob = blobs(arr_t)
check("四边形之内检测到 4 个墨块（正对照）",
      len(ob) == 4 and sum(b[2] for b in ob) > 50,
      "检出 %d 个，总面积 %d px" % (len(ob), sum(b[2] for b in ob)))
q = [(30, 20), (300, 60), (280, 260), (10, 240)]
mapped = geom.map_points(q, 100, 80, [(0, 0), (100, 0), (100, 80), (0, 80)])
mx = max(np.hypot(m[0] - t[0], m[1] - t[1]) for m, t in zip(mapped, q))
check("map_points：矩形四角映射到四边形四角（误差 < 1e-6）", mx < 1e-6, "最大误差 %.2e" % mx)

# ---------- ③ 负例：退化输入必须抛 Degenerate ----------
def expect_degenerate(name, fn):
    try:
        fn()
    except geom.Degenerate:
        check(name, True)
        return
    except Exception as e:                                       # noqa: BLE001
        check(name, False, "抛了别的异常 %r" % e)
        return
    check(name, False, "竟然没抛异常")


expect_degenerate("三点共线 -> 抛 Degenerate",
                  lambda: geom.perspective_coeffs([(0, 0), (10, 10), (20, 20), (30, 30)], 10, 10))
expect_degenerate("只有 3 个点 -> 抛 Degenerate",
                  lambda: geom.perspective_coeffs([(0, 0), (10, 0), (10, 10)], 10, 10))
expect_degenerate("四点重合 -> 抛 Degenerate",
                  lambda: geom.perspective_coeffs([(5, 5)] * 4, 10, 10))
expect_degenerate("源图尺寸为 0 -> 抛 Degenerate",
                  lambda: geom.perspective_coeffs([(0, 0), (10, 0), (10, 10), (0, 10)], 0, 10))
expect_degenerate("角点含 NaN -> 抛 Degenerate",
                  lambda: geom.perspective_coeffs([(0, 0), (float("nan"), 0), (10, 10), (0, 10)], 10, 10))

# ---------- ④ 包围盒与预缩放尺寸 ----------
bb = geom.quad_bbox([(50, 60), (200, 40), (210, 190), (40, 200)], 300, 250, pad=4)
check("quad_bbox 取到四点的包围盒（含 padding）", bb == (36, 36, 214, 204), str(bb))
bb2 = geom.quad_bbox([(-50, -50), (400, -20), (380, 300), (0, 280)], 300, 250)
check("quad_bbox 夹到图内不越界", bb2[0] >= 0 and bb2[1] >= 0 and bb2[2] <= 300 and bb2[3] <= 250,
      str(bb2))
rw, rh = geom.fit_rect_size([(0, 0), (300, 0), (300, 200), (0, 200)], 1000, 800)
check("fit_rect_size 缩小到目标尺度（不超过原图）", rw < 1000 and rh < 800, "%dx%d" % (rw, rh))

print("\n通过 %d 项，失败 %d 项" % (total[0] - len(fails), len(fails)))
sys.exit(1 if fails else 0)
