# 总编排：把稿纸墨迹贴到草稿纸照片上
#
# 参数规格（PARAMS）在这里统一定义：名字、中文标签、默认值、上下限、步长。
# 前端据此生成滑块，后端据此夹范围 —— 只有一份定义，前后端不会漂移，
# 也不会出现"界面上能调到 3 而后端只认 1"这种问题。
import numpy as np
from PIL import Image

from . import blend, geom, ink, scan

# 墨色可选项（键必须与 ink.INK_COLORS 对齐）
INK_CHOICES = [
    ("black", "黑"), ("blueblack", "蓝黑"), ("blue", "蓝"),
    ("green", "绿"), ("red", "红"), ("gray", "灰"),
]

PARAMS = [
    # ---- 墨迹 ----
    {"k": "ink_color", "group": "墨迹", "label": "墨色", "type": "enum",
     "default": "black", "options": [list(c) for c in INK_CHOICES]},
    {"k": "density", "group": "墨迹", "label": "墨的浓淡", "type": "range",
     "default": 1.0, "min": 0.4, "max": 1.8, "step": 0.02},
    {"k": "bleed", "group": "墨迹", "label": "晕染", "type": "range",
     "default": 0.45, "min": 0.0, "max": 1.0, "step": 0.02},
    {"k": "uneven", "group": "墨迹", "label": "浓淡不匀", "type": "range",
     "default": 0.35, "min": 0.0, "max": 1.0, "step": 0.02},
    {"k": "ink_alpha", "group": "墨迹", "label": "字的透明度", "type": "range",
     "default": 1.0, "min": 0.15, "max": 1.0, "step": 0.02},
    # ---- 光影 ----
    {"k": "light_follow", "group": "光影", "label": "跟随原图光影", "type": "range",
     "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.02},
    {"k": "texture", "group": "光影", "label": "纸纹吃墨", "type": "range",
     "default": 0.35, "min": 0.0, "max": 1.0, "step": 0.02},
    # 手动光源：只作用在字上，绝不改变纸面（在画布上拖动光点即可定位）
    {"k": "light_on", "group": "光影", "label": "手动添加光源", "type": "bool",
     "default": False},
    {"k": "light_gain", "group": "光影", "label": "光源强度", "type": "range",
     "default": 0.45, "min": -1.0, "max": 1.0, "step": 0.02, "needs": "light_on"},
    {"k": "light_r", "group": "光影", "label": "光斑大小", "type": "range",
     "default": 0.45, "min": 0.05, "max": 1.5, "step": 0.01, "needs": "light_on"},
    {"k": "light_soft", "group": "光影", "label": "边缘柔和", "type": "range",
     "default": 0.55, "min": 0.0, "max": 1.0, "step": 0.02, "needs": "light_on"},
    {"k": "light_x", "group": "光影", "label": "光源横位置", "type": "range",
     "default": 0.35, "min": 0.0, "max": 1.0, "step": 0.005, "needs": "light_on"},
    {"k": "light_y", "group": "光影", "label": "光源纵位置", "type": "range",
     "default": 0.35, "min": 0.0, "max": 1.0, "step": 0.005, "needs": "light_on"},
    # ---- 扫描件 ----
    {"k": "scan_on", "group": "扫描件", "label": "处理成扫描件", "type": "bool",
     "default": False},
    {"k": "flatten", "group": "扫描件", "label": "压平光影", "type": "range",
     "default": 0.85, "min": 0.0, "max": 1.0, "step": 0.02, "needs": "scan_on"},
    {"k": "neutral", "group": "扫描件", "label": "白平衡", "type": "range",
     "default": 0.6, "min": 0.0, "max": 1.0, "step": 0.02, "needs": "scan_on"},
    {"k": "contrast", "group": "扫描件", "label": "色阶", "type": "range",
     "default": 0.35, "min": 0.0, "max": 1.0, "step": 0.02, "needs": "scan_on"},
    {"k": "noise", "group": "扫描件", "label": "颗粒", "type": "range",
     "default": 0.05, "min": 0.0, "max": 1.0, "step": 0.02, "needs": "scan_on"},
    {"k": "warmth", "group": "扫描件", "label": "色温", "type": "range",
     "default": 0.0, "min": -1.0, "max": 1.0, "step": 0.02, "needs": "scan_on"},
]

SPEC = {p["k"]: p for p in PARAMS}


def defaults():
    return {p["k"]: p["default"] for p in PARAMS}


def clamp(params):
    """按规格夹范围。缺的补默认；非法值退回默认（永不信任外部输入）。"""
    out = defaults()
    for k, v in (params or {}).items():
        s = SPEC.get(k)
        if s is None:
            continue
        try:
            if s["type"] == "bool":
                out[k] = bool(v)
            elif s["type"] == "range":
                out[k] = float(min(max(float(v), s["min"]), s["max"]))
            elif s["type"] == "enum":
                ok = [o[0] for o in s["options"]]
                out[k] = v if v in ok else s["default"]
        except (TypeError, ValueError):
            out[k] = s["default"]
    return out


def process(paper_img, ink_img, quad, params=None, seed=0):
    """把 ink_img 的墨迹按 quad 贴到 paper_img 上，返回新的 PIL 图。

    quad 是 paper_img 坐标系里的四个点（左上、右上、右下、左下）。
    """
    p = clamp(params)
    paper = paper_img.convert("RGB")
    W, H = paper.size
    quad = geom.check_quad(quad, W, H)
    x0, y0, x1, y1 = geom.quad_bbox(quad, W, H, pad=8)
    cw, ch = x1 - x0, y1 - y0
    if cw < 4 or ch < 4:
        raise geom.Degenerate("选中的区域太小（%dx%d 像素），请把四角拖开一些" % (cw, ch))

    q = [(float(x - x0), float(y - y0)) for x, y in quad]   # 换到裁剪窗口的局部坐标
    sub = paper.crop((x0, y0, x1, y1))
    sub_rgb = np.asarray(sub, dtype=np.float32) / 255.0

    # 墨迹先缩放到四边形的实际尺度再透视进来（原尺寸直接缩会走样）
    tw, th = geom.fit_rect_size(q, ink_img.width, ink_img.height)
    src = ink_img.convert("L").resize((tw, th), Image.LANCZOS)
    code = geom.perspective_coeffs(q, tw, th)
    # fillcolor=255：四边形之外当作"没有墨"（填 0 会被当成满墨）
    warped = src.transform((cw, ch), Image.PERSPECTIVE, code, Image.BICUBIC, fillcolor=255)
    cover = (Image.new("L", (tw, th), 255)
             .transform((cw, ch), Image.PERSPECTIVE, code, Image.BILINEAR, fillcolor=0))

    mask = ink.extract_ink(warped) * (np.asarray(cover, dtype=np.float32) / 255.0)
    rng = np.random.default_rng(int(seed) & 0x7FFFFFFF)
    mask = ink.bleed(mask, p["bleed"], rng, p["uneven"])
    albedo = ink.tint_record(mask, p["ink_color"], p["density"])

    # 光源权重场按**整幅图**的坐标算，再裁到当前窗口 —— 这样光源位置
    # 与画布上拖动看到的光点一致，不会因为裁剪窗口而偏移
    light = None
    if p["light_on"]:
        full_w = blend.light_weight((H, W), p["light_x"], p["light_y"],
                                    p["light_r"], p["light_soft"])
        light = np.ascontiguousarray(full_w[y0:y1, x0:x1])
    mask, albedo = blend.modulate_ink(mask, albedo, p["ink_alpha"], light,
                                      p["light_gain"] if p["light_on"] else 0.0)

    detail = blend.paper_detail(sub_rgb, 1.5) if p["texture"] > 0 else None
    out = blend.compositing(sub_rgb, mask, albedo, p["light_follow"],
                            p["texture"], detail)
    result = paper.copy()
    result.paste(Image.fromarray(np.clip(out * 255.0, 0, 255).astype(np.uint8)), (x0, y0))
    if p["scan_on"]:
        # 扫描件要作用在**整张图**上：只处理四边形那块的话，周围还是原来的灰照片，
        # 看上去就是"贴了一块白纸上去"，而不是"这张照片被扫描过"
        full = np.asarray(result, dtype=np.float32) / 255.0
        full = scan.scan(full, {k: p[k] for k in scan.DEF}, rng)
        result = Image.fromarray(np.clip(full * 255.0, 0, 255).astype(np.uint8))
    return result


def preview(paper_img, ink_img, quad, params=None, max_side=900, seed=0):
    """小图快速预览：把整张纸等比缩到 max_side 以内，四边形同步缩放。"""
    W, H = paper_img.size
    k = min(1.0, float(max_side) / max(W, H))
    if k >= 1.0:
        return process(paper_img, ink_img, quad, params, seed)
    pw, ph = max(1, int(W * k)), max(1, int(H * k))
    small = paper_img.resize((pw, ph), Image.LANCZOS)
    q = [(x * k, y * k) for x, y in quad]
    return process(small, ink_img, q, params, seed)
