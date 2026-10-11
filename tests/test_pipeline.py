# 图像管线测试：验证的是"物理关系成立"，不是"跑通了"
#
# 关键几条：
#   ① 四边形之内才有墨，之外逐像素不变
#   ② 跟随光影：暗处的墨必须比亮处的墨更暗；关掉跟随则差异应大幅缩小（差分对照）
#   ③ 晕染会扩大墨的覆盖面积；浓度会加深墨色
#   ④ 扫描件会把纸面的明暗不匀压平（用标准差量化）
#   ⑤ 参数越界必须被夹回区间，而不是报错或原样放行
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw, ImageFont                      # noqa: E402

from pic import geom, pipeline                                   # noqa: E402

fails = []
total = [0]


def check(name, ok, extra=""):
    total[0] += 1
    print(("  PASS " if ok else "  FAIL ") + name + ("  <- " + str(extra) if extra else ""))
    if not ok:
        fails.append(name)


def make_paper(w=900, h=640, dark_band=True):
    """合成一张"草稿纸照片"：左暗右亮的渐变光照 + 纸纹 + 淡黄底色"""
    rng = np.random.default_rng(11)
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
    light = 0.62 + 0.34 * x                       # 左边暗、右边亮
    if dark_band:
        y = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
        light = light * (1.0 - 0.22 * np.clip((y - 0.55) / 0.45, 0, 1))
    base = np.stack([light * (0.985 - 0.03 * np.clip(1 - x, 0, 1)),
                     light * 0.975,
                     light * 0.93], axis=2)
    fib = rng.normal(0.0, 0.012, (h, w, 1)).astype(np.float32)
    arr = np.clip(base + fib, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def make_ink(w=520, h=150):
    """合成一张"手写稿"：白底黑字（粗细不同的笔画）"""
    im = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(im)
    # 只用 ASCII：consola 这类等宽字体没有中文字形，写中文会得到豆腐块，
    # 那是 fixture 的问题，会让人误以为管线画错了
    try:
        f = ImageFont.truetype("consola.ttf", 54)
    except Exception:                                            # noqa: BLE001
        f = ImageFont.load_default()
    d.text((16, 18), "Ag handwriting", fill=8, font=f)
    d.line((20, 108, 470, 116), fill=30, width=5)                # 加一条长笔画
    d.line((24, 128, 300, 122), fill=60, width=3)
    return im


PAPER = make_paper()
INK = make_ink()
W, H = PAPER.size
QUAD = [(120, 90), (800, 140), (770, 540), (90, 470)]


def quad_region(arr, quad, inset=0.12):
    """取四边形内侧的一小块（缩到中心 12% 处，避免边缘插值干扰）"""
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    cx0, cx1 = min(xs), max(xs)
    cy0, cy1 = min(ys), max(ys)
    dx, dy = (cx1 - cx0) * inset, (cy1 - cy0) * inset
    return arr[int(cy0 + dy):int(cy1 - dy), int(cx0 + dx):int(cx1 - dx)]


paper_a = np.asarray(PAPER, dtype=np.float32)

# ---------- ① 只在四边形之内落墨 ----------
out = pipeline.process(PAPER, INK, QUAD, {"bleed": 0.4, "density": 1.0})
oa = np.asarray(out, dtype=np.float32)
inside_before = quad_region(paper_a, QUAD).mean()
inside_after = quad_region(oa, QUAD).mean()
check("四边形之内明显变暗（墨落进去了）", inside_after < inside_before - 8,
      "前 %.1f -> 后 %.1f" % (inside_before, inside_after))

mask_out = np.ones(oa.shape[:2], dtype=bool)
x0, y0, x1, y1 = geom.quad_bbox(QUAD, W, H, pad=0)
mask_out[y0:y1, x0:x1] = False
d_out = np.abs(oa - paper_a)[mask_out].max()
check("四边形之外逐像素不变（最多 ±1 舍入）", d_out <= 1.0, "最大差异 %.1f" % d_out)

# ---------- ② 跟随光影（差分对照：开 / 关） ----------
def ink_darkness(follow):
    o = np.asarray(pipeline.process(PAPER, INK, QUAD,
                                    {"light_follow": follow, "bleed": 0.2, "texture": 0.0}),
                   dtype=np.float32)
    reg = quad_region(o, QUAD)
    pa = quad_region(paper_a, QUAD)
    # 墨迹越深，与纸面的差越大
    return float((pa - reg).mean()), reg.mean()


d_follow, _ = ink_darkness(1.0)
d_flat, _ = ink_darkness(0.0)
check("跟随光影时墨更深（因为纸面本身有明暗）", d_follow > d_flat,
      "跟随 %.1f  vs  平涂 %.1f" % (d_follow, d_flat))

# 暗处的墨必须比亮处的墨更暗：分别在左（暗）右（亮）半边取墨迹区域
o_follow = np.asarray(pipeline.process(PAPER, INK, QUAD,
                                       {"light_follow": 1.0, "bleed": 0.2, "texture": 0.0}),
                      dtype=np.float32)
o_flat = np.asarray(pipeline.process(PAPER, INK, QUAD,
                                     {"light_follow": 0.0, "bleed": 0.2, "texture": 0.0}),
                    dtype=np.float32)
mid = (QUAD[0][0] + QUAD[1][0]) // 2
left_slice = slice(int(mid * 0.35), int(mid * 0.65))
right_slice = slice(int(mid * 1.35), int(mid * 1.65))
ly = slice(int(QUAD[0][1] * 1.05), int(QUAD[3][1] * 0.95))
lf = o_follow[ly, left_slice].mean()
rf = o_follow[ly, right_slice].mean()
lfl = o_flat[ly, left_slice].mean()
rfl = o_flat[ly, right_slice].mean()
check("跟随模式：暗处的墨比亮处更暗", rf > lf + 2.0,
      "暗处 %.1f / 亮处 %.1f" % (lf, rf))
check("平涂模式：明暗差异被明显抹平", (rf - lf) > (rfl - lfl),
      "跟随差 %.1f，平涂差 %.1f" % (rf - lf, rfl - lfl))

# ---------- ③ 晕染扩大覆盖，浓度加深墨色 ----------
def coverage(bleed):
    o = np.asarray(pipeline.process(PAPER, INK, QUAD,
                                    {"bleed": bleed, "density": 1.0, "texture": 0.0}),
                   dtype=np.float32)
    lo = quad_region(o, QUAD).min(axis=2)
    hi = quad_region(paper_a, QUAD).min(axis=2)
    return int((lo < hi - 12).sum())


c0, c1 = coverage(0.0), coverage(1.0)
check("晕染越大，着墨的像素越多", c1 > c0 * 1.05, "bleed0=%d  bleed1=%d" % (c0, c1))

def ink_core(density):
    """只统计「确实是墨」的像素，取它们偏暗的那一档。
       用整片区域的平均会被大片白纸稀释 —— 实测浓度从 0.6 拉到 1.6，
       区域均值只差 0.5，而墨像素本身差了 4 个灰阶（本测试踩过）。"""
    o = np.asarray(pipeline.process(PAPER, INK, QUAD,
                                    {"density": density, "bleed": 0.2, "texture": 0.0}),
                   dtype=np.float32)
    reg, pa = quad_region(o, QUAD), quad_region(paper_a, QUAD)
    m = (pa.min(axis=2) - reg.min(axis=2)) > 12          # 墨迹像素掩膜
    if m.sum() < 100:
        return -1.0
    # reg[m] 已经是 N×3（二维掩膜索引三维数组会自动拍平），沿 axis=1 求各像素的均值
    return float(np.percentile(reg[m].mean(axis=1), 15))


d_light, d_heavy = ink_core(0.6), ink_core(1.6)
check("墨越浓，墨像素本身越暗", d_heavy < d_light - 2.0,
      "浓1.6=%.1f  淡0.6=%.1f" % (d_heavy, d_light))

# ---------- ④ 扫描件压平光影 ----------
# 先在整张纸上单独测扫描模块（这样采样的两块都在处理范围内 ——
# 之前直接量 pipeline 的输出，取样块跨在裁剪边界上，一半是没处理的原图，结论全错）
paper_rgb01 = paper_a / 255.0
lum_before = np.asarray(PAPER.convert("L"), dtype=np.float32)


def paper_patch(a):
    return np.concatenate([a[60:140, 60:240].ravel(), a[40:120, 640:860].ravel()])


from pic import scan as scanmod                                   # noqa: E402

sc_full = scanmod.scan(paper_rgb01, {"flatten": 0.95, "neutral": 0.6,
                                     "contrast": 0.4, "noise": 0.05})
sc_l = (sc_full[:, :, 0] * 0.299 + sc_full[:, :, 1] * 0.587 + sc_full[:, :, 2] * 0.114) * 255.0
b, s = paper_patch(lum_before), paper_patch(sc_l)
check("扫描件把纸面明暗压平（标准差降到 1/3 以下）", s.std() < b.std() / 3.0,
      "原 std=%.1f -> 扫描 std=%.1f" % (b.std(), s.std()))
check("扫描件纸面更白（均值上升）", s.mean() > b.mean() + 10,
      "原 %.1f -> 扫描 %.1f" % (b.mean(), s.mean()))

# 管线里确实接上了扫描：开与关相比，四边形内的纸面均值应当明显不同
off = np.asarray(pipeline.process(PAPER, INK, QUAD, {"scan_on": False}), dtype=np.float32)
on = np.asarray(pipeline.process(PAPER, INK, QUAD, {"scan_on": True}), dtype=np.float32)
p_off, p_on = quad_region(off, QUAD).mean(), quad_region(on, QUAD).mean()
check("管线里确实走了扫描（开关带来明显差异）", abs(p_on - p_off) > 5.0,
      "关 %.1f  开 %.1f" % (p_off, p_on))

# ---------- ⑤ 参数夹范围 ----------
p = pipeline.clamp({"density": 99, "bleed": -5, "ink_color": "紫色", "scan_on": "yes",
                    "warmth": 100})
check("数值越界被夹到上界", p["density"] == pipeline.SPEC["density"]["max"], p["density"])
check("数值越界被夹到下界", p["bleed"] == pipeline.SPEC["bleed"]["min"], p["bleed"])
check("非法枚举退回默认", p["ink_color"] == "black", p["ink_color"])
check("布尔按真值处理", p["scan_on"] is True)
check("缺省项补齐默认值", pipeline.clamp({})["texture"] == pipeline.SPEC["texture"]["default"])
check("未知键被忽略不报错", "没这个键" not in pipeline.clamp({"没这个键": 1}))

# ---------- ⑥ 负例：退化四边形必须报错 ----------
try:
    pipeline.process(PAPER, INK, [(10, 10), (20, 20), (30, 30), (40, 40)])
    check("退化四边形 -> 抛 Degenerate", False, "竟然没抛")
except geom.Degenerate:
    check("退化四边形 -> 抛 Degenerate", True)
except Exception as e:                                           # noqa: BLE001
    check("退化四边形 -> 抛 Degenerate", False, "抛了别的：%r" % e)

# ---------- ⑦ 预览与整图结果在相对位置上一致 ----------
full = np.asarray(pipeline.process(PAPER, INK, QUAD, {"bleed": 0.3}), dtype=np.float32)
pv = pipeline.preview(PAPER, INK, QUAD, {"bleed": 0.3}, max_side=450)
pva = np.asarray(pv, dtype=np.float32)
k = pv.width / float(W)


def ink_centroid(a, base):
    d = base.min(axis=2) - a.min(axis=2)
    ys, xs = np.where(d > 12)
    if len(xs) == 0:
        return None
    return float(xs.mean()), float(ys.mean())


cf = ink_centroid(full, paper_a)
cp = ink_centroid(pva, np.asarray(PAPER.resize((pv.width, pv.height), Image.LANCZOS),
                                  dtype=np.float32))
ok = cf is not None and cp is not None
if ok:
    ok = abs(cf[0] * k - cp[0]) < max(6.0, pv.width * 0.02) and \
         abs(cf[1] * k - cp[1]) < max(6.0, pv.height * 0.02)
check("预览与整图的墨迹重心一致（缩放后偏差 < 2%）", ok,
      "整图 %s  预览 %s" % (None if cf is None else (round(cf[0] * k), round(cf[1] * k)),
                            None if cp is None else (round(cp[0]), round(cp[1]))))

# ---------- ⑧ 字的透明度 ----------
def ink_level(alpha):
    o = np.asarray(pipeline.process(PAPER, INK, QUAD,
                                    {"bleed": 0.2, "texture": 0.0, "ink_alpha": alpha}),
                   dtype=np.float32)
    reg, base = quad_region(o, QUAD), quad_region(paper_a, QUAD)
    m = (base.min(axis=2) - reg.min(axis=2)) > 12      # 着墨像素
    return float(reg[m].mean()) if m.any() else float("nan")


lv = [ink_level(a) for a in (1.0, 0.55, 0.20)]
check("字的透明度调小 -> 着墨处变亮（纸透出来）", lv[2] > lv[1] + 2 > lv[0] + 4,
      "1.0 -> %.1f，0.55 -> %.1f，0.20 -> %.1f" % tuple(lv))

# ---------- ⑨ 手动光源：只影响字，不碰纸 ----------
BASE_P = {"bleed": 0.2, "texture": 0.0}


def render(extra=None):
    pr = dict(BASE_P)
    if extra:
        pr.update(extra)
    return np.asarray(pipeline.process(PAPER, INK, QUAD, pr), dtype=np.float32)


def light_render(gain, lx=0.30, ly=0.35):
    return render({"light_on": True, "light_gain": gain, "light_r": 0.30,
                   "light_soft": 0.5, "light_x": lx, "light_y": ly})


o_off = render()
o_on = light_render(0.8)
delta = o_on - o_off
noink = np.abs(o_off - paper_a).max(axis=2) < 2.0      # 与纸面完全一致 = 没落墨
inkpx = ~noink

check("光源只影响字：没落墨处逐像素不变", float(np.abs(delta[noink]).max()) <= 1.0,
      "最大改变 %.1f（没落墨的像素 %d 个）" % (np.abs(delta[noink]).max(), int(noink.sum())))
check("光源确实改变了字", float(np.abs(delta[inkpx]).max()) > 4.0,
      "着墨处最大改变 %.1f" % np.abs(delta[inkpx]).max())

# 空间分布：光点附近的字应比远处的字被提亮得更多
LX, LY = 0.30 * W, 0.35 * H
yy, xx = np.mgrid[0:H, 0:W]
d2 = (xx - LX) ** 2 + (yy - LY) ** 2
near = inkpx & (d2 < (0.16 * max(W, H)) ** 2)
far = inkpx & (d2 > (0.45 * max(W, H)) ** 2)
check("光点附近的字提亮更多、远处几乎不动",
      near.any() and far.any() and delta[near].mean() > delta[far].mean() + 3.0,
      "近处 +%.1f / 远处 +%.1f" % (delta[near].mean(), delta[far].mean()))

d_neg = light_render(-0.8) - o_off
check("强度取负 -> 把字压深（不是纸）",
      float(d_neg[inkpx].min()) < -2.0 and float(np.abs(d_neg[noink]).max()) <= 1.0,
      "着墨处最深 %.1f / 没落墨处最大 %.1f" % (d_neg[inkpx].min(),
                                              np.abs(d_neg[noink]).max()))

check("移动光点会改变结果",
      float(np.abs(light_render(0.8, lx=0.80, ly=0.72) - o_on).max()) > 4.0)
check("关掉光源则完全不生效",
      float(np.abs(render({"light_on": False, "light_gain": 1.0,
                           "light_x": 0.9, "light_y": 0.9}) - o_off).max()) <= 1.0)

# ---------- ⑩ 参数表本身 ----------
check("纸张阴影与纸面保留已移除",
      "shadow" not in pipeline.SPEC and "paper_keep" not in pipeline.SPEC,
      "shadow=%s paper_keep=%s" % ("shadow" in pipeline.SPEC, "paper_keep" in pipeline.SPEC))
check("新增字的透明度与手动光源参数",
      all(k in pipeline.SPEC for k in ("ink_alpha", "light_on", "light_gain",
                                       "light_r", "light_soft", "light_x", "light_y")))
check("光源的从属参数在没开光源时被标记为依赖 light_on",
      all(pipeline.SPEC[k].get("needs") == "light_on"
          for k in ("light_gain", "light_r", "light_soft", "light_x", "light_y")))

# 落一份样张给人看
os.makedirs(os.path.join(ROOT, "output"), exist_ok=True)
pipeline.process(PAPER, INK, QUAD, {"bleed": 0.5, "scan_on": False}).save(
    os.path.join(ROOT, "output", "_测试_贴合.png"))
pipeline.process(PAPER, INK, QUAD, {"bleed": 0.5, "scan_on": True}).save(
    os.path.join(ROOT, "output", "_测试_扫描件.png"))
pipeline.process(PAPER, INK, QUAD,
                 {"bleed": 0.5, "light_on": True, "light_gain": 0.75,
                  "light_r": 0.32, "light_x": 0.34, "light_y": 0.34}).save(
    os.path.join(ROOT, "output", "_测试_光源.png"))

print("\n通过 %d 项，失败 %d 项" % (total[0] - len(fails), len(fails)))
sys.exit(1 if fails else 0)
