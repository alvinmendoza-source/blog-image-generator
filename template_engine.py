"""Simple brand template engine for the 🎨 Create Template tab.

website → logo (header brand link) → brand colours (taken FROM THE LOGO) → random designs.

A design = one photo shape (7) × one background (white / light tint / dark / brand colour)
× at most one small detail, with logo + title on the left (house style of the approved Figma
templates). Output is a full-canvas OVERLAY (opaque brand surface + logo, transparent where the
blog photo shows) plus title-box coords, so the app's existing make_tpls() + composite_template()
use it exactly like a Figma template. No AI, no Kie credits.

Pure module: no Streamlit calls.
"""
import io
import re
import random
import colorsys
import collections
import html as htmlmod
from urllib.parse import urljoin

import numpy as np
import requests
from PIL import Image, ImageDraw

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Safari/537.36"}
# website font-family → font file stem in template_assets/
FONTS = {"satoshi": "Satoshi", "inter": "Inter", "fustat": "Fustat",
         "plus jakarta sans": "PlusJakartaSans", "outfit": "Outfit", "mulish": "Mulish",
         "open sans": "OpenSans", "roboto": "Roboto", "mona sans": "MonaSans",
         "libre franklin": "LibreFranklin"}
DEFAULT_FONT = "Satoshi"
# Colours that come from the shared Webflow site template, not from the client's brand.
COMMON_CSS = {"ffbb29", "ffb300", "3898ec", "ffffff", "000000"}
SS = 3  # supersampling for smooth shape edges

SHAPES = ["straight", "slant", "curve", "ring", "disc", "card", "band"]
SHAPE_LABELS = {"straight": "Straight edge", "slant": "Slanted edge", "curve": "Curved edge",
                "ring": "Circle + ring", "disc": "Circle + brand circle", "card": "Photo card",
                "band": "Photo top + band"}
BG_LABELS = {"white": "white", "tint": "light tint", "deep": "dark", "brand": "brand colour"}
BG_ORDER = ["white", "tint", "deep", "brand"]


# ───────────────────────── colour helpers ─────────────────────────
def lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def hls(c):
    return colorsys.rgb_to_hls(*(v / 255 for v in c[:3]))


def from_hls(h, l, s):
    return tuple(int(round(v * 255)) for v in colorsys.hls_to_rgb(h, l, s))


def hexc(c):
    return "#%02X%02X%02X" % tuple(c[:3])


def hex_to_rgb(h):
    h = (h or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (13, 33, 64)


def _arr_ls(rgb):
    """Vectorised HLS lightness + saturation for an (..., 3) float array in 0..1."""
    mx, mn = rgb.max(-1), rgb.min(-1)
    l = (mx + mn) / 2
    d = mx - mn
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(d == 0, 0.0, np.where(l <= 0.5, d / (mx + mn), d / (2 - mx - mn)))
    return l, np.nan_to_num(s)


def _arr_lum(rgb):
    v = np.where(rgb <= 0.03928, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return 0.2126 * v[..., 0] + 0.7152 * v[..., 1] + 0.0722 * v[..., 2]


def _arr_contrast(rgb, c):
    la, lb = _arr_lum(rgb), lum(c)
    return (np.maximum(la, lb) + 0.05) / (np.minimum(la, lb) + 0.05)


def _small(im, n):
    sm = im.convert("RGBA").copy()
    sm.thumbnail((n, n))
    return np.asarray(sm).astype(float)


# ───────────────────────── website → logo ─────────────────────────
def fetch(url, binary=False, timeout=20):
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.content if binary else r.text


def _svg_to_img(svg):
    import resvg_py
    if "xmlns" not in svg[:400]:
        svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    first = Image.open(io.BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=svg))))
    zoom = max(1, 1400 / max(1, max(first.size)))
    return Image.open(io.BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=svg, zoom=zoom))))


def crop_alpha(im):
    im = im.convert("RGBA")
    bb = im.split()[-1].getbbox()
    return im.crop(bb) if bb else im


