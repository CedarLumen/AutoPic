# 几何：把一个矩形映射到任意四边形的单应变换（透视变换）
#
# 全部是纯函数，不碰文件也不碰图像对象 —— 所以可以直接表驱动测试。
#
# 约定：四个角点顺序固定为 左上、右上、右下、左下（顺时针）。
#   - 源矩形用的是图像坐标：左上 (0,0)、右下 (w,h)
#   - 目标四边形是用户在草稿纸照片上拖出来的四个点（同样是图像坐标，y 向下）
import math

import numpy as np

# 四点顺序（顺时针）：左上、右上、右下、左下
ORDER = ("左上", "右上", "右下", "左下")

# 退化判定阈值：四边形面积小于外接框的这个比例，说明四点几乎共线/重合
MIN_AREA_RATIO = 0.02


class Degenerate(Exception):
    """四点退化（共线、重合、面积过小）—— 调用方应提示用户重拖"""


def _solve(src, dst):
    """解出把 src 四点映射到 dst 四点的 3x3 单应矩阵（8 个未知数，最小二乘）"""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.append(v)
    a = np.asarray(rows, dtype=np.float64)
    b = np.asarray(rhs, dtype=np.float64)
    try:
        sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    except np.linalg.LinAlgError as e:
        raise Degenerate("四点退化，无法求解：%s" % e)
    h = np.array([[sol[0], sol[1], sol[2]],
                  [sol[3], sol[4], sol[5]],
                  [sol[6], sol[7], 1.0]], dtype=np.float64)
    return h


def quad_area(pts):
    """四边形面积（不依赖顺序的绝对值，用鞋带公式）"""
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def check_quad(pts, w=None, h=None):
    """四点是否可用：数量、退化、面积占比。不可用就抛 Degenerate。"""
    if len(pts) != 4:
        raise Degenerate("需要 4 个角点，收到 %d 个" % len(pts))
    pts = [(float(x), float(y)) for x, y in pts]
    if not all(math.isfinite(v) for p in pts for v in p):
        raise Degenerate("角点里有非法数值")
    area = quad_area(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    box = max(1.0, (max(xs) - min(xs)) * (max(ys) - min(ys)))
    if area / box < MIN_AREA_RATIO:
        raise Degenerate("四点几乎在一条线上（面积占比 %.3f%%），请重新拖四角"
                         % (area / box * 100.0))
    return pts


def perspective_coeffs(quad, src_w, src_h):
    """给 PIL 的 Image.transform(..., Image.PERSPECTIVE, coeffs) 用的 8 个系数。

    注意 PIL 的语义是「反向」的：它要的是 输出坐标 -> 输入坐标 的映射，
    所以这里求的是 四边形 -> 源矩形 的单应矩阵，再展平。
    """
    quad = check_quad(quad)
    if src_w <= 0 or src_h <= 0:
        raise Degenerate("源图尺寸非法：%dx%d" % (src_w, src_h))
    rect = [(0.0, 0.0), (float(src_w), 0.0), (float(src_w), float(src_h)), (0.0, float(src_h))]
    h_inv = _solve(quad, rect)            # 四边形 -> 源矩形（PIL 要的方向）
    if abs(h_inv[2, 2]) < 1e-12:
        raise Degenerate("单应矩阵退化")
    m = h_inv / h_inv[2, 2]
    return tuple(float(v) for v in m.reshape(9)[:8])


def map_points(quad, src_w, src_h, pts):
    """把四边形上的点反查回源图坐标（前端画对齐辅助线、测试都用得上）"""
    quad = check_quad(quad)
    rect = [(0.0, 0.0), (float(src_w), 0.0), (float(src_w), float(src_h)), (0.0, float(src_h))]
    h_fwd = _solve(rect, quad)            # 源矩形 -> 四边形
    out = []
    for x, y in pts:
        v = h_fwd @ np.array([x, y, 1.0])
        if abs(v[2]) < 1e-12:
            out.append((float("nan"), float("nan")))
        else:
            out.append((float(v[0] / v[2]), float(v[1] / v[2])))
    return out


def quad_bbox(quad, w, h, pad=0):
    """四边形在目标图里的包围盒（裁剪出来只处理这一块，省时间），并夹到图内"""
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    x0 = max(0, int(math.floor(min(xs))) - pad)
    y0 = max(0, int(math.floor(min(ys))) - pad)
    x1 = min(int(w), int(math.ceil(max(xs))) + pad)
    y1 = min(int(h), int(math.ceil(max(ys))) + pad)
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def fit_rect_size(quad, src_w, src_h, max_side=4096):
    """按四边形的实际尺寸算出「源图应该先缩放成多大」再透视采样。

    直接把源图按原尺寸透视到目标区，缩小时会走样（采样点太稀）；
    放大时又浪费。按四边形外接框的尺度预缩放，两头都合适。
    """
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    span_w = max(1.0, max(xs) - min(xs))
    span_h = max(1.0, max(ys) - min(ys))
    # 源图长宽比与四边形外接框长宽比的折中，避免拉伸失真过大
    k = min(span_w / max(1.0, src_w), span_h / max(1.0, src_h))
    k = max(0.05, min(k, 8.0))
    w = int(round(src_w * k))
    h = int(round(src_h * k))
    if max(w, h) > max_side:
        s = max_side / float(max(w, h))
        w, h = max(1, int(w * s)), max(1, int(h * s))
    return max(1, w), max(1, h)
