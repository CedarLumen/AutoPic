# 扫描件：把"拍出来的照片"整成"扫出来的文件"
#
# 顺序有讲究：先压平光照（否则后面的白平衡与色阶都是在补偿同一个不均匀性），
# 再做白平衡，最后才是色阶与噪声 —— 顺序错了会互相打架。
import numpy as np

from .blend import illumination

# 扫描件默认值（都做成可调，且都有下限保护）
DEF = {
    "flatten": 0.85,     # 压平光影：把光照不均去掉，纸面变匀
    "neutral": 0.6,      # 白平衡：把纸面偏向中性灰白，去掉偏黄/偏蓝
    "contrast": 0.35,    # 色阶：提亮纸面、压暗墨迹
    "noise": 0.05,       # 传感器噪声
    "warmth": 0.0,       # 色温偏移（正=偏暖，负=偏冷）
}


def _renorm(a):
    return np.clip(a, 0.0, 1.0)


def scan(rgb, params=None, rng=None):
    """输入输出都是 0..1 的 HxWx3。"""
    p = dict(DEF)
    if params:
        p.update({k: v for k, v in params.items() if v is not None})
    rng = rng or np.random.default_rng(7)
    out = np.clip(rgb, 0.0, 1.0).astype(np.float32)
    h, w = out.shape[:2]

    # ① 压平光照：除以低频光照场再归一化回白
    fl = float(np.clip(p["flatten"], 0.0, 1.0))
    if fl > 0:
        ill = illumination(out, radius=max(8.0, min(h, w) / 8.0))
        # 只在中间调上做除法，全强度会过冲
        corr = np.power(ill, -fl)[:, :, None]
        out = _renorm(out * corr)
        # 压平后可能整体变亮，重新贴回白场
        hi = float(np.percentile(out, 97))
        if hi > 1e-3:
            out = _renorm(out / hi)

    # ② 白平衡：取最亮的一小撮像素当"纸"，把它拉成中性
    ne = float(np.clip(p["neutral"], 0.0, 1.0))
    if ne > 0:
        luma = out[:, :, 0] * 0.299 + out[:, :, 1] * 0.587 + out[:, :, 2] * 0.114
        thr = np.percentile(luma, 92)
        m = luma >= thr
        if m.sum() > 16:
            ref = out[m].mean(axis=0)
            gray = float(ref.mean())
            gain = np.where(ref > 1e-4, gray / np.maximum(ref, 1e-4), 1.0)
            gain = 1.0 + (np.clip(gain, 0.6, 1.6) - 1.0) * ne
            out = _renorm(out * gain[None, None, :])

    # ③ 色阶：抬黑场把纸提白，墨自然更沉
    ct = float(np.clip(p["contrast"], 0.0, 1.0))
    if ct > 0:
        low = 0.30 * ct
        out = _renorm((out - low) / max(1e-3, 1.0 - low))
        out = np.power(out, 1.0 / (1.0 + 0.25 * ct))     # 轻微提亮中调

    # ④ 色温
    wm = float(np.clip(p["warmth"], -1.0, 1.0))
    if abs(wm) > 1e-3:
        out = _renorm(out * np.asarray([1.0 + 0.05 * wm, 1.0, 1.0 - 0.05 * wm],
                                       dtype=np.float32)[None, None, :])

    # ⑤ 噪声：扫描仪不是无噪声的，一点颗粒反而更像扫描件
    nz = float(np.clip(p["noise"], 0.0, 1.0))
    if nz > 0:
        out = _renorm(out + rng.normal(0.0, 0.035 * nz, out.shape).astype(np.float32))
    return out.astype(np.float32)
