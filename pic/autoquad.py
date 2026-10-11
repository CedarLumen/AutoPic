# 自动找纸张四角：给用户一个"起手位置"，不替代手工微调
#
# 为什么不用「除以局部均值」那套：局部均值对**局部均匀**的区域一律归一成 1，
# 桌面内部和纸面内部会同时变成 1，根本分不开（实测全场景失败）。
#
# 实际管用的做法：照片四边看到的必然是背景（桌面/床单），
# 用四角与四边的中位数拟合出一个平滑的"背景光照面"，
# 再拿「像素 - 背景面」的残差定阈值 —— 这样明暗渐变会被一起吃进去，
# 也不要求背景占多数或少数。
#
# 猜不出来就返回 None：给一个错的四角比让用户自己拖更糟。
import numpy as np
from PIL import Image, ImageDraw

from . import geom

MIN_AREA_RATIO = 0.10          # 纸面至少占全图这么多
MAX_SIDE = 640
INSET = 0.015                  # 四角往里收一点（纸边常有阴影）


def _otsu(x, lo=0.0, hi=1.0):
    """在 [lo,hi] 上做 Otsu 自动阈值（参数无关，适合双峰）"""
    hist, edges = np.histogram(np.clip(x, lo, hi), bins=64, range=(lo, hi))
    hist = hist.astype(np.float64)
    if hist.sum() <= 0:
        return (lo + hi) * 0.5
    p = hist / hist.sum()
    omega = np.cumsum(p)
    mu = np.cumsum(p * np.arange(len(hist)))
    denom = omega * (1.0 - omega)
    denom[denom <= 0] = 1e-9
    sigma_b = (mu[-1] * omega - mu) ** 2 / denom
    k = int(np.argmax(sigma_b))
    return float(0.5 * (edges[k] + edges[k + 1]))