def load_image(src, base):
    """src may be a URL or an inline <svg>…</svg> string."""
    if src.lstrip().startswith("<svg"):
        im = _svg_to_img(src)
    else:
        url = urljoin(base, htmlmod.unescape(src))
        raw = fetch(url, binary=True)
        if url.lower().split("?")[0].endswith(".svg") or raw[:200].lstrip().startswith((b"<svg", b"<?xml")):
            im = _svg_to_img(raw.decode("utf-8-sig", "replace"))
        else:
            im = Image.open(io.BytesIO(raw))
    return crop_alpha(im)


# Partner / vendor logos that sit on MSP homepages next to the client's own logo.
_VENDOR_RE = (r'partner|client|award|badge|cert|microsoft|azure|google|3cx|lenovo|dell|hp|'
              r'hp_logo|cisco|fortinet|sophos|datto|connectwise|kaseya|ninja|sentinel|crowdstrike|'
              r'huntress|aws|amazon|vmware|sonicwall|meraki|ubiquiti|apple|intel|adobe|zoom|'
              r'barracuda|mimecast|veeam|webroot|n-able|solarwinds|watchguard|palo ?alto|okta')


def logo_candidates(page):
    """Ordered logo sources: header brand link first, then anything tagged 'logo'."""
    out = []
    for m in re.finditer(r'<a\b[^>]*>(.*?)</a>', page, re.I | re.S):
        open_tag = page[m.start():m.start() + page[m.start():].find(">") + 1]
        if not re.search(r'w-nav-brand|brand|logo', open_tag, re.I) and 'href="/"' not in open_tag:
            continue
        inner = m.group(1)
        im = re.search(r'<img\b[^>]*\bsrc="([^"]+)"', inner, re.I)
        sv = re.search(r'<svg\b.*?</svg>', inner, re.I | re.S)
        if im:
            out.append(im.group(1))
        elif sv:
            out.append(sv.group(0))
        if len(out) >= 3:
            break
    for m in re.finditer(r'<img\b[^>]+>', page, re.I):
        tag = m.group(0)
        if re.search(r'logo', tag, re.I) and not re.search(_VENDOR_RE, tag, re.I):
            s = re.search(r'\bsrc="([^"]+)"', tag)
            if s:
                out.append(s.group(1))
    seen, uniq = set(), []
    for v in out:
        if v[:200] not in seen:
            seen.add(v[:200])
            uniq.append(v)
    return uniq[:6]


