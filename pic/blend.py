# 合成：把墨压到纸面照片上，并让它跟着原图的光影走
#
# 物理关系（也是本模块存在的理由）：
#   相机看到的 = 纸的反照率 × 光照。墨是透光的，所以有墨的地方近似是
#       输出 = 纸面像素 × (1 - 墨量 × (1 - 墨反照率))
#   这样墨自动跟着纸的明暗走 —— 阴影里的墨也变暗，亮处的墨也变亮，
#   不需要额外估计"光照"，因为纸面像素本身已经把光照带进来了。
#
# light_follow 在这套物理做法和"平涂贴上去"之间插值：
#   1.0 = 完全跟着纸走（真实）
#   0.0 = 墨是固定深度的色块，无视纸的明暗（一眼就是贴上去的）
import numpy as np


def illumination(paper_rgb, radius):
    """估计光照场：大幅度模糊后的亮度，归一化到均值 1。用于「校正纸面明暗」与扫描件。"""
    if isinstance(paper_rgb, np.ndarray) and paper_rgb.ndim == 3:
        luma = paper_rgb[:, :, 0] * 0.299 + paper_rgb[:, :, 1] * 0.587 + paper_rgb[:, :, 2] * 0.114
    else:
        luma = np.asarray(paper_rgb, dtype=np.float32)
    from PIL import Image, ImageFilter
    h, w = luma.shape
    r = max(1, int(radius))
    small = Image.fromarray(np.clip(luma * 255.0 if luma.max() <= 1.5 else luma, 0, 255)
                            .astype(np.uint8))
    small = small.resize((max(2, w // 8), max(2, h // 8)), Image.BILINEAR)
    small = small.filter(ImageFilter.GaussianBlur(r / 8.0))
    big = np.asarray(small.resize((w, h), Image.BICUBIC), dtype=np.float32)
    if luma.max() > 1.5:
        big /= 255.0
    m = float(big.mean())
    return np.clip(big / max(1e-4, m), 0.05, 4.0).astype(np.float32)


def paper_detail(paper_rgb, radius=1.5):
    """纸面纹理：高通细节，均值 1。墨会陷进纹理里，被它调制后看起来才"长在纸上"。"""
    from PIL import Image, ImageFilter
    if paper_rgb.ndim == 3:
        luma = paper_rgb[:, :, 0] * 0.299 + paper_rgb[:, :, 1] * 0.587 + paper_rgb[:, :, 2] * 0.114
    else:
        luma = paper_rgb
    im = Image.fromarray(np.clip(luma * 255.0, 0, 255).astype(np.uint8))
    low = np.asarray(im.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255.0
    hi = luma - low                            # 高通
    return (1.0 + hi * 3.0).astype(np.float32)  # 均值 1 附近波动


def light_weight(shape, cx, cy, radius, soft):
    """手动光源的权重场（0..1）。**只用来调制墨**，不碰纸面。

    cx/cy 是相对整幅图的 0..1 坐标，radius 是相对长边的比例。
    衰减用 1/(1+d^p)：p 由 soft 控制，soft 越大过渡带越宽、光斑越柔。
    """
    h, w = shape[:2]
    r = max(1e-3, float(radius) * max(w, h))
    yy = (np.arange(h, dtype=np.float32)[:, None] + 0.5 - float(cy) * h) / r
    xx = (np.arange(w, dtype=np.float32)[None, :] + 0.5 - float(cx) * w) / r
    d = np.sqrt(xx * xx + yy * yy)
    p = 6.0 - 5.0 * float(np.clip(soft, 0.0, 1.0))     # soft=0 接近硬边，soft=1 很柔
    return (1.0 / (1.0 + d ** p)).astype(np.float32)


def modulate_ink(alpha, albedo, ink_alpha=1.0, light=None, gain=0.0):
    """按「字的透明度」与「手动光源」调制墨，返回新的 (alpha, albedo)。

    ink_alpha : 整体透明度。越小墨越透，纸的光影与纹理越明显地透出来。
    light     : light_weight 算出的权重场，None 表示不开光源。
    gain      : 正 = 被照亮（墨变淡、发灰，像反光），负 = 被压深（墨更浓）。

    关键：这里只动 alpha 与 albedo 两个属于**墨**的量，纸面像素不参与，
    所以光源绝不可能改变纸 —— 这正是「只影响字」的实现方式。
    """
    a = np.clip(alpha * float(np.clip(ink_alpha, 0.0, 1.0)), 0.0, 1.0)
    al = albedo
    g = float(gain)
    if light is not None and abs(g) > 1e-6:
        w3 = light[:, :, None] if al.ndim == 3 else light
        if g > 0:
            # 照亮：墨量被冲淡，同时墨色向纸色靠（发灰、发白）
            a = np.clip(a * (1.0 - g * light * 0.85), 0.0, 1.0)
            al = al + (1.0 - al) * (g * w3 * 0.75)
        else:
            # 压深：墨量不减，把墨的反照率**压下去**（反照率越低墨越黑）
            al = albedo * (1.0 - 0.85 * (-g) * w3)
    return a, np.clip(al, 0.0, 1.0)


def compositing(paper_rgb, alpha, albedo, light_follow=1.0, texture=0.35, detail=None):
    """核心合成。全部输入为 0..1 float。

    paper_rgb : HxWx3 纸面照片（原样使用，不额外压暗）
    alpha     : HxW   墨量（0..1）
    albedo    : HxWx3 墨的反照率
    light_follow : 1=墨完全跟着纸的明暗；0=平涂
    texture   : 纸纹对墨量的调制强度（墨陷进纤维里，浓淡不均）
    detail    : 纸面高频细节（均值 1），texture>0 时用
    """
    a = np.clip(alpha, 0.0, 1.0)
    if texture > 0 and detail is not None:
        # 亮（凸）处墨薄一点、暗（凹）处墨积得厚一点
        a = np.clip(a * (1.0 + float(texture) * (detail - 1.0) * 0.9), 0.0, 1.0)

    k = float(np.clip(light_follow, 0.0, 1.0))
    # 物理做法：墨乘在纸的像素上（自动跟随光影）
    phys = paper_rgb * (1.0 - a[:, :, None] * (1.0 - albedo))
    # 平涂做法：固定墨色直接盖上去（无视光影）
    flat = paper_rgb * (1.0 - a[:, :, None]) + albedo * a[:, :, None]
    return np.clip((1.0 - k) * flat + k * phys, 0.0, 1.0)


def to_pil(rgb):
    return __import__("PIL.Image", fromlist=["Image"]).fromarray(
        np.clip(rgb * 255.0, 0, 255).astype(np.uint8))