def _bg_surface(luma, bw):
    """用四角 + 四边中点的中位数，双线性拟合出背景亮度面"""
    h, w = luma.shape
    b = max(3, min(bw, min(h, w) // 12))

    def med(sl):
        v = luma[sl]
        return float(np.median(v)) if v.size else 0.0

    four = [med(np.s_[0:b, 0:b]), med(np.s_[0:b, w - b:w]),
            med(np.s_[h - b:h, w - b:w]), med(np.s_[h - b:h, 0:b])]
    # 每边中点也看一眼，抑制某一角恰好被杂物占住
    top = med(np.s_[0:b, w // 2 - b:w // 2 + b])
    bot = med(np.s_[h - b:h, w // 2 - b:w // 2 + b])
    lef = med(np.s_[h // 2 - b:h // 2 + b, 0:b])
    rig = med(np.s_[h // 2 - b:h // 2 + b, w - b:w])
    tl = float(np.median([four[0], top, lef]))
    tr = float(np.median([four[1], top, rig]))
    br = float(np.median([four[2], bot, rig]))
    bl = float(np.median([four[3], bot, lef]))
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
    top_row = tl * (1 - xx) + tr * xx
    bot_row = bl * (1 - xx) + br * xx
    return top_row * (1 - yy) + bot_row * yy


def _central_blob(mask, seed, block=8):
    """取包含 seed 的那一片连通区域（4 连通）。

    自己实现而不用 ImageDraw.floodfill：后者在 640x480 这种尺寸上
    **会静默什么都不做**（小图上正常，大图上不报错也不填），实测踩过。

    做法：先按 block 做块级 max 下采样，在缩小图上迭代生长（迭代次数少一个数量级），
    再放大回原分辨率与掩膜求交 —— 既快又不丢边界精度。
    """
    h, w = mask.shape
    bh, bw = (h + block - 1) // block, (w + block - 1) // block
    pad = ((0, bh * block - h), (0, bw * block - w))
    m = np.pad(mask.astype(bool), pad, mode="constant")
    small = m.reshape(bh, block, bw, block).max(axis=(1, 3))
    sx, sy = int(seed[0]) // block, int(seed[1]) // block
    sx = min(max(sx, 0), bw - 1)
    sy = min(max(sy, 0), bh - 1)
    if not small[sy, sx]:
        ys, xs = np.where(small)
        if len(xs) == 0:
            return np.zeros_like(mask, dtype=bool)
        d = (xs - sx) ** 2 + (ys - sy) ** 2
        i = int(np.argmin(d))
        sx, sy = int(xs[i]), int(ys[i])
    cur = np.zeros_like(small)
    cur[sy, sx] = True
    while True:
        nxt = cur.copy()
        nxt[1:, :] |= cur[:-1, :]
        nxt[:-1, :] |= cur[1:, :]
        nxt[:, 1:] |= cur[:, :-1]
        nxt[:, :-1] |= cur[:, 1:]
        nxt &= small
        if nxt.sum() == cur.sum():
            break
        cur = nxt
    grown = np.repeat(np.repeat(cur, block, axis=0), block, axis=1)[:h, :w]
    return grown & mask.astype(bool)


def _corners(xs, ys, step=5):
    """从区域内的点里取四个角。

    只用 x±y 的极值只对「大致正放的纸」成立：把纸转成菱形时会两次取到同一个点，
    另外两个角就丢了（实测偏差 38%）。
    做法：在若干个旋转角下各取一次极值点，选四边形**面积最大**的那一组 ——
    正确的那组角面积最大，这个选择天然就做对了，不需要判断纸的朝向。
    """
    if len(xs) < 4:
        return []
    best, best_area = None, -1.0
    for deg in range(0, 90, step):
        th = np.deg2rad(deg)
        u = xs * np.cos(th) + ys * np.sin(th)
        v = -xs * np.sin(th) + ys * np.cos(th)
        idx = [(u + v).argmin(), (u - v).argmax(), (u + v).argmax(), (u - v).argmin()]
        if len(set(idx)) < 4:
            continue
        pts = [(float(xs[i]), float(ys[i])) for i in idx]
        a = geom.quad_area(pts)
        if a > best_area:
            best_area, best = a, pts
    if best is None:
        idx = [(xs + ys).argmin(), (xs - ys).argmax(),
               (xs + ys).argmax(), (xs - ys).argmin()]
        best = [(float(xs[i]), float(ys[i])) for i in idx]
    return best


def detect(paper_img):
    """返回 4 个点（原图坐标，左上/右上/右下/左下）或 None。"""
    im = paper_img.convert("RGB")
    W, H = im.size
    k = min(1.0, MAX_SIDE / float(max(W, H)))
    sw, sh = max(8, int(W * k)), max(8, int(H * k))
    a = np.asarray(im.resize((sw, sh), Image.LANCZOS), dtype=np.float32) / 255.0
    luma = a[:, :, 0] * 0.299 + a[:, :, 1] * 0.587 + a[:, :, 2] * 0.114
    sat = a.max(axis=2) - a.min(axis=2)

    bg = _bg_surface(luma, max(4, int(min(sw, sh) / 18)))
    resid = luma - bg                       # 纸面明显为正，桌面接近 0
    hi = float(np.percentile(resid, 99))
    if hi < 0.05:
        return None                         # 全图跟背景一个亮度，没纸可找
    thr = _otsu(resid, 0.0, hi) * 0.9       # 稍微放宽，别把纸面暗部切掉
    sat_lim = max(0.12, float(np.percentile(sat, 60)))
    mask = ((resid >= thr) & (sat <= sat_lim)).astype(np.uint8)
    if mask.sum() < MIN_AREA_RATIO * sw * sh:      # sat 判据太严就只按亮度
        mask = (resid >= thr).astype(np.uint8)
    if mask.sum() == 0:
        return None

    # 只保留包含画面中心的那一片（白墙、白杯子、第二张纸都不算）
    region = _central_blob(mask, (sw // 2, sh // 2))
    if region.sum() < MIN_AREA_RATIO * sw * sh:
        return None

    ys, xs = np.where(region)
    quad = [(float(px) / k, float(py) / k)
            for px, py in _corners(xs.astype(np.float64), ys.astype(np.float64))]
    quad = [(min(max(x, 0.0), float(W)), min(max(y, 0.0), float(H))) for x, y in quad]
    try:
        geom.check_quad(quad, W, H)
    except geom.Degenerate:
        return None
    cx0 = sum(p[0] for p in quad) / 4.0
    cy0 = sum(p[1] for p in quad) / 4.0
    return [(x + (cx0 - x) * INSET, y + (cy0 - y) * INSET) for x, y in quad]