def logo_profile(im):
    """Colour make-up of a logo: share of white-ish / dark-neutral / coloured pixels + palette."""
    a = _small(im, 300)
    a = a[a[..., 3] >= 160]
    tot = max(len(a), 1)
    rgb = a[:, :3] / 255.0
    l, s = _arr_ls(rgb)
    white = ((l > 0.88) & (s < 0.35)) | ((l > 0.8) & (s < 0.15))
    darkn = ~white & ((s < 0.22) | (l < 0.12))
    col = ~white & ~darkn
    q = (a[:, :3] // 12 * 12).astype(int)

    def counter(mask):
        if not mask.any():
            return collections.Counter()
        u, n = np.unique(q[mask], axis=0, return_counts=True)
        return collections.Counter({tuple(int(x) for x in k): int(v) for k, v in zip(u, n)})

    return {"white": white.sum() / tot, "dark": darkn.sum() / tot, "col": col.sum() / tot,
            "pal": counter(col), "darks": counter(darkn | (col & (l < 0.3)))}


def transparent_share(im):
    a = np.asarray(im.convert("RGBA").split()[-1].resize((min(200, im.width), min(200, im.height))))
    return float((a < 40).mean())


def pick_logo(site):
    """Fetch the homepage → (page_html, main_logo, reversed_logo). Logos are PIL RGBA or None.
    main = first coloured/dark logo (reads on light); reversed = a WHITE logo on a TRANSPARENT
    background with the same shape (partner badges on opaque boxes are rejected)."""
    page = fetch(site)
    cands = []
    for src in logo_candidates(page):
        try:
            im = load_image(src, site)
        except Exception:
            continue
        w, h = im.size
        if w < 40 or h < 12 or not (0.6 <= w / h <= 12):
            continue
        cands.append((im, logo_profile(im)))
    if not cands:
        return page, None, None
    # The FIRST candidate is the header brand logo = the client's own. Other candidates only
    # count as its other version when they have the same shape (a partner logo — Lenovo, Dell —
    # never stands in for it).
    first = cands[0]
    ar = first[0].width / first[0].height

    def same_shape(c):
        return 0.85 < (c[0].width / c[0].height) / ar < 1.18

    is_white = lambda c: c[1]["white"] >= 0.6 and transparent_share(c[0]) > 0.2
    if not is_white(first):
        main = first
        rev = next((c for c in cands[1:] if is_white(c) and same_shape(c)), None)
    else:   # header logo is the white (reversed) version
        rev = first
        main = next((c for c in cands[1:] if not is_white(c) and c[1]["white"] < 0.6
                     and same_shape(c)), None) or first
    return page, main[0], (rev[0] if rev else None)


# ───────────────────────── brand colours ─────────────────────────
def _css_colours(page):
    cnt = collections.Counter()
    css = page or ""
    for href in re.findall(r'<link[^>]+href="([^"]+\.css[^"]*)"', css, re.I)[:3]:
        try:
            css += fetch(("https:" + href) if href.startswith("//") else href)
        except Exception:
            pass
    for hx in re.findall(r'#([0-9a-fA-F]{6})\b', css):
        c = tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
        h, l, s = hls(c)
        if s > 0.25 and 0.1 < l < 0.8 and hx.lower() not in COMMON_CSS:
            cnt[c] += 1
    return cnt


def brand_colours(prof, page):
    """deep = darkest saturated logo colour (dark bg / text), vivid = signature accent,
    ink = darkest colour for text on white. Colours come from the LOGO (site CSS is polluted
    by the shared template colour) — CSS only when the logo has no colour at all."""
    groups = []
    for c, n in prof["pal"].most_common(40):
        h, l, s = hls(c)
        for g in groups:
            gh, gl, gs = hls(g["c"])
            if min(abs(h - gh), 1 - abs(h - gh)) < 0.05 and abs(l - gl) < 0.25:
                g["n"] += n
                break
        else:
            groups.append({"c": c, "n": n})
    groups.sort(key=lambda g: -g["n"])
    src = "logo"
    if not groups and page:
        src = "website"
        groups = [{"c": c, "n": n} for c, n in _css_colours(page).most_common(6)]
    total = sum(g["n"] for g in groups) or 1
    real = [g for g in groups if g["n"] / total > 0.03] or groups
    deeps = [g["c"] for g in real if hls(g["c"])[1] < 0.36 and hls(g["c"])[2] >= 0.3]
    deep = min(deeps, key=lambda c: hls(c)[1]) if deeps else None

    def vivid_score(g):
        h, l, s_ = hls(g["c"])
        return s_ * (1 - min(1, abs(l - 0.5) * 1.6)) * (g["n"] / total) ** 0.35
    brights = [g for g in real if hls(g["c"])[1] >= 0.35 and hls(g["c"])[2] >= 0.45]
    if brights:
        vivid = max(brights, key=vivid_score)["c"]
    elif real:
        vivid = max(real, key=lambda g: hls(g["c"])[2])["c"]
    else:
        vivid, src = (0, 96, 228), "default"
    ink = deep
    if ink is None and prof["darks"]:
        d = prof["darks"].most_common(1)[0][0]
        if lum(d) < 0.08:
            ink = d
    return deep, vivid, ink, src


def third_colour(prof, deep, vivid):
    """A 3rd real logo colour (differs from deep & vivid), else a lighter shade of vivid."""
    total = sum(prof["pal"].values()) or 1
    for c, n in prof["pal"].most_common(30):
        if n / total < 0.03:
            break
        ok = True
        for ref in [x for x in (deep, vivid) if x]:
            h1, l1, s1 = hls(c)
            h2, l2, s2 = hls(ref)
            if min(abs(h1 - h2), 1 - abs(h1 - h2)) < 0.04 and abs(l1 - l2) < 0.15:
                ok = False
        if ok and hls(c)[2] > 0.2:
            return c
    vh, vl, vs = hls(vivid)
    return from_hls(vh, min(0.78, vl + 0.25), vs * 0.8)


def heading_font(page):
    css = page or ""
    for href in re.findall(r'<link[^>]+href="([^"]+\.css[^"]*)"', css, re.I)[:2]:
        try:
            css += fetch(("https:" + href) if href.startswith("//") else href)
        except Exception:
            pass
    cnt = collections.Counter()
    for fam in re.findall(r'font-family\s*:\s*["\']?([A-Za-z][A-Za-z ]+)', css):
        k = fam.strip().lower()
        if k in FONTS:
            cnt[k] += 1
    return FONTS[cnt.most_common(1)[0][0]] if cnt else DEFAULT_FONT


# ───────────────────────── logo recolouring ─────────────────────────
def _recolor(im, mask_fn, rgb):
    a = np.asarray(im.convert("RGBA")).copy()
    m = mask_fn(a[..., :3].astype(float) / 255.0) & (a[..., 3] > 0)
    a[m, 0], a[m, 1], a[m, 2] = rgb[0], rgb[1], rgb[2]
    return Image.fromarray(a, "RGBA")


def solid(im, rgb):
    return _recolor(im, lambda x: np.ones(x.shape[:2], bool), rgb)


def whites_to(im, rgb):
    def m(x):
        l, s = _arr_ls(x)
        return (l > 0.88) & (s < 0.35)
    return _recolor(im, m, rgb)


def low_contrast_to_white(im, bg):
    return _recolor(im, lambda x: _arr_contrast(x, bg) < 2.5, (255, 255, 255))


def logo_reads_on(im, bg):
    a = _small(im, 240)
    a = a[a[..., 3] >= 160]
    if not len(a):
        return 1.0
    return float((_arr_contrast(a[:, :3] / 255.0, bg) >= 2.2).mean())


# ───────────────────────── brand kit ─────────────────────────
def analyze_website(url):
    """→ dict(page, main, rev, font). Raises on fetch failure."""
    page, main, rev = pick_logo(url)
    return {"page": page, "main": main, "rev": rev, "font": heading_font(page)}


def split_uploaded_logo(im):
    """An uploaded logo → (main, rev). A white logo on transparency is the reversed version."""
    im = crop_alpha(im)
    prof = logo_profile(im)
    if prof["white"] >= 0.6 and transparent_share(im) > 0.2:
        return im, im
    return im, None


def detect_colours(main, page=""):
    prof = logo_profile(main) if main is not None else {
        "white": 0, "dark": 0, "col": 0, "pal": collections.Counter(), "darks": collections.Counter()}
    deep, vivid, ink, src = brand_colours(prof, page)
    return {"deep": deep, "vivid": vivid, "ink": ink, "third": third_colour(prof, deep, vivid),
            "src": src}


def make_themes(main, rev, deep, vivid, ink=None, third=None):
    """Background themes the logo can sit on → {white, tint, deep, brand}: each
    {bg, text, accent, detail, logo}. deep may be None (derived from the vivid hue)."""
    prof = logo_profile(main) if main is not None else None
    vh, vl, vs = hls(vivid)
    if deep is not None:
        dh, dl, ds = hls(deep)
        dark_bg = from_hls(dh, min(dl, 0.16), min(ds, 0.75))
    elif vh < 0.12 or vh > 0.9 or (0.15 < vh < 0.45):   # orange/red/green brands → near-black
        dark_bg = (18, 20, 26)
    else:
        dark_bg = from_hls(vh, 0.13, min(0.7, vs))
    if ink is None:
        ink = deep
    out = {}
    if main is not None:
        text = ink if ink is not None and contrast(ink, (255, 255, 255)) >= 7 else (26, 26, 30)
        lg = main
        if prof["white"] > 0.02:              # white words/parts would vanish on white → dark
            lg = whites_to(main, text)
        acc = vivid if contrast(vivid, (255, 255, 255)) > 1.8 else from_hls(vh, 0.42, vs)
        out["white"] = {"bg": (255, 255, 255), "text": text, "accent": acc, "logo": lg}
        out["tint"] = dict(out["white"], bg=from_hls(vh, 0.965, min(0.6, vs)))
    lg = rev
    if lg is not None and logo_reads_on(lg, dark_bg) < 0.9:   # site's white logo may carry
        lg = low_contrast_to_white(lg, dark_bg)                 # dark parts → make them white
    if lg is None and main is not None:
        lg = low_contrast_to_white(main, dark_bg)
        if logo_reads_on(lg, dark_bg) < 0.9:
            lg = solid(main, (255, 255, 255))
    if lg is not None:
        acc = vivid
        if contrast(acc, dark_bg) < 3:
            acc = from_hls(vh, max(vl, 0.58), max(vs, 0.6))
        out["deep"] = {"bg": dark_bg, "text": (255, 255, 255), "accent": acc, "logo": lg}
    if contrast((255, 255, 255), vivid) >= 3.0 and (main is not None or rev is not None):
        acc = deep if deep and contrast(deep, vivid) >= 2 else (255, 255, 255)
        out["brand"] = {"bg": vivid, "text": (255, 255, 255), "accent": acc,
                        "logo": solid(main if main is not None else rev, (255, 255, 255))}
    third = third or from_hls(vh, min(0.78, vl + 0.25), vs * 0.8)
    for k, t in out.items():  # detail = 3rd colour when it shows on this bg, else the accent
        d = third
        if contrast(d, t["bg"]) < 1.5 or (k == "brand" and contrast(d, t["bg"]) < 2):
            d = t["accent"]
        t["detail"] = d
    return {k: out[k] for k in BG_ORDER if k in out}


# ───────────────────────── random specs ─────────────────────────
def random_spec(rng, shape, bg):
    if shape == "band":
        det = "none"
    elif shape == "card":
        det = rng.choice(["none", "dots", "underline", "corner"])
    else:
        det = rng.choice(["none", "dots", "underline"])
    return {"shape": shape, "bg": bg, "detail": det,
            "pw": rng.choice([0.38, 0.40, 0.42, 0.44, 0.46]),
            "title_y": rng.choice([0.38, 0.42, 0.46]),
            "slant": rng.choice([0.08, 0.11, 0.14]),
            "curve_r": rng.choice([1.3, 1.6, 2.2]),
            "card_style": rng.choice(["frame", "block"]),
            "band_h": rng.choice([0.24, 0.28]),
            "accent_w": rng.choice([0.006, 0.010, 0.016])}


def make_batch(available_bgs, prev=None, n=6, rng=None):
    """n designs with n distinct photo shapes and mixed backgrounds; avoids the previous
    batch's (shape, background) combos so every click looks new."""
    rng = rng or random.Random()
    prev_combo = {(s["shape"], s["bg"]) for s in (prev or [])}
    specs = []
    for _ in range(300):
        shapes = rng.sample(SHAPES, min(n, len(SHAPES)))
        pool = list(available_bgs)
        bgs = [pool[i % len(pool)] for i in range(len(shapes))]
        rng.shuffle(bgs)
        specs = [random_spec(rng, s, b) for s, b in zip(shapes, bgs)]
        if len({(s["shape"], s["bg"]) for s in specs} & prev_combo) <= 1:
            break
    return specs


def spec_label(spec):
    det = "" if spec.get("detail", "none") == "none" else f" · {spec['detail']}"
    return f"{SHAPE_LABELS.get(spec['shape'], spec['shape'])} · {BG_LABELS.get(spec['bg'], spec['bg'])} background{det}"


# ───────────────────────── renderer ─────────────────────────
def _mask(W, H, draw_fn):
    m = Image.new("L", (W * SS, H * SS), 0)
    draw_fn(ImageDraw.Draw(m), SS)
    return m.resize((W, H), Image.LANCZOS)


def _place_logo(c, lg, x, y, max_w, max_h, anchor="lt"):
    s = min(max_w / lg.width, max_h / lg.height)
    l = lg.resize((max(1, int(lg.width * s)), max(1, int(lg.height * s))), Image.LANCZOS)
    if anchor == "rm":
        x, y = x - l.width, y - l.height // 2
    c.alpha_composite(l, (int(x), int(y)))
    return l.size


def render_overlay(spec, th, W, H):
    """→ (overlay RGBA W×H, title coords). The overlay is opaque brand surface + shapes + logo;
    alpha 0 where the blog photo shows. The title is drawn later by composite_template()."""
    bg, text, acc, det, lg = th["bg"], th["text"], th["accent"], th["detail"], th["logo"]
    c = Image.new("RGBA", (W, H), tuple(bg) + (255,))
    thumb = W / H < 1.7
    inset = int(0.05 * W)
    pw = spec["pw"] + (0.06 if thumb else 0)
    logo_w, logo_h = (0.38 if thumb else 0.27) * W, (0.16 if thumb else 0.12) * H
    fsz = int((0.062 if thumb else 0.052) * H)
    text_w = (pw - 0.09) * W
    aw = max(3, spec["accent_w"] * W)
    photo_box = None

    def shape(fill, fn):  # paste blends alpha too → opaque again where a shape is drawn
        c.paste(Image.new("RGBA", (W, H), tuple(fill) + (255,)), (0, 0), _mask(W, H, fn))

    def punch(fn):        # photo window: alpha → 0
        m = np.asarray(_mask(W, H, fn)).astype(float) / 255.0
        a = np.asarray(c.split()[-1]).astype(float)
        c.putalpha(Image.fromarray((a * (1 - m)).round().astype("uint8"), "L"))

    def circ(d, k, x, y, r, **kw):
        d.ellipse(((x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k), **kw)

    s = spec["shape"]
    if s == "straight":
        x = pw * W
        punch(lambda d, k: d.rectangle((x * k, 0, W * k, H * k), fill=255))
        shape(acc, lambda d, k: d.rectangle(((x - aw) * k, 0, x * k, H * k), fill=255))
        photo_box = (x, 0, W, H)
    elif s == "slant":
        x, sl = pw * W, spec["slant"] * W
        punch(lambda d, k: d.polygon([((x + sl / 2) * k, 0), (W * k, 0), (W * k, H * k),
                                      ((x - sl / 2) * k, H * k)], fill=255))
        st = aw * 1.8
        shape(acc, lambda d, k: d.polygon([((x + sl / 2) * k, 0), ((x + sl / 2 + st) * k, 0),
                                           ((x - sl / 2 + st) * k, H * k), ((x - sl / 2) * k, H * k)], fill=255))
        photo_box = (x, 0, W, H)
    elif s == "curve":
        x = pw * W
        R = H * spec["curve_r"]
        cx = x + R
        g = aw * 1.6
        shape(acc, lambda d, k: circ(d, k, cx, H / 2, R + g, outline=255, width=int(aw * k)))
        punch(lambda d, k: circ(d, k, cx, H / 2, R, fill=255))
        photo_box = (x, 0, W, H)
    elif s in ("ring", "disc"):
        r = (0.40 if thumb else 0.41) * H
        cx, cy = W - inset - r - 0.03 * W, H / 2
        if s == "ring":
            g = 0.035 * H
            lw = max(4, 0.016 * H)
            shape(acc, lambda d, k: circ(d, k, cx, cy, r + g + lw / 2, outline=255, width=int(lw * k)))
        else:
            o = 0.06 * H
            shape(acc, lambda d, k: circ(d, k, cx - o, cy + o, r, fill=255))
        punch(lambda d, k: circ(d, k, cx, cy, r, fill=255))
        lim = cx - r - 0.06 * H - inset - 0.03 * W
        text_w, logo_w = min(text_w, lim), min(logo_w, lim)
        photo_box = (cx - r, cy - r, cx + r, cy + r)
    elif s == "card":
        x0, y0, x1, y1 = (pw + 0.04) * W, 0.10 * H, W - inset - 0.02 * W, 0.86 * H
        o = 0.028 * W
        rad = 0.025 * W
        if spec["card_style"] == "frame":
            shape(acc, lambda d, k: d.rounded_rectangle(((x0 + o) * k, (y0 + o) * k, (x1 + o) * k, (y1 + o) * k),
                                                        int(rad * k), outline=255,
                                                        width=int(max(3, 0.006 * W) * k)))
        else:
            shape(acc, lambda d, k: d.rounded_rectangle(((x0 + o) * k, (y0 + o) * k, (x1 + o) * k, (y1 + o) * k),
                                                        int(rad * k), fill=255))
        punch(lambda d, k: d.rounded_rectangle((x0 * k, y0 * k, x1 * k, y1 * k), int(rad * k), fill=255))
        photo_box = (x0, y0, x1, y1)
    elif s == "band":
        bh = (spec["band_h"] + (0.04 if thumb else 0)) * H
        punch(lambda d, k: d.rectangle((0, 0, W * k, (H - bh) * k), fill=255))
        shape(acc, lambda d, k: d.rectangle((0, (H - bh) * k, W * k, (H - bh + max(4, 0.012 * H)) * k), fill=255))
        mid = H - bh / 2 + 0.006 * H
        lw_, _ = _place_logo(c, lg, W - inset, mid, (0.30 if thumb else 0.24) * W, 0.5 * bh, anchor="rm")
        tsz = int(fsz * (0.95 if thumb else 0.82))
        return c, {"tx": inset, "ty": int(mid - tsz * 1.25), "tw": int(W - 3 * inset - lw_),
                   "fsz": tsz, "lh": round(tsz * 1.25), "color": list(text),
                   "max_lines": 2, "vcenter": int(mid)}

    dk = spec["detail"]
    if thumb and dk == "dots":
        dk = "none"                      # too busy at thumbnail size; collides with long titles
    dots_top = None
    if dk == "dots" and photo_box:
        x0, y0, x1, y1 = photo_box
        rr = 0.018 * H
        if s == "slant":
            x0 -= spec["slant"] * W / 2          # the slanted photo edge reaches furthest left at the bottom
        bx, by = (x0 - 0.03 * W - rr, y1 - 0.12 * H) if s not in ("ring", "disc") else (x0 - 0.01 * W, y1 - 0.06 * H)
        for i in range(3):
            shape(det, lambda d, k, i=i: circ(d, k, bx - i * 3.2 * rr, by, rr, fill=255))
        dots_top = by - rr - 0.03 * H
    elif dk == "corner" and photo_box and s == "card":
        x0, y0, x1, y1 = photo_box
        L, t = 0.10 * H, max(3, 0.012 * H)
        ex, ey = x1 - inset * 0.6, y1 - inset * 0.6
        shape(det, lambda d, k: (d.rectangle(((ex - L) * k, (ey - t) * k, ex * k, ey * k), fill=255),
                                 d.rectangle(((ex - t) * k, (ey - L) * k, ex * k, ey * k), fill=255)))
    _, lh = _place_logo(c, lg, inset, 0.14 * H, logo_w, logo_h)
    ty = max(spec["title_y"] * H, 0.14 * H + lh + 0.08 * H)
    if dk == "underline":
        uy = 0.14 * H + lh + 0.045 * H
        shape(det, lambda d, k: d.rectangle((inset * k, uy * k, (inset + 0.06 * W) * k,
                                             (uy + max(3, 0.008 * H)) * k), fill=255))
        ty = max(ty, uy + 0.05 * H)
    lh_t = round(fsz * 1.25)
    bottom = 0.92 * H if dots_top is None else min(0.92 * H, dots_top)
    max_lines = max(3, int((bottom - ty) // lh_t))
    return c, {"tx": inset, "ty": int(ty), "tw": int(text_w), "fsz": fsz, "lh": lh_t,
               "color": list(text), "max_lines": max_lines, "vcenter": None}
