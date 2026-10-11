# 墨迹：从稿纸图里取出墨量、模拟纸纤维晕染、上墨色
#
# 全程用 0..1 的浮点数组表示「墨量」（1 = 最浓），不做二值化 ——
# 手写的轻重变化全在这些中间值里，二值化会把它抹平。
import numpy as np
from PIL import Image, ImageFilter


def _to_arrays(img):
    """返回 (luma 0..1, alpha 0..1)；alpha 全 1 表示不透明"""
    im = img.convert("RGBA")
    a = np.asarray(im, dtype=np.float32) / 255.0
    rgb, alpha = a[:, :, :3], a[:, :, 3]
    luma = rgb[:, :, 0] * 0.299 + rgb[:, :, 1] * 0.587 + rgb[:, :, 2] * 0.114
    return luma, alpha


def extract_ink(img, gamma=1.0):
    """把「白纸黑字」的稿纸图转成 0..1 的墨量图。

    - 透明底（抠好的墨迹 PNG）：用 1-alpha 当墨量
    - 白底黑字：用 1-luma
    - 深底浅字（负片式的图）：自动识别并翻转，否则会得到一整片墨
    最后按白场归一化：把「纸面最亮的那一档」当作 0，避免把底噪当墨。
    """
    luma, alpha = _to_arrays(img)
    if luma.mean() < 0.5:                    # 整图偏暗 = 深底浅字
        luma = 1.0 - luma
    ink = 1.0 - luma
    if alpha.min() < 0.999:                  # 有透明通道就用它
        ink = np.maximum(ink, 1.0 - alpha)
    # 白场：取亮度的高分位当纸面，低于它的才当墨，避免把纸面噪声算进来
    paper = float(np.percentile(luma, 90))
    if paper > 1e-3:
        ink = np.clip(ink / max(1e-3, paper * 1.0), 0.0, 1.0)
    ink = np.clip(ink, 0.0, 1.0)
    if gamma != 1.0:
        ink = np.power(ink, gamma)
    return ink.astype(np.float32)


def _blur(arr, radius):
    if radius <= 0:
        return arr
    im = Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255.0


def fiber_noise(shape, rng, scale=6, octaves=3):
    """纸纤维噪声（多尺度叠加的高斯随机场），值域大致 0..1，均值 ~0.5"""
    h, w = shape
    out = np.zeros(shape, dtype=np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        s = max(2, int(scale * (2 ** o)))
        small = rng.random((max(2, h // s + 1), max(2, w // s + 1))).astype(np.float32)
        layer = np.asarray(Image.fromarray((small * 255).astype(np.uint8)).resize(
            (w, h), Image.BICUBIC), dtype=np.float32) / 255.0
        out += layer * amp
        tot += amp
        amp *= 0.55
    out /= max(1e-6, tot)
    return np.clip(out, 0.0, 1.0)


def bleed(mask, strength=0.5, rng=None, uneven=0.35):
    """晕染：墨在纸纤维上会往外洇一圈，边缘毛糙，浓度不匀。

    strength = 0 就是干净的矢量墨迹；越大越像钢笔写在毛边纸上。
    返回 0..1 的墨量（比输入更"胖"、更不匀）。
    """
    rng = rng or np.random.default_rng(0)
    if strength <= 0:
        return mask.astype(np.float32)
    h, w = mask.shape
    # ① 洇边：中小半径的扩散，形成一个"淡晕"
    halo = _blur(mask, 1.0 + 2.5 * strength)
    # ② 纤维牵引：让扩散不均匀，像被纸纤维吸着走
    fib = fiber_noise((h, w), rng, scale=max(2, int(2 + 6 * (1.0 - strength))), octaves=4)
    halo = halo * (0.55 + 0.9 * fib)
    # ③ 芯部略收，保证笔画中心仍然浓
    core = np.power(mask, 1.0 + 0.5 * strength)
    alpha = np.maximum(core, halo * (0.22 + 0.5 * strength))
    # ④ 浓度不匀：低频噪声调制（模拟下笔轻重与墨水分布）
    if uneven > 0:
        nr = fiber_noise((h, w), rng, scale=max(4, int(10 + 30 * (1.0 - uneven))), octaves=2)
        alpha = alpha * (1.0 - uneven * 0.55 + uneven * 1.1 * nr)
    return np.clip(alpha, 0.0, 1.0).astype(np.float32)


INK_COLORS = {
    # 名字 -> (R, G, B) 的墨色（0..1）。注意这是"纯墨"的反照率，
    # 实际落到纸上时还会乘以纸面亮度，所以这里的值偏暗是正常的。
    "black": (0.07, 0.07, 0.08),
    "blue": (0.10, 0.16, 0.42),
    "blueblack": (0.09, 0.11, 0.26),
    "gray": (0.22, 0.22, 0.24),
    "red": (0.52, 0.10, 0.12),
    "green": (0.10, 0.30, 0.18),
}


def tint_record(mask, color="black", density=1.0):
    """把墨量图变成「墨的反照率」RGB（0..1）。

    真实墨水是透光的：相机看到的是 纸面颜色 × 墨的反照率。
    所以这里只产出反照率，**不碰光照** —— 光照适配是合成阶段的事
    （在这里乘一次、合成时又乘一次，会把光影算重，墨会过暗）。
    """
    if isinstance(color, str):
        base = np.asarray(INK_COLORS.get(color, INK_COLORS["black"]), dtype=np.float32)
    else:
        base = np.asarray(color, dtype=np.float32)
    base = np.clip(base, 0.0, 1.0)
    density = float(np.clip(density, 0.05, 2.0))
    # 墨水浓 -> 反照率低；density 用来缩放这个效应
    albedo = np.clip(base[None, None, :] * (1.0 - 0.55 * (density - 1.0)), 0.0, 1.0)
    h, w = mask.shape
    return np.clip(np.broadcast_to(albedo, (h, w, 3)), 0.0, 1.0).astype(np.float32)
