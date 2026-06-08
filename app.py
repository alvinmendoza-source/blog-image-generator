import os
import re
import time
import random
import base64
import io
import json
import hashlib
import requests
import urllib.parse
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import streamlit as st
from PIL import Image as PILImage, ImageDraw, ImageFont

load_dotenv(Path(__file__).parent / ".env", override=True)

# Streamlit Cloud: bridge secrets → os.environ so os.getenv() calls work unchanged
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str) and _k not in os.environ:
            os.environ[_k] = _v
except Exception:
    pass

GOOGLE_API_KEY  = os.getenv("GOOGLE_API_KEY", "").strip()
KIE_API_KEY     = os.getenv("KIE_API_KEY", "").strip()
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "").strip()
IMAGEN_API_KEY  = os.getenv("IMAGEN_API_KEY", "").strip()
KIE_BASE        = "https://api.kie.ai/api/v1"
FREE_MODE       = not GOOGLE_API_KEY or not KIE_API_KEY

FIGMA_TOKEN    = os.getenv("FIGMA_ACCESS_TOKEN", os.getenv("FIGMA_API_TOKEN", "")).strip()
FIGMA_FILE_KEY = "qMMRF3RX3tkUt64hWA5KHT"
ASSETS_DIR      = Path("template_assets")
ASSETS_DIR.mkdir(exist_ok=True)          # ensure folder always exists
INTER_FONT_PATH = ASSETS_DIR / "Inter.ttf"
MAIN_TPL  = {"w": 920, "h": 613, "logo": ASSETS_DIR / "logo_panel_main.png",  "tx": 34, "ty": 307, "tw": 290, "fsz": 24}
THUMB_TPL = {"w": 736, "h": 560, "logo": ASSETS_DIR / "logo_panel_thumb.png", "tx": 33, "ty": 280, "tw": 227, "fsz": 19}

SYSTEM_PROMPT_TEMPLATE = """You write scene descriptions for a documentary workplace photo generator. The generator creates images indistinguishable from real DSLR office photography.

⚠️ CRITICAL FORMAT RULE: Do NOT start your descriptions with "Documentary-style", "candid workplace photography", or any camera/photography terminology. That prefix and all camera/lighting details are added automatically by the generator. Write ONLY the scene content: who + what they are doing + where exactly.

HIGHEST PRIORITY — REALISTIC HUMANS (NON-NEGOTIABLE):
- ALL people must be white American or British Caucasian. No South Asian, East Asian, Indian, Hispanic, or Middle Eastern features. Ever.
- Age 30–50, average everyday build. Real faces — subtle asymmetry, visible pores, natural wrinkles, slight under-eye detail. NOT model-perfect.
- Candid body language: eyes on monitor/desk/colleague — NEVER at the camera
- Natural hands: resting on desk, around a mug, or out of frame — never posed or highlighted
- Believable posture: slight slouch, shifted weight, imperfect — not perfectly upright

PHOTOGRAPHY STYLE:
- Documentary, photojournalistic, editorial corporate — unscripted, naturally captured
- NEVER staged, posed, stock-photo composed, or symmetrically framed
- Feel: a real photographer quietly walked into the office and shot this

ENVIRONMENT (lived-in, authentic):
- Cables on desks, coffee cups, water bottles, a jacket on a chair back, a phone face-down
- Monitors show dark dashboards or terminal screens — no readable text
- Plain business casual clothing: navy polo, grey fleece, chinos, plain t-shirt — NO logos, brand names, or company names on any clothing
- SERVER ROOMS / NETWORK CLOSETS: cables must be mostly organized — bundled with velcro ties, routed through cable managers, with only a few natural loose loops or slight slack. NOT a tangled chaotic mess, NOT a showroom-perfect installation. Think: a real IT team maintains this room regularly.

BANNED WORDS — never write these:
cinematic, dramatic, golden, moody, stunning, glowing, vibrant, misty, hazy, vintage, bokeh,
blurry, soft light, rim light, perfect, flawless, symmetrical, posed, stock photo,
whiteboard with text, readable signs, hologram, neon, glowing code, padlock, sci-fi

REQUIRED ENVIRONMENTS — already pre-selected for variety (use in order, one per image):
{required_envs}

⚠️ Do NOT choose or swap environments — they are already assigned.
Your ONLY job is to write what the people are DOING in each environment based on the blog topic.

CONTENT RELEVANCE RULE (most important):
- Each image must show people doing something DIRECTLY related to the blog topic.
- If the blog is about cybersecurity: show someone reviewing security alerts, a tech checking firewall logs, a team discussing a security incident, etc.
- If the blog is about cloud backup: show someone configuring cloud software, a tech managing storage, two people reviewing backup reports, etc.
- Do NOT write generic "working at a computer" scenes — the activity must reflect the blog subject.
- Each image must show a DIFFERENT activity — not just the same task in a different room.
- Be creative connecting the topic to the assigned environment — even an "unusual" pairing (e.g. cloud backup blog + walking corridor) is fine: show someone discussing a backup plan mid-walk, reading an alert on a phone, etc.

FORMAT — STRICT:
- Output ONLY a bulleted list of exactly {count} descriptions. Nothing else. No headers, no environment labels.
- Each description = 1–2 plain sentences: who + what specific task (related to blog topic) + which environment + one lived-in detail.
- Write like a photo caption. Plain and mundane. No adjectives like stunning, perfect, dramatic."""

# ── Scene type pool — Gemini selects the most topic-relevant ones ─────────────
SCENE_TYPES = [
    ("SOLO WORKSTATION",
     "one person alone at a single desk with a laptop or monitor"),
    ("SIDE-BY-SIDE PAIR",
     "two people seated side by side reviewing something on a shared screen"),
    ("MEETING TABLE",
     "two to four people seated around a meeting room table in discussion"),
    ("STANDING DESK",
     "one person standing at a height-adjustable desk looking at their screen"),
    ("HELP DESK COUNTER",
     "one person at a help desk counter with headset and two monitors"),
    ("OPEN PLAN WIDE",
     "wide shot of three or four people at separate desks across an open-plan office floor"),
    ("INFORMAL HUDDLE",
     "two people standing and talking near a kitchen counter or office hallway"),
    ("NETWORK CLOSET",
     "one person crouching or standing in a small network/cable closet — patch panel cables are routed with velcro ties and cable managers, mostly tidy with a few natural loops or slack"),
    ("WALKING CORRIDOR",
     "one or two people walking mid-stride through a bright office corridor"),
    ("DUAL MONITOR DESK",
     "one person at a corner desk with two large monitors"),
    ("PHONE CALL AT DESK",
     "one person at a desk with phone to one ear, notepad in front"),
    ("WHITEBOARD SESSION",
     "one or two people standing at a whiteboard with markers, no readable text on board"),
    ("RECEPTION AREA",
     "one person at a front reception desk in a modern office lobby"),
    ("COFFEE BREAK CHAT",
     "two people having a casual standing conversation near a coffee machine in a break room"),
]

def _scene_pool_text() -> str:
    """Return all scene types as a bulleted list (shuffled) for Gemini to choose from."""
    return "\n".join(f"  • {name}: {desc}" for name, desc in random.sample(SCENE_TYPES, len(SCENE_TYPES)))


def _pick_required_scenes(count: int) -> list:
    """Randomly pre-select `count` scenes so variety is guaranteed across generations."""
    return random.sample(SCENE_TYPES, min(count, len(SCENE_TYPES)))


def _format_required_envs(scenes: list) -> str:
    return "\n".join(f"  {i+1}. {name}: {desc}" for i, (name, desc) in enumerate(scenes))


# ── Pollinations (used in free mode) ──────────────────────────────────────────
_QUALITY_BLOCK = (
    # photography style — strongest Flux signals first
    ", documentary photography, candid unscripted moment, "
    "photojournalistic editorial corporate photography, "
    # camera spec — tells Flux to simulate real glass/sensor behavior
    "Sony A7 IV 50mm lens f/1.8 ISO 800, "
    # color accuracy — critical to prevent filtered/graded look
    "true natural colors, accurate white balance, no color filter, no color grading, "
    "clean neutral light, well-exposed, natural color palette, "
    # lighting
    "soft natural window light, uneven ambient office lighting, "
    "realistic shadows, natural surface reflections, "
    # sensor / optics realism
    "slight sensor grain, shallow depth of field, natural bokeh, realistic focus falloff, "
    # human realism
    "subtle facial asymmetry, natural skin texture, visible pores, "
    "believable posture, candid body language, "
    # environment
    "authentic lived-in office, slightly messy desk, "
    # cable management — applies to all scenes especially server/network rooms
    "network cables neatly bundled and routed, tidy cable management, organized server cabling, "
    # ethnicity anchor
    "white American Caucasian office workers"
    # NOTE: NO "avoid X" here — those belong in NEGATIVE_PROMPT only.
    # Flux reads "avoid plastic skin" as a POSITIVE token for plastic skin.
)
QUALITY_SUFFIX = _QUALITY_BLOCK
NEGATIVE_PROMPT = (
    # quality / sharpness
    "blurry, out of focus, soft focus, unfocused, low resolution, low quality, pixelated, jpeg artifacts, "
    # CGI / AI rendered look
    "CGI, CGI humans, CGI rendering, 3d render, digital art, computer-generated, "
    "illustration, cartoon, anime, painting, drawing, render, 3d, "
    "AI-generated appearance, uncanny valley, uncanny valley face, artificial human, "
    "fake corporate stock photo look, "
    # skin / face realism failures
    "plastic skin, doll-like skin, airbrushed skin, overly smooth skin, hyper-detailed CGI texture, "
    "perfect symmetry, perfectly symmetrical face, hyper symmetry, "
    "perfect model face, perfect skin, overly perfect face, "
    "over-sharpened eyes, over-sharpened face, overprocessed details, "
    "unrealistic teeth, perfect teeth, porcelain teeth, "
    "uncanny smile, frozen expression, emotionless expression, robotic posture, "
    # anatomy
    "deformed hands, extra fingers, missing fingers, fused fingers, melted hands, bad anatomy, "
    "distorted face, crossed eyes, blurry eyes, doll eyes, glassy eyes, extra limbs, unrealistic anatomy, "
    # ethnicity
    "South Asian, Indian, East Asian, Asian features, Middle Eastern features, Hispanic features, "
    # lighting / cinematic / filter looks
    "cinematic lighting, dramatic lighting, moody lighting, teal and orange, color graded, "
    "HDR, golden hour, blue hour, rim lighting, backlit silhouette, spotlight, "
    "artificial lighting, fake shadows, overdramatic cinematic glow, excessive rim lighting, "
    "perfect studio lighting, studio lighting, studio white background, dark studio, "
    "desaturated, muted colors, washed out, faded colors, vintage filter, film filter, "
    "color filter, warm color filter, cool color filter, color toning, color cast, "
    "warm glow, warm tones, orange tones, teal tones, blue tones, color shift, "
    "heavy grain, film grain, dark room, underexposed, dark and moody, "
    # environment / atmosphere
    "sterile office, perfectly clean office, empty office, unrealistic minimalism, "
    "perfectly aligned objects, fake futuristic environment, "
    "sci-fi, hologram, glowing code, binary code, neon, holograms, floating text, padlock, hacker, "
    # server / networking equipment — banned to prevent constant data-center look
    "server rack, server racks, server room, data center, network rack, patch panel, "
    "rack mount, rack unit, networking equipment, router rack, switch rack, "
    "server farm, computer room, server closet, network closet background, "
    "colorful cables on rack, bundled patch cables, ethernet patch panel, "
    # composition / staging
    "centered AI composition, perfectly symmetrical framing, forced cinematic angle, "
    "overly dramatic pose, stock photo pose, staged pose, "
    "stock photo, getty images, shutterstock, posed, staged, commercial photography, "
    "professional photography pose, artistic photography, award winning, "
    # misc
    "watermark, text overlay, logo, oversaturated, oversharpened, extreme HDR, fake depth, "
    # clothing — no branded/company items
    "company logo on shirt, branded clothing, logo on polo, logo on uniform, company uniform, "
    "branded polo shirt, embroidered logo, company name on clothing, uniform with logo, name tag, "
    # cable mess — banned in all scenes
    "messy cables, tangled cables, spaghetti wiring, chaotic wiring, cable chaos, "
    "tangled wires, rats nest cables, disorganized cabling, cable clutter, "
    # food and drinks — unprofessional
    "water bottle, coffee cup, coffee mug, tea cup, soda can, food, snacks, lunch, "
    "takeout, fast food, meal, drink, beverage, bottle on desk, cup on desk"
)

# ── Kie paid mode (Google Imagen 4 Ultra → Flux 2 Pro fallback) ───────────────
# Shorter, focused suffix works better for Imagen 4 Ultra — it doesn't need verbose guidance
KIE_QUALITY_SUFFIX = (
    ", documentary workplace photography, candid unscripted office moment, "
    "photojournalistic editorial style, natural office lighting, "
    "Sony A7 IV, no color grading, no filter, no CGI, "
    "plain unbranded clothing with no logos or company names, "
    "white Caucasian American office workers, "
    "network cables neatly bundled and routed, tidy cable management, "
    "organized server room cabling, cables secured with velcro ties, "
    "no food on desk, no drinks on desk, no water bottle, no coffee cup, no snacks, clean professional workspace"
)
KIE_NEGATIVE_PROMPT = NEGATIVE_PROMPT + (
    # text in scene
    "whiteboard with writing, whiteboard text, chalkboard text, "
    "sticky notes on wall, writing on board, presentation screen with text, readable signs, "
    "text on screen, visible words on displays, "
    # cable mess
    "messy cables, tangled cables, spaghetti wiring, chaotic wiring, cable chaos, "
    "tangled wires, rats nest cables, disorganized cabling, "
)


SKIP_SRC_KEYWORDS = ["author", "profile", "avatar", "logo", "icon", "signature", "headshot"]
SKIP_ALT_KEYWORDS = ["author", "profile", "avatar", "logo", "icon", "portrait", "headshot", "photo of"]
CONTENT_SELECTORS = [".w-richtext", "article", ".blog-content", ".post-body", ".post-content", ".entry-content"]
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1500, 1000
MIME_MAP = {"avif": "image/avif", "webp": "image/webp", "jpg": "image/jpeg"}
MAX_ATTEMPTS = 3


def _pollinations_text(response_json: dict) -> str:
    """Safely extract text from Pollinations response regardless of model type."""
    msg = (response_json.get("choices") or [{}])[0].get("message", {})
    return msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning") or ""


# ── Image helpers ──────────────────────────────────────────────────────────────

def download_image_bytes(url: str) -> bytes:
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    return r.content


def get_image_info(img_bytes: bytes, url: str = "") -> dict:
    try:
        img = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        fmt = (img.format or url.split(".")[-1]).upper()
    except Exception:
        w, h, fmt = 0, 0, url.split(".")[-1].upper() if "." in url else "?"
    return {"width": w, "height": h, "format": fmt, "size_kb": round(len(img_bytes) / 1024, 1)}


def optimize_image(img_bytes: bytes, max_kb: int = 200) -> tuple[bytes, str]:
    max_bytes = max_kb * 1024
    try:
        img = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return img_bytes, "jpg"
    for fmt, ext in [("AVIF", "avif"), ("WEBP", "webp"), ("JPEG", "jpg")]:
        try:
            lo, hi, best = 20, 90, None
            while lo <= hi:
                mid = (lo + hi) // 2
                buf = io.BytesIO()
                kw = {"format": fmt, "quality": mid}
                if fmt == "WEBP": kw["method"] = 6
                if fmt == "JPEG": kw["optimize"] = True
                img.save(buf, **kw)
                if buf.tell() <= max_bytes:
                    best = buf.getvalue()
                    lo = mid + 1
                else:
                    hi = mid - 1
            if best is None:
                buf = io.BytesIO()
                kw = {"format": fmt, "quality": 20}
                if fmt == "WEBP": kw["method"] = 6
                if fmt == "JPEG": kw["optimize"] = True
                img.save(buf, **kw)
                best = buf.getvalue()
            return best, ext
        except Exception:
            continue
    return img_bytes, "jpg"


def analyze_image_quality(img_bytes: bytes) -> dict:
    try:
        img = PILImage.open(io.BytesIO(img_bytes))
        fmt = img.format or "JPEG"
        if fmt.upper() in ("AVIF", "WEBP"):
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=85)
            img_bytes = buf.getvalue()
    except Exception:
        pass
    b64 = base64.b64encode(img_bytes).decode()
    payload = {
        "model": "openai",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": (
                "Analyze this image for quality issues as a blog image for an IT/MSP company. "
                "Check for: AI-generated artifacts, distorted faces, wrong finger count, unnatural lighting, "
                "sci-fi/cliche elements, or anything fake/unprofessional.\n\n"
                "Reply ONLY in this exact JSON format:\n"
                '{"quality": "good|fair|poor", "is_ai_generated": true|false, "issues": ["issue1"]}\n'
                "Keep issues as [] if image looks fine."
            )},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        ]}],
        "private": True
    }
    try:
        r = requests.post("https://text.pollinations.ai/openai", json=payload, timeout=60)
        r.raise_for_status()
        content = _pollinations_text(r.json())
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {"quality": "unknown", "is_ai_generated": None, "issues": []}


_ANATOMY_CHECK_PROMPT = (
    "You are a strict quality control inspector for AI-generated images. "
    "Examine this image carefully and check every item below.\n\n"

    "STEP 1 — COUNT HANDS:\n"
    "Look carefully at every person in the image. Count every human hand visible, even partial ones at edges.\n"
    "A normal human has exactly 2 hands maximum. Flag immediately if ANY single person appears to have 3 or more hands.\n"
    "Also flag if the total hand count across all people seems impossible (e.g. 2 people but 5+ hands visible).\n"
    "Pay special attention to areas near whiteboards, desks, and keyboards where extra hand artifacts commonly appear.\n\n"

    "STEP 2 — COUNT FINGERS:\n"
    "For each clearly visible hand, count the fingers. A normal hand has exactly 5 fingers.\n"
    "Flag if any hand has 6+ fingers, 3 or fewer fingers, fused fingers, or extra stumps.\n\n"

    "STEP 3 — ARMS & LIMBS:\n"
    "Count arms per person. Each person must have exactly 2. Flag any extra arms, missing arms, or limbs growing from wrong places.\n\n"

    "STEP 4 — BODY STRUCTURE:\n"
    "Flag if any person has: a body that does not make anatomical sense, a torso that merges into furniture, "
    "a neck at an impossible angle, or a face that is severely distorted.\n\n"

    "STEP 5 — PHYSICAL REALITY:\n"
    "Flag if any object is floating with no visible surface support (laptop in air, keyboard hovering, "
    "monitor with no desk beneath it, person with no chair or floor contact).\n\n"

    "DECISION RULE:\n"
    "If ANY of steps 1–5 flagged a problem → has_defect: true.\n"
    "Only return has_defect: false if ALL five steps passed with zero flags.\n"
    "When uncertain whether something is a defect, default to has_defect: true.\n\n"

    "Reply ONLY in this exact JSON format (no extra text, no explanation):\n"
    '{"has_defect": true|false, "reason": "one short sentence describing the worst defect, or empty string if none"}'
)


def check_anatomy(img_bytes: bytes) -> tuple[bool, str]:
    """Vision AI check for anatomy and physical defects. Returns (is_ok, reason)."""
    try:
        buf = io.BytesIO()
        PILImage.open(io.BytesIO(img_bytes)).convert("RGB").save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return True, ""

    # Use Gemini in live mode — more reliable vision for anatomy counting
    if not FREE_MODE and GOOGLE_API_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GOOGLE_API_KEY)
            model = genai.GenerativeModel("gemini-2.5-flash")
            img_part = {"mime_type": "image/jpeg", "data": b64}
            resp = model.generate_content([_ANATOMY_CHECK_PROMPT, img_part])
            content = resp.text.strip()
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                data = json.loads(match.group())
                if data.get("has_defect"):
                    return False, data.get("reason", "defect detected")
                return True, ""
        except Exception:
            pass  # fall through to Pollinations

    # Free mode fallback — Pollinations vision
    payload = {
        "model": "openai",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _ANATOMY_CHECK_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        ]}],
        "private": True
    }
    try:
        r = requests.post("https://text.pollinations.ai/openai", json=payload, timeout=60)
        r.raise_for_status()
        content = _pollinations_text(r.json())
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            data = json.loads(match.group())
            if data.get("has_defect"):
                return False, data.get("reason", "defect detected")
    except Exception:
        pass
    return True, ""


def get_target_dimensions(image_infos: list) -> tuple:
    from collections import Counter
    sizes = [(i["width"], i["height"]) for i in image_infos if i["width"] > 0]
    if not sizes:
        return DEFAULT_WIDTH, DEFAULT_HEIGHT
    w, h = Counter(sizes).most_common(1)[0][0]
    return min(max(w, 512), 2048), min(max(h, 512), 2048)


# ── Blog scraping ──────────────────────────────────────────────────────────────

def get_img_src(img_tag) -> str:
    """Get the best available URL from an img tag: src → data-src → srcset → data-srcset."""
    for attr in ("src", "data-src"):
        val = (img_tag.get(attr) or "").strip()
        if val and not val.startswith("data:") and not val.endswith(".svg"):
            return val
    for attr in ("srcset", "data-srcset"):
        srcset = (img_tag.get(attr) or "").strip()
        if srcset:
            # "url1 100w, url2 200w" — take the last entry (largest resolution)
            candidates = [p.strip().split()[0] for p in srcset.split(",") if p.strip()]
            if candidates:
                return candidates[-1]
    return ""


def is_content_image(img_tag) -> bool:
    src = get_img_src(img_tag)
    if not src:
        return False
    alt = (img_tag.get("alt", "") or "").lower()
    if any(kw in src.lower() for kw in SKIP_SRC_KEYWORDS): return False
    if any(kw in alt for kw in SKIP_ALT_KEYWORDS): return False
    try:
        if 0 < int(img_tag.get("width", 0)) < 80: return False
    except (ValueError, TypeError):
        pass
    return True


def fetch_blog(url: str):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")
    for tag in soup(["nav", "footer", "script", "style"]):
        tag.decompose()
    # Note: <header> intentionally excluded — Webflow wraps blog content inside <header> elements
    # Try h1 first, then og:title meta tag, then h2, then page <title> (strip site name after " | " or " - ")
    _h1 = soup.find("h1")
    _og = soup.find("meta", property="og:title")
    _h2 = soup.find("h2")
    _pt = soup.find("title")
    if _h1 and _h1.get_text(strip=True):
        title = _h1.get_text(strip=True)
    elif _og and _og.get("content", "").strip():
        title = _og["content"].strip()
    elif _h2 and _h2.get_text(strip=True):
        title = _h2.get_text(strip=True)
    elif _pt:
        title = re.split(r"\s*[|\-–]\s*", _pt.get_text(strip=True))[0].strip()
    else:
        title = ""
    content_el = next((soup.select_one(s) for s in CONTENT_SELECTORS if soup.select_one(s)), None)
    content = content_el.get_text("\n", strip=True) if content_el else \
        "\n".join(p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True))
    area = content_el or soup
    image_urls = [get_img_src(img) for img in area.find_all("img") if is_content_image(img)]
    return title, content, image_urls


def fetch_blog_from_cms(slug: str, wf, collection_id: str, item_id: str):
    """Fetch blog content from Webflow CMS API — used when page is draft/unpublished."""
    item = wf._get(f"/collections/{collection_id}/items/{item_id}")
    field_data = item.get("fieldData", {})

    title = (field_data.get("name") or field_data.get("title") or slug)

    content = ""
    image_urls = []

    # Rich text field with images → use as main content
    for val in field_data.values():
        if isinstance(val, str) and "<img" in val:
            soup = BeautifulSoup(val, "html.parser")
            content = soup.get_text("\n", strip=True)
            image_urls = [img.get("src") or img.get("data-src", "")
                          for img in soup.find_all("img") if is_content_image(img)]
            break

    # Fallback: longest plain-text field
    if not content:
        candidates = [(k, v) for k, v in field_data.items()
                      if isinstance(v, str) and k not in ("slug", "name", "title")]
        if candidates:
            content = max(candidates, key=lambda x: len(x[1]))[1]

    return title, content, image_urls


# ── Prompt generation ──────────────────────────────────────────────────────────

def parse_bullet_list(raw: str, count: int) -> list:
    prompts, current = [], []

    def flush():
        if current:
            prompts.append(" ".join(current))
            current.clear()

    for line in raw.split("\n"):
        s = line.strip()
        is_b = s.startswith(("•", "-", "*", "·", "–"))
        is_n = bool(re.match(r"^\d+[.)]\s", s))
        if is_b or is_n:
            flush()
            cleaned = s.lstrip("•-*·– ") if is_b else re.sub(r"^\d+[.)]\s+", "", s)
            if cleaned.strip(): current.append(cleaned.strip())
        elif s and current:
            current.append(s)
    flush()
    if len(prompts) < count:
        prompts = [p.strip() for p in raw.split("\n\n") if p.strip()]
    return prompts[:count]


def generate_prompts_free(title: str, content: str, count: int) -> list:
    required_envs = _format_required_envs(_pick_required_scenes(count))
    system = SYSTEM_PROMPT_TEMPLATE.format(count=count, required_envs=required_envs)
    trimmed = content[:4000] + ("..." if len(content) > 4000 else "")
    payload = {"model": "openai", "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Blog Title:\n{title}\n\nWhole Blog:\n{trimmed}"}
    ], "private": True}
    for attempt in range(3):
        try:
            r = requests.post("https://text.pollinations.ai/openai", json=payload, timeout=120)
            r.raise_for_status()
            raw = _pollinations_text(r.json())
            if not raw:
                raise ValueError("Empty Pollinations response")
            prompts = parse_bullet_list(raw.strip(), count)
            # ── Debug: show what text AI generated so user can verify quality ──
            with st.expander("🔍 Debug — Scene descriptions sent to image generator", expanded=False):
                for idx, p in enumerate(prompts, 1):
                    st.markdown(f"**Scene {idx}:** {p}")
                    final = "Documentary-style candid workplace photography of " + p + _QUALITY_BLOCK
                    st.caption(f"Final Flux prompt ({len(final)} chars): {final[:300]}{'…' if len(final)>300 else ''}")
            return prompts
        except Exception as e:
            if attempt < 2: time.sleep(5)
            else: raise


def generate_prompts_live(title: str, content: str, count: int) -> list:
    try:
        from google import genai
        from google.genai import types as gt
        client = genai.Client(api_key=GOOGLE_API_KEY)
        required_envs = _format_required_envs(_pick_required_scenes(count))
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Blog Title:\n{title}\n\nWhole Blog:\n{content}",
            config=gt.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT_TEMPLATE.format(count=count, required_envs=required_envs),
                max_output_tokens=3000,
                temperature=1.5,
            )
        )
        prompts = parse_bullet_list(resp.text.strip(), count)
        # ── Debug: show what Gemini generated ──
        with st.expander("🔍 Debug — Scene descriptions sent to image generator", expanded=False):
            for idx, p in enumerate(prompts, 1):
                st.markdown(f"**Scene {idx}:** {p}")
                final = "Documentary-style candid workplace photography of " + p + _QUALITY_BLOCK
                st.caption(f"Final Flux prompt ({len(final)} chars): {final[:300]}{'…' if len(final)>300 else ''}")
        return prompts
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            st.warning("⚠️ Gemini quota exhausted — falling back to Pollinations for descriptions.")
            return generate_prompts_free(title, content, count)
        raise


# ── Image slot planner (photos + infographics in one Gemini call) ──────────────

_SLOT_PLAN_SYSTEM = """\
You plan image slots for a blog post. Each slot gets either a documentary office PHOTO or a Python-rendered INFOGRAPHIC.

OUTPUT RULE: Return ONLY a valid JSON object. No markdown, no extra text.

FORMAT:
{
  "slots": [
    {"slot": 1, "type": "photo", "description": "..."},
    {"slot": 2, "type": "infographic", "infographic_type": "steps", "title": "...", ...}
  ]
}

════════════════════════════
INFOGRAPHIC DECISION (critical)
════════════════════════════
Use infographic ONLY when the blog has CLEAR, EXTRACTABLE structured content:
  • Named phases/steps in a process (3–7 steps with titles and brief descriptions)
  • Specific statistics with numbers or percentages (e.g. "94% of companies...", "40% cost reduction")
  • A clear list of action items, tips, or best practices (4–8 items)
  • Measurable comparison values (before/after, option A vs B with numbers)

SKIP infographic (use photo instead) when content is:
  • General narrative advice without clear discrete structure
  • Vague statements without extractable data points
  • Content that doesn't cleanly map to one of the 4 types below

MAX 2 infographic slots per batch. Place at the slot where the content naturally fits.
If no qualifying structured content exists → use 0 infographic slots (all photos).

════════════════
INFOGRAPHIC TYPES
════════════════
"steps" — Numbered phases or process steps.
  Required: "items": [{"number":1,"title":"Phase Name","points":["detail 1","detail 2"]}, ...] (3–7 items, max 4 bullet points each)
  Optional: "subtitle": "...", "footer": "One closing insight or tip (max 100 chars)"
  Best for: migration roadmaps, implementation phases, how-to guides, onboarding processes

"stats" — Key impact statistics.
  Required: "stats": [{"value":"94%","label":"of businesses saw improved security after migration"}, ...] (2–4 stats only)
  Optional: "subtitle": "..."
  Best for: blogs citing specific percentages, dollar amounts, time savings, ROI data

"checklist" — Action items or best practices.
  Required: "items": ["First action item", "Second action item", ...] (4–8 items, max 60 chars each)
  Optional: "subtitle": "..."
  Best for: "what to do before/after", "common mistakes to avoid", "must-have features" lists

"bar_chart" — Comparison with measurable values.
  Required: "bars": [{"label":"Before Cloud","value":72,"unit":"%"}, ...] (3–6 bars, values must be numbers)
  Optional: "subtitle": "..."
  Best for: before/after comparisons, adoption rates, feature comparison percentages

Infographic titles: 4–8 words, specific to the blog topic. Keep data concise — quality over quantity.

══════════════════════════════
PHOTO DESCRIPTION RULES (type="photo")
══════════════════════════════
• People: white American/British Caucasian only, age 30–50, average everyday build
• Eyes on screen/desk/colleague — NEVER at camera
• Clothing: plain business casual (navy polo, grey fleece, chinos) — NO logos, no brand names on clothing
• Lived-in detail: jacket on chair, loose cable, sticky notes, notebook, pen — NO food, NO drinks, NO water bottles, NO coffee cups
• REQUIRED ENVIRONMENTS for photo slots (assign in order — next env to each photo slot):
{required_envs}
  Do NOT swap or replace — pre-selected for variety. Infographic slots skip the list.
• Activity must directly relate to the blog topic
• Each photo slot = VISUALLY DISTINCT — different number of people, different camera angle
• Write 1–2 plain sentences: who + what specific task related to topic + where + one lived-in detail
• Do NOT write photography terms ("documentary-style", "candid", "photojournalistic") — added automatically

Total slots: {count}""".strip()


def _plan_image_slots(title: str, content: str, count: int) -> list:
    """Plan all image slots in one Gemini call — returns list of slot dicts.
    Gracefully falls back to all-photo mode on any failure."""

    if FREE_MODE:
        descs = generate_prompts_free(title, content, count)
        return [{"slot": i + 1, "type": "photo", "description": d}
                for i, d in enumerate(descs)]

    _debug_raw = ""
    try:
        from google import genai
        from google.genai import types as gt
        client = genai.Client(api_key=GOOGLE_API_KEY)
        required_envs = _format_required_envs(_pick_required_scenes(count))
        system_instr = (
            _SLOT_PLAN_SYSTEM
            .replace("{count}", str(count))
            .replace("{required_envs}", required_envs)
        )
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Blog Title:\n{title}\n\nBlog Content:\n{content[:5000]}",
            config=gt.GenerateContentConfig(
                system_instruction=system_instr,
                max_output_tokens=4000,
                temperature=1.5,
            )
        )
        _debug_raw = resp.text or ""
        raw = _debug_raw.strip()

        def _extract_json(text: str) -> dict:
            """Try 3 strategies to parse JSON from model response."""
            # Strategy 1: direct parse
            try:
                return json.loads(text)
            except Exception:
                pass
            # Strategy 2: strip markdown fences then parse
            cleaned = re.sub(r"^```[a-z]*\s*", "", text)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
            try:
                return json.loads(cleaned)
            except Exception:
                pass
            # Strategy 3: find outermost { ... } and parse that
            s, e = cleaned.find('{'), cleaned.rfind('}')
            if s != -1 and e > s:
                return json.loads(cleaned[s:e + 1])
            raise ValueError(f"No JSON found in response (first 120 chars): {text[:120]!r}")

        data = _extract_json(raw)
        slots = data.get("slots", [])
        if len(slots) != count:
            raise ValueError(f"Expected {count} slots, got {len(slots)}")
        for s in slots:
            if s.get("type") == "photo" and not (s.get("description") or "").strip():
                raise ValueError(f"Slot {s.get('slot')} photo missing description")
        n_ig = sum(1 for s in slots if s.get("type") == "infographic")
        photo_count = count - n_ig
        label = (f"🔍 Debug — Slot plan: {photo_count} photo{'s' if photo_count != 1 else ''}"
                 + (f", {n_ig} infographic{'s' if n_ig != 1 else ''}" if n_ig else ""))
        with st.expander(label, expanded=False):
            for s in slots:
                if s["type"] == "photo":
                    st.markdown(f"**Slot {s['slot']} 📷 PHOTO:** {(s.get('description') or '')[:180]}")
                else:
                    st.markdown(
                        f"**Slot {s['slot']} 📊 INFOGRAPHIC "
                        f"[{s.get('infographic_type','').upper()}]:** {s.get('title','')}"
                    )
        return slots

    except Exception as e:
        with st.expander(f"⚠️ Slot planning failed — click to debug", expanded=True):
            st.error(f"**Error:** `{e}`")
            st.code(repr(_debug_raw[:400]) if _debug_raw else "No response captured (error before API call)")
        descs = []
        try:
            descs = generate_prompts_live(title, content, count)
        except Exception:
            try:
                descs = generate_prompts_free(title, content, count)
            except Exception:
                pass
        # Always ensure exactly `count` descriptions — pad if AI returned fewer
        fallback = f"IT professional working at a desk on tasks related to {title[:40]}."
        while len(descs) < count:
            descs.append(fallback)
        descs = descs[:count]
        return [{"slot": i + 1, "type": "photo", "description": d}
                for i, d in enumerate(descs)]


def generate_alt_text_for(prompt: str, title: str) -> str:
    payload = {"model": "openai", "messages": [
        {"role": "system", "content": (
            "You write short alt text for blog images. "
            "Rules: 60-80 characters maximum, no quotes, no 'image of', no full sentences — "
            "just a brief descriptive phrase using 1-2 keywords from the blog topic. "
            "Example good output: 'IT technician reviewing network dashboard at desk' (51 chars). "
            "Never exceed 80 characters."
        )},
        {"role": "user", "content": f"Blog topic: {title}\nScene: {prompt[:150]}\n\nWrite the alt text:"}
    ], "private": True}
    for attempt in range(2):
        try:
            r = requests.post("https://text.pollinations.ai/openai", json=payload, timeout=30)
            r.raise_for_status()
            result = _pollinations_text(r.json()).strip().strip('"')
            # Hard cap at 80 chars, trim at last word boundary
            if len(result) > 80:
                result = result[:80].rsplit(' ', 1)[0]
            return result
        except Exception:
            if attempt == 0: time.sleep(3)
    return prompt[:75].rsplit(' ', 1)[0]


# ── Image generation ───────────────────────────────────────────────────────────

def generate_image_free(prompt: str, index: int, width: int, height: int,
                        seed: int = None, model: str = "flux") -> bytes:
    """Generate via Pollinations.ai. model: 'flux' (default) or 'flux-realism' (photorealism LoRA)."""
    enhanced = "Documentary-style candid workplace photography of " + prompt + QUALITY_SUFFIX
    encoded = urllib.parse.quote(enhanced)
    negative_encoded = urllib.parse.quote(NEGATIVE_PROMPT)
    actual_seed = seed if seed is not None else index * 42
    st.write(f"🖼️ Generating via **Pollinations** (`{model}`)...")
    url = (f"https://image.pollinations.ai/prompt/{encoded}"
           f"?width={width}&height={height}&model={model}&seed={actual_seed}"
           f"&nologo=true&enhance=false&negative={negative_encoded}")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    # Resize to target width proportionally (same as Kie path) so images are never stretched
    try:
        img = PILImage.open(io.BytesIO(r.content)).convert("RGB")
        if img.width != width:
            new_h = round(img.height * width / img.width)
            img = img.resize((width, new_h), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()
    except Exception:
        return r.content


def generate_prompt_variation(original_prompt: str, title: str) -> str:
    """Ask AI to write a completely different image scene for the same blog topic.
    Output fills the [SUBJECT/ACTION inside ENVIRONMENT] slot of the master prompt template —
    the generator prepends 'Documentary-style candid workplace photography of' automatically."""
    payload = {
        "model": "openai",
        "messages": [
            {"role": "system", "content": (
                "Write a plain scene description for a documentary office photo generator. "
                "Your output fills this slot: 'Documentary-style candid workplace photography of [YOUR OUTPUT HERE]'\n"
                "⚠️ Do NOT start your output with 'Documentary-style', 'candid workplace photography', or any photography term. Write ONLY the scene content.\n\n"
                "MANDATORY RULES — non-negotiable:\n"
                "- ALL people are white American or British Caucasian, age 30-50, average build\n"
                "- Real imperfect faces: subtle asymmetry, visible pores, natural wrinkles — NOT model-perfect\n"
                "- Candid body language: eyes on screen/desk/colleague, NEVER at camera\n"
                "- Natural hands: resting on desk, around a mug, or out of frame\n"
                "- Believable posture: slight slouch, not perfectly upright\n"
                "- Lived-in office: cables, coffee cups, water bottles, jacket on chair — authentic mess\n"
                "- Soft ambient daylight from windows — NOT dramatic, NOT moody, NOT studio-lit\n"
                "- Different scene and different number of people from the original\n"
                "- Plain business casual clothing only: navy polo, grey fleece, chinos — NO logos or company names on clothing\n\n"
                "BANNED WORDS — never use these:\n"
                "cinematic, dramatic, golden, moody, stunning, glowing, vibrant, misty, hazy, "
                "vintage, bokeh, blurry, soft light, rim light, perfect, flawless, symmetrical, "
                "hologram, neon, glowing code, whiteboard with text, readable signs\n\n"
                "FORMAT: Write 1–2 plain sentences. Sentence 1 = [white Caucasian person/people] + [action] + inside [exact location]. "
                "Sentence 2 (optional) = one lived-in environment detail.\n"
                "Output ONLY the scene description. No labels, no intro, no 'Documentary-style' prefix — that is added automatically."
            )},
            {"role": "user", "content": (
                f"Blog topic: {title}\n"
                f"Original scene (write something different): {original_prompt}\n\n"
                "Write the scene description:"
            )}
        ],
        "private": True
    }
    try:
        r = requests.post("https://text.pollinations.ai/openai", json=payload, timeout=60)
        r.raise_for_status()
        new_prompt = _pollinations_text(r.json()).strip().lstrip("•-* ")
        return new_prompt if len(new_prompt) > 30 else original_prompt
    except Exception:
        return original_prompt


def _generate_cover_scene(title: str) -> str:
    """Generate a cover scene description directly tied to the blog title."""
    payload = {
        "model": "openai",
        "messages": [
            {"role": "system", "content": (
                "Write a plain scene description for a documentary office photo. "
                "The scene must visually represent the EXACT topic from the blog title — not a generic office scene.\n"
                "Your output fills this slot: 'Documentary-style candid workplace photography of [YOUR OUTPUT HERE]'\n"
                "⚠️ Do NOT start with 'Documentary-style', 'candid', or any photography term.\n\n"
                "MANDATORY RULES:\n"
                "- Scene must DIRECTLY show the core activity described in the blog title\n"
                "- ALL people are white American or British Caucasian, age 30-50, average build\n"
                "- Candid: eyes on screen/desk/colleague, NEVER at camera\n"
                "- Plain business casual only: NO logos, no brand names on clothing\n"
                "- Soft ambient office daylight — NOT dramatic\n\n"
                "FORMAT: 1-2 plain sentences. [white Caucasian person/people] + [activity matching blog title] + inside [location]. "
                "Output ONLY the scene description."
            )},
            {"role": "user", "content": f"Blog title: {title}\n\nWrite the cover image scene:"}
        ],
        "private": True
    }
    try:
        r = requests.post("https://text.pollinations.ai/openai", json=payload, timeout=60)
        r.raise_for_status()
        prompt = _pollinations_text(r.json()).strip().lstrip("•-* ")
        return prompt if len(prompt) > 30 else title
    except Exception:
        return title


def _generate_cover_bg(title: str, content_prompts: list) -> bytes:
    """Generate a dedicated background photo for main/thumbnail — tied directly to the blog title."""
    cover_prompt = _generate_cover_scene(title)
    seed = abs(hash(title)) % 90000 + 50000
    return _dispatch_image_gen(cover_prompt, 99, DEFAULT_WIDTH, DEFAULT_HEIGHT, seed=seed)


def generate_image_live(prompt: str, index: int = 1, width: int = 1500, height: int = 1000) -> bytes:
    """Generate image via Kie API.
    Try 1: GPT Image 2   — best photorealism, primary model
    Try 2: Grok Imagine  — fallback if GPT Image 2 fails
    Credits exhausted    — falls back to Pollinations (free)
    Normal model failure — raises error (no Pollinations fallback)
    """
    headers     = {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}
    full_prompt = "Documentary-style candid workplace photography of " + prompt + KIE_QUALITY_SUFFIX

    def _exhausted(msg: str) -> bool:
        return any(k in str(msg).lower() for k in ("credit", "balance", "quota", "insufficient", "limit"))

    def _download_and_resize(url: str) -> bytes:
        raw = requests.get(url, timeout=60).content
        img = PILImage.open(io.BytesIO(raw)).convert("RGB")
        orig_w, orig_h = img.size
        new_h = round(orig_h * width / orig_w)
        img = img.resize((width, new_h), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()

    def _submit_and_poll(label: str, payload: dict):
        """Submit to /jobs/createTask + poll /jobs/recordInfo. Returns bytes or None."""
        try:
            r = requests.post(f"{KIE_BASE}/jobs/createTask", headers=headers,
                              json=payload, timeout=60)
            if r.status_code in (401, 403):
                raise RuntimeError(f"Kie auth error {r.status_code}")
            if r.status_code == 402 or _exhausted(r.text):
                raise RuntimeError("Kie credits exhausted")
            if not r.ok:
                st.write(f"⚠️ {label} HTTP {r.status_code} — skipping.")
                return None
            j = r.json()
            if j.get("code") != 200:
                msg = j.get("msg", "")
                if _exhausted(msg):
                    raise RuntimeError("Kie credits exhausted")
                st.write(f"⚠️ {label} rejected: {msg} — trying next model...")
                return None
            task_id = (j.get("data") or {}).get("taskId")
            if not task_id:
                st.write(f"⚠️ {label}: no taskId — trying next model...")
                return None
        except RuntimeError:
            raise
        except Exception as e:
            st.write(f"⚠️ {label} submission error: {e} — trying next model...")
            return None

        for _ in range(50):
            time.sleep(3)
            try:
                poll = requests.get(f"{KIE_BASE}/jobs/recordInfo", headers=headers,
                                    params={"taskId": task_id}, timeout=30)
                poll.raise_for_status()
            except Exception:
                continue
            data  = poll.json().get("data") or {}
            state = data.get("state", "")
            if state == "success":
                try:
                    urls = json.loads(data.get("resultJson") or "{}").get("resultUrls", [])
                except Exception:
                    urls = []
                if not urls:
                    st.write(f"⚠️ {label}: success but no URL — trying next model...")
                    return None
                return _download_and_resize(urls[0])
            if state == "fail":
                err = data.get("failMsg") or ""
                if _exhausted(err):
                    raise RuntimeError("Kie credits exhausted")
                st.write(f"⚠️ {label} failed: {err or 'server error'} — trying next model...")
                return None
        st.write(f"⚠️ {label} timed out — trying next model...")
        return None

    # ── Payloads ──────────────────────────────────────────────────────────────
    _payloads = {
        "gpt2": ("GPT Image 2", {
            # Primary — best photorealism for humans, max quality settings
            "model": "gpt-image-2-text-to-image",
            "input": {"prompt": full_prompt, "aspect_ratio": "16:9", "resolution": "2K",
                      "quality": "high"},
        }),
        "grok": ("Grok Imagine", {
            # Fallback — good quality, speed mode
            "model": "grok-imagine/text-to-image",
            "input": {"prompt": full_prompt, "aspect_ratio": "16:9",
                      "enable_pro": False, "nsfw_checker": False},
        }),
        "flux2": ("Flux 2 Pro", {
            # Test only — not used in auto
            "model": "flux-2/pro-text-to-image",
            "input": {"prompt": full_prompt, "aspect_ratio": "16:9",
                      "resolution": "1K", "nsfw_checker": False},
        }),
    }
    # Auto: GPT Image 2 first → Grok if GPT fails
    # Pollinations only used when Kie credits are exhausted (not on normal model failure)
    _auto_order = ["gpt2", "grok"]

    # Check if user has pinned a specific model for testing
    _choice = st.session_state.get("_model_choice", "auto")
    _run_order = [_choice] if _choice != "auto" else _auto_order

    try:
        for _key in _run_order:
            _label, _payload = _payloads[_key]
            _verb = "🎨 Generating" if _key == _run_order[0] else "↩️ Trying"
            st.write(f"{_verb} via **Kie API** ({_label})...")
            result = _submit_and_poll(_label, _payload)
            if result:
                return result
            # If user pinned a specific model and it failed, don't try others
            if _choice != "auto":
                break

    except RuntimeError as e:
        # Credits exhausted — only case where Pollinations is used as fallback
        st.warning("⚠️ Kie credits exhausted — switching to Pollinations (free) for remaining images.")
        return generate_image_free(prompt, index, width, height)

    # Normal model failure (not credits) — show error, no Pollinations fallback
    if _choice != "auto":
        raise ValueError(f"{_payloads[_choice][0]} failed — check kie.ai/logs for details.")
    raise ValueError("GPT Image 2 and Grok Imagine both failed — check kie.ai/logs for details.")


def generate_image_openai(prompt: str, index: int, width: int, height: int) -> bytes:
    """Generate via OpenAI DALL-E 3 — photorealistic natural style."""
    if not OPENAI_API_KEY:
        st.warning("⚠️ OPENAI_API_KEY not set — falling back to Pollinations.")
        return generate_image_free(prompt, index, width, height)

    full_prompt = "Documentary-style candid workplace photography of " + prompt + _QUALITY_BLOCK
    st.write("🖼️ Generating via **OpenAI DALL-E 3** (natural style)...")
    try:
        r = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "dall-e-3",
                "prompt": full_prompt,
                "n": 1,
                "size": "1792x1024",   # closest to 16:9 landscape
                "quality": "hd",       # more detail
                "style": "natural",    # photorealistic, NOT vivid/dramatic
                "response_format": "url",
            },
            timeout=120,
        )
        if r.status_code in (400, 401, 403):
            st.warning(f"⚠️ OpenAI error {r.status_code}: {r.json().get('error', {}).get('message', r.text[:200])}")
            st.warning("Falling back to Pollinations.")
            return generate_image_free(prompt, index, width, height)
        r.raise_for_status()
        resp = r.json()
        # Show if DALL-E revised the prompt (useful for debugging)
        revised = resp["data"][0].get("revised_prompt", "")
        if revised:
            st.caption(f"📝 DALL-E revised prompt: {revised[:200]}{'…' if len(revised) > 200 else ''}")
        img_url = resp["data"][0]["url"]
        raw = requests.get(img_url, timeout=60).content
        img = PILImage.open(io.BytesIO(raw)).convert("RGB")
        orig_w, orig_h = img.size
        new_h = round(orig_h * 1500 / orig_w)
        img = img.resize((1500, new_h), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()
    except Exception as e:
        st.warning(f"⚠️ OpenAI generation failed ({e}) — falling back to Pollinations.")
        return generate_image_free(prompt, index, width, height)


def generate_image_gemini(prompt: str, index: int, width: int, height: int) -> bytes:
    """Generate via Google Imagen 3 (falls back to Gemini 2.0 image gen if Imagen unavailable)."""
    if not IMAGEN_API_KEY:
        st.warning("⚠️ IMAGEN_API_KEY not set — falling back to Pollinations.")
        return generate_image_free(prompt, index, width, height)

    full_prompt = "Documentary-style candid workplace photography of " + prompt + _QUALITY_BLOCK
    st.write("🖼️ Generating via **Google Imagen 3**...")

    try:
        from google import genai as gai
        from google.genai import types as gai_types

        client = gai.Client(api_key=IMAGEN_API_KEY)

        img_bytes = None

        # Attempt 1 — Imagen 3 (best quality, needs billing enabled on key)
        try:
            resp = client.models.generate_images(
                model="imagen-3.0-generate-001",
                prompt=full_prompt,
                config=gai_types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="16:9",
                    safety_filter_level="BLOCK_ONLY_HIGH",
                    person_generation="ALLOW_ALL",
                ),
            )
            img_bytes = resp.generated_images[0].image.image_bytes
            st.write("✅ Imagen 3 success")
        except Exception as e1:
            st.write(f"⚠️ Imagen 3 failed: `{e1}`")

        # Attempt 2 — gemini-2.0-flash-exp base model with IMAGE modality
        # (image gen is a capability of the base model; dedicated -image-generation names need special access)
        if not img_bytes:
            try:
                st.write("Trying `gemini-2.0-flash-exp` with IMAGE modality...")
                resp2 = client.models.generate_content(
                    model="gemini-2.0-flash-exp",
                    contents=full_prompt,
                    config=gai_types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                    ),
                )
                for part in resp2.candidates[0].content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        img_bytes = base64.b64decode(part.inline_data.data)
                        break
                if img_bytes:
                    st.write("✅ Gemini 2.0 Flash exp (IMAGE modality) success")
                else:
                    raise ValueError("No image in response — model returned text only")
            except Exception as e2:
                st.write(f"⚠️ gemini-2.0-flash-exp IMAGE modality failed: `{e2}`")

        # Attempt 3 — gemini-2.0-flash-preview-image-generation
        if not img_bytes:
            try:
                st.write("Trying `gemini-2.0-flash-preview-image-generation`...")
                resp3 = client.models.generate_content(
                    model="gemini-2.0-flash-preview-image-generation",
                    contents=full_prompt,
                    config=gai_types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                    ),
                )
                for part in resp3.candidates[0].content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        img_bytes = base64.b64decode(part.inline_data.data)
                        break
                if img_bytes:
                    st.write("✅ Gemini 2.0 Flash preview image generation success")
                else:
                    raise ValueError("No image in response")
            except Exception as e3:
                st.write(f"⚠️ gemini-2.0-flash-preview-image-generation failed: `{e3}`")

        # Attempt 4 — gemini-2.0-flash-exp-image-generation (old dedicated name)
        if not img_bytes:
            try:
                st.write("Trying `gemini-2.0-flash-exp-image-generation`...")
                resp4 = client.models.generate_content(
                    model="gemini-2.0-flash-exp-image-generation",
                    contents=full_prompt,
                    config=gai_types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                    ),
                )
                for part in resp4.candidates[0].content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        img_bytes = base64.b64decode(part.inline_data.data)
                        break
                if img_bytes:
                    st.write("✅ Gemini 2.0 exp image generation success")
                else:
                    raise ValueError("No image in response")
            except Exception as e4:
                st.write(f"⚠️ gemini-2.0-flash-exp-image-generation failed: `{e4}`")

        if not img_bytes:
            raise ValueError(
                "All 4 Google image generation attempts failed. "
                "The API key likely needs: (1) Cloud billing enabled, "
                "(2) 'Generative Language API' enabled in Google Cloud Console, "
                "or (3) Imagen access via aistudio.google.com/prompts/new_chat → Image generation."
            )

        # Resize to target width proportionally
        img = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
        orig_w, orig_h = img.size
        new_h = round(orig_h * width / orig_w)
        img = img.resize((width, new_h), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()

    except Exception as e:
        st.warning(f"⚠️ Google image generation failed: {e} — falling back to Pollinations.")
        return generate_image_free(prompt, index, width, height)


def _dispatch_image_gen(prompt: str, index: int, width: int, height: int,
                        seed: int = None) -> bytes:
    """Always use Kie API — no Pollinations fallback."""
    return generate_image_live(prompt, index, width, height)


# ── Webflow API client ─────────────────────────────────────────────────────────

class WebflowClient:
    BASE = "https://api.webflow.com/v2"

    def __init__(self, api_key: str):
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json",
            "content-type": "application/json"
        }

    def _raise(self, r):
        try:
            detail = r.json()
            msg = detail.get("message") or detail.get("msg") or str(detail)
        except Exception:
            msg = r.text[:300] if r.text else r.reason
        raise requests.HTTPError(
            f"{r.status_code} {r.reason} — {msg}\n(URL: {r.url})", response=r
        )

    def _get(self, path, params=None):
        r = requests.get(f"{self.BASE}{path}", headers=self.headers, params=params, timeout=30)
        if not r.ok:
            self._raise(r)
        return r.json()

    def _post(self, path, body=None):
        r = requests.post(f"{self.BASE}{path}", headers=self.headers, json=body, timeout=60)
        if not r.ok:
            self._raise(r)
        return r.json()

    def _patch(self, path, body):
        r = requests.patch(f"{self.BASE}{path}", headers=self.headers, json=body, timeout=30)
        if not r.ok:
            self._raise(r)
        return r.json()

    def get_sites(self):
        return self._get("/sites")["sites"]

    def get_collections(self, site_id: str):
        return self._get(f"/sites/{site_id}/collections")["collections"]

    def find_blog_collection(self, site_id: str):
        collections = self.get_collections(site_id)
        # Collections to SKIP — these contain "blog" but are not the posts collection
        skip_if = ["categor", "tag", "author", "team", "member",
                   "testimon", "career", "industry", "service", "case"]

        def _is_skip(col):
            name = col.get("displayName", "").lower()
            slug = col.get("slug", "").lower()
            return any(kw in name or kw in slug for kw in skip_if)

        # Pass 1: exact slug "blog" — most reliable signal
        for col in collections:
            if col.get("slug", "").lower() == "blog" and not _is_skip(col):
                return col

        # Pass 2: displayName contains "post" or "article" (e.g. "Blog Posts")
        for col in collections:
            name = col.get("displayName", "").lower()
            if any(kw in name for kw in ["post", "article", "news", "insight"]) and not _is_skip(col):
                return col

        # Pass 3: any collection with "blog" in name/slug, skipping category-like ones
        for col in collections:
            name = col.get("displayName", "").lower()
            slug = col.get("slug", "").lower()
            if ("blog" in name or "blog" in slug) and not _is_skip(col):
                return col

        return collections[0] if collections else None

    def find_item_by_slug(self, collection_id: str, slug: str):
        offset = 0
        while True:
            data = self._get(f"/collections/{collection_id}/items",
                             params={"limit": 100, "offset": offset})
            items = data.get("items", [])
            for item in items:
                if item.get("fieldData", {}).get("slug") == slug:
                    return item
            if len(items) < 100:
                break
            offset += 100
        return None

    def is_published(self, item: dict) -> bool:
        return not item.get("isDraft", True)

    def upload_asset(self, site_id: str, img_bytes: bytes, filename: str) -> str:
        """Upload image to Webflow Assets. Returns hosted URL."""
        file_hash = hashlib.md5(img_bytes).hexdigest()
        resp = self._post(f"/sites/{site_id}/assets", body={
            "fileName": filename,
            "fileSize": len(img_bytes),
            "fileHash": file_hash,
        })
        upload_url = resp.get("uploadUrl")
        upload_details = resp.get("uploadDetails") or {}
        asset_url = (resp.get("hostedUrl")
                     or (resp.get("asset") or {}).get("hostedUrl")
                     or "")
        if not upload_url:
            raise ValueError(f"Webflow did not return an upload URL. Response: {resp}")

        content_type = upload_details.get("Content-Type", "image/jpeg")
        form_data = {k: v for k, v in upload_details.items() if k != "Content-Type"}
        s3 = requests.post(
            upload_url,
            data=form_data,
            files={"file": (filename, img_bytes, content_type)},
            timeout=60,
        )
        if not s3.ok:
            raise ValueError(f"S3 upload failed ({s3.status_code}): {s3.text[:200]}")

        if not asset_url:
            raise ValueError(f"Webflow asset uploaded but hostedUrl missing. Full response: {resp}")
        return asset_url

    def update_item(self, collection_id: str, item_id: str, field_data: dict):
        return self._patch(f"/collections/{collection_id}/items/{item_id}", {"fieldData": field_data})

    def publish_items(self, collection_id: str, item_ids: list):
        return self._post(f"/collections/{collection_id}/items/publish", {"itemIds": item_ids})

    def replace_images_in_richtext(self, html: str, old_urls: list,
                                   new_urls: list, new_alts: list) -> tuple:
        """Replace content images in rich text sequentially (1st new → 1st img, etc.)."""
        soup = BeautifulSoup(html, "html.parser")
        imgs = [img for img in soup.find_all("img")
                if not any(kw in (img.get("src", "") + img.get("alt", "")).lower()
                           for kw in ["author", "avatar", "logo", "icon", "signature"])]
        replaced = 0
        for idx, img_tag in enumerate(imgs):
            if idx >= len(new_urls):
                break
            img_tag["src"] = new_urls[idx]
            if idx < len(new_alts):
                img_tag["alt"] = new_alts[idx]
            replaced += 1
        return str(soup), replaced


# ── Figma template compositing ────────────────────────────────────────────────

def _ensure_inter_font():
    if not INTER_FONT_PATH.exists() or INTER_FONT_PATH.stat().st_size < 10000:
        try:
            r = requests.get(
                "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=30,
            )
            if r.ok and r.content[:4] == b"\x00\x01\x00\x00":
                INTER_FONT_PATH.write_bytes(r.content)
        except Exception:
            pass


_FONT_WEIGHT_MAP = {
    "thin": 100, "extralight": 200, "light": 300, "regular": 400,
    "medium": 500, "semibold": 600, "bold": 700, "extrabold": 800, "black": 900,
}


def _ensure_client_font(font_family: str, font_style: str = "Regular") -> Path:
    """Download the variable-font TTF for the given Google Font family.
    One file covers all weights; the correct weight/style is applied at render time via
    set_variation_by_name(font_style). The font_style argument is kept for future static-file
    support but does not affect the download path.
    Returns local path to a usable TTF."""
    family_cap  = font_family.replace(" ", "")          # "Open Sans" → "OpenSans"
    family_slug = font_family.lower().replace(" ", "")  # "opensans"

    # Cached as Family.ttf — one file serves all weights of this family
    var_fname = ASSETS_DIR / f"{family_cap}.ttf"
    if var_fname.exists() and var_fname.stat().st_size > 10000:
        return var_fname

    # Try all common variable-font axis naming patterns used in Google Fonts GitHub
    urls = [
        # single weight axis (Outfit, Roboto Mono, Nunito, etc.)
        f"https://github.com/google/fonts/raw/main/ofl/{family_slug}/{family_cap}%5Bwght%5D.ttf",
        # optical-size + weight (Inter, etc.)
        f"https://github.com/google/fonts/raw/main/ofl/{family_slug}/{family_cap}%5Bopsz%2Cwght%5D.ttf",
        # width + weight (Roboto, etc.)
        f"https://github.com/google/fonts/raw/main/ofl/{family_slug}/{family_cap}%5Bwdth%2Cwght%5D.ttf",
        # apache mirror (some families)
        f"https://github.com/google/fonts/raw/main/apache/{family_slug}/{family_cap}%5Bwght%5D.ttf",
        # italic optical-size + weight (Inter Italic fallback)
        f"https://github.com/google/fonts/raw/main/ofl/{family_slug}/{family_cap}%5Bopsz%2Cwdth%2Cwght%5D.ttf",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            if r.ok and len(r.content) > 10000 and r.content[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"ttcf"):
                var_fname.write_bytes(r.content)
                return var_fname
        except Exception:
            pass

    return INTER_FONT_PATH


def _parse_tpl(node, prefix, entry):
    """Parse overlay + title info from a Main/thumbnail Figma node into entry dict."""
    if not node:
        return
    bb  = node.get("absoluteBoundingBox", {})
    fw  = bb.get("width",  920)
    fh  = bb.get("height", 613)
    fx  = bb.get("x", 0)
    fy  = bb.get("y", 0)
    entry[f"{prefix}_w"] = int(fw)
    entry[f"{prefix}_h"] = int(fh)
    overlay_node = None
    title_node   = None
    _image_candidate = None  # fallback: narrow RECTANGLE with "image" in name
    for c in node.get("children", []):
        ct = c.get("type", "")
        cn = c.get("name", "").lower()
        if ct == "TEXT" and "title" in cn:
            title_node = c
        elif ct == "GROUP" and overlay_node is None:
            overlay_node = c
        elif ct == "RECTANGLE" and cn == "template" and overlay_node is None:
            overlay_node = c
        elif ct == "RECTANGLE" and cn == "image" and overlay_node is None:
            overlay_node = c
        elif ct == "RECTANGLE" and "image" in cn and _image_candidate is None:
            cbb = c.get("absoluteBoundingBox", {})
            if cbb.get("width", fw) < fw * 0.9:
                _image_candidate = c
    # Only use image-named candidate if no higher-priority overlay was found
    if overlay_node is None and _image_candidate is not None:
        overlay_node = _image_candidate
    if overlay_node:
        obb = overlay_node.get("absoluteBoundingBox", {})
        entry[f"overlay_{prefix}"]   = overlay_node["id"]
        entry[f"overlay_{prefix}_x"] = int(obb.get("x", fx) - fx)
        entry[f"overlay_{prefix}_y"] = int(obb.get("y", fy) - fy)
    else:
        entry[f"overlay_{prefix}"]   = None
        entry[f"overlay_{prefix}_x"] = 0
        entry[f"overlay_{prefix}_y"] = 0
    if title_node:
        tbb  = title_node.get("absoluteBoundingBox", {})
        st_  = title_node.get("style", {})
        fsz  = st_.get("fontSize", 24)
        entry[f"{prefix}_tx"]           = int(tbb.get("x", fx) - fx)
        entry[f"{prefix}_ty"]           = int(tbb.get("y", fy) - fy)
        entry[f"{prefix}_tw"]           = int(tbb.get("width", 290))
        entry[f"{prefix}_fsz"]          = int(round(fsz))
        entry[f"{prefix}_font_family"]  = st_.get("fontFamily", "Inter")
        entry[f"{prefix}_font_style"]   = st_.get("fontStyle",  "Medium")
        # Parse font color from Figma fills (r/g/b are 0–1 floats → convert to 0–255)
        fills = title_node.get("fills", [])
        font_color = (255, 255, 255)  # default white
        if fills:
            solid = next((f for f in fills if f.get("type") == "SOLID"), None)
            if solid:
                c = solid.get("color", {})
                font_color = (
                    round(c.get("r", 1.0) * 255),
                    round(c.get("g", 1.0) * 255),
                    round(c.get("b", 1.0) * 255),
                )
        entry[f"{prefix}_font_color"] = font_color


def _add_client_from_figma_url(figma_url: str) -> tuple:
    """
    Add or refresh a single client template by pasting its Figma frame link.
    Calls only /nodes?ids={node_id}&depth=3 — cheap, targeted, avoids file-level rate limits.
    Returns (success: bool, message: str, client_name: str).
    """
    token = os.getenv("FIGMA_ACCESS_TOKEN", "").strip()
    if not token:
        return False, "FIGMA_ACCESS_TOKEN not set in .env", ""

    match = re.search(r'node-id=([0-9]+)[^0-9]+([0-9]+)', figma_url)
    if not match:
        return False, "No node-id found. In Figma: right-click the client frame → Copy link, then paste that URL here.", ""

    node_id = f"{match.group(1)}:{match.group(2)}"
    hdrs = {"X-Figma-Token": token}
    try:
        r = requests.get(
            f"https://api.figma.com/v1/files/{FIGMA_FILE_KEY}/nodes?ids={node_id}&depth=3",
            headers=hdrs, timeout=20,
        )
        if r.status_code == 429:
            wait = r.headers.get("Retry-After", "unknown")
            return False, f"Rate limited (Retry-After: {wait}s). Try again later.", ""
        if not r.ok:
            return False, f"Figma API error {r.status_code}: {r.text[:120]}", ""

        frame_doc = r.json().get("nodes", {}).get(node_id, {}).get("document", {})
        if not frame_doc:
            return False, "Frame not found. Make sure you copied the link of a top-level client frame.", ""

        frame_name = frame_doc["name"]
        slug       = re.sub(r"[^a-z0-9]+", "-", frame_name.lower()).strip("-")
        children   = frame_doc.get("children", [])

        # Case-insensitive name match — also accept "Main Image", "Thumbnail Group", etc.
        main_node  = next((c for c in children
                           if c.get("type") in ("GROUP", "FRAME")
                           and c["name"].lower().startswith("main")), None)
        thumb_node = next((c for c in children
                           if c.get("type") in ("GROUP", "FRAME")
                           and any(c["name"].lower().startswith(k)
                                   for k in ("thumb", "thumbnail"))), None)

        if not main_node and not thumb_node:
            child_names = [f"'{c['name']}' ({c.get('type','')})" for c in children[:8]]
            return False, (
                f"'{frame_name}' has no child group named 'main' or 'thumbnail'.\n\n"
                f"Children found: {', '.join(child_names)}\n\n"
                f"In Figma: name your main group **main** and your thumbnail group **thumbnail**."
            ), ""

        entry = {
            "name":  frame_name,
            "main":  main_node["id"]  if main_node  else None,
            "thumb": thumb_node["id"] if thumb_node else None,
        }
        _parse_tpl(main_node,  "main",  entry)
        _parse_tpl(thumb_node, "thumb", entry)

        # Delete stale overlay PNGs so they are re-downloaded from new node IDs on next run.
        # This is critical when re-registering after a Figma redesign — old PNGs are always wrong.
        for stale in [ASSETS_DIR / f"logo_panel_main_{slug}.png", ASSETS_DIR / f"logo_panel_thumb_{slug}.png"]:
            if stale.exists():
                stale.unlink()

        cache = _load_node_cache()
        # Preserve webflow_api_key from existing entry if not in new one
        existing = cache.get(slug, {})
        if existing.get("webflow_api_key") and not entry.get("webflow_api_key"):
            entry["webflow_api_key"] = existing["webflow_api_key"]
        cache[slug] = entry
        _save_node_cache(cache)

        # Build a detailed success message so the user can verify at a glance
        mw = entry.get("main_w",  "?")
        mh = entry.get("main_h",  "?")
        tw = entry.get("thumb_w", "?")
        th = entry.get("thumb_h", "?")
        mo = entry.get("overlay_main",  "⚠️ none")
        to = entry.get("overlay_thumb", "⚠️ none")
        mf = entry.get("main_font_family",  "Inter")
        ms = entry.get("main_font_style",   "Regular")
        mc = entry.get("main_font_color",   [255,255,255])
        tf = entry.get("thumb_font_family", "Inter")
        ts = entry.get("thumb_font_style",  "Regular")
        tc = entry.get("thumb_font_color",  [255,255,255])
        msg = (
            f"✅ **{frame_name}** registered.\n\n"
            f"| | Main | Thumbnail |\n"
            f"|---|---|---|\n"
            f"| Canvas | {mw}×{mh} | {tw}×{th} |\n"
            f"| Overlay node | `{mo}` | `{to}` |\n"
            f"| Font | {mf} {ms} | {tf} {ts} |\n"
            f"| Text color | rgb{tuple(mc)} | rgb{tuple(tc)} |\n\n"
            + ("⚠️ **Overlay not found** — title text will render without a design background.\n"
               "Make sure your Figma group has a child GROUP or a RECTANGLE named **template**."
               if not entry.get("overlay_main") and not entry.get("overlay_thumb") else "")
        )
        return True, msg, frame_name

    except Exception as e:
        return False, f"Error: {e}", ""


def _refresh_figma_templates() -> tuple:
    """
    Fetch all client templates from Figma (two cheap calls: depth=2 file scan + nodes details).
    Called automatically on cache miss during generation.
    Returns (success: bool, message: str, names: list).
    """
    token = os.getenv("FIGMA_ACCESS_TOKEN", "").strip()
    if not token:
        return False, "FIGMA_ACCESS_TOKEN not set in .env", []
    hdrs = {"X-Figma-Token": token}
    try:
        r1 = requests.get(
            f"https://api.figma.com/v1/files/{FIGMA_FILE_KEY}?depth=2",
            headers=hdrs, timeout=20,
        )
        if r1.status_code == 429:
            wait = r1.headers.get("Retry-After", "unknown")
            return False, f"Figma rate limited (Retry-After: {wait}s)", []
        if not r1.ok:
            return False, f"Figma API error {r1.status_code}", []

        canvas = r1.json()["document"]["children"][0]
        frames = [f for f in canvas.get("children", []) if f.get("type") == "FRAME"]
        if not frames:
            return False, "No FRAME nodes found on Figma canvas", []

        frame_ids = ",".join(f["id"] for f in frames)
        r2 = requests.get(
            f"https://api.figma.com/v1/files/{FIGMA_FILE_KEY}/nodes?ids={frame_ids}&depth=3",
            headers=hdrs, timeout=20,
        )
        if r2.status_code == 429:
            wait = r2.headers.get("Retry-After", "unknown")
            return False, f"Figma rate limited on nodes call (Retry-After: {wait}s)", []
        if not r2.ok:
            return False, f"Figma nodes API error {r2.status_code}", []

        nodes_data = r2.json().get("nodes", {})
        cache = _load_node_cache()
        names = []
        for frame in frames:
            frame_id  = frame["id"]
            frame_doc = nodes_data.get(frame_id, {}).get("document", frame)
            names.append(frame_doc["name"])
            slug      = re.sub(r"[^a-z0-9]+", "-", frame_doc["name"].lower()).strip("-")
            children  = frame_doc.get("children", [])
            main_node  = next((c for c in children if c["name"].lower() == "main"), None)
            thumb_node = next((c for c in children if c["name"].lower() in ("thumb", "thumbnail")), None)
            entry = {
                "name":  frame_doc["name"],
                "main":  main_node["id"]  if main_node  else None,
                "thumb": thumb_node["id"] if thumb_node else None,
            }
            _parse_tpl(main_node,  "main",  entry)
            _parse_tpl(thumb_node, "thumb", entry)
            cache[slug] = entry
        _save_node_cache(cache)
        return True, f"Loaded {len(names)} template(s): {', '.join(names)}", names
    except Exception as e:
        return False, f"Error: {e}", []


def get_figma_clients() -> list:
    """Return client names from local cache file (no API call)."""
    return _load_node_cache_clients()


FIGMA_NODE_CACHE = Path("figma_node_cache.json")


def _load_node_cache() -> dict:
    try:
        return json.loads(FIGMA_NODE_CACHE.read_text(encoding="utf-8")) if FIGMA_NODE_CACHE.exists() else {}
    except Exception:
        return {}


def _load_node_cache_clients() -> list:
    return list(_load_node_cache().keys())


def _match_client(site_name: str) -> str:
    """Return the cache key (slug) that best matches a Webflow site display name, or '' if none."""
    cache = _load_node_cache()
    if not cache:
        return ""
    s = re.sub(r"[^a-z0-9]+", "-", site_name.lower()).strip("-")
    if s in cache:
        return s
    for key in cache:
        if key in s:
            return key
    for key in cache:
        if s in key:
            return key
    return ""   # no match — do not fall back to a random client


def _client_display_name(slug: str) -> str:
    """Return the original Figma frame name for a cache slug, or title-case fallback."""
    entry = _load_node_cache().get(slug, {})
    return entry.get("name", slug.replace("-", " ").title())


def _save_node_cache(cache: dict):
    try:
        FIGMA_NODE_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception:
        pass


def _lookup_node_ids(slug: str) -> tuple:
    """Read node IDs from local cache only — no Figma API call here."""
    entry = _load_node_cache().get(slug, {})
    return entry.get("main"), entry.get("thumb")


def ensure_figma_assets_for_client(client_name: str) -> tuple:
    """
    Download overlay PNGs for a specific client from Figma.
    Uses overlay_main / overlay_thumb node IDs from cache (not the frame nodes).
    Returns (main_path, thumb_path).
    """
    slug       = client_name.lower().replace(" ", "-")
    main_path  = ASSETS_DIR / f"logo_panel_main_{slug}.png"
    thumb_path = ASSETS_DIR / f"logo_panel_thumb_{slug}.png"

    missing_main  = not main_path.exists()  or main_path.stat().st_size  < 1000
    missing_thumb = not thumb_path.exists() or thumb_path.stat().st_size < 1000

    _ensure_inter_font()

    if not (missing_main or missing_thumb):
        return main_path, thumb_path

    if not FIGMA_TOKEN:
        st.warning("⚠️ FIGMA_ACCESS_TOKEN not set — compositing without logo overlay.")
        return main_path, thumb_path

    entry = _load_node_cache().get(slug, {})
    overlay_main_id  = entry.get("overlay_main")
    overlay_thumb_id = entry.get("overlay_thumb")

    if not overlay_main_id and not overlay_thumb_id:
        st.warning(f"⚠️ No overlay nodes found for '{client_name}' — reload the app to refresh Figma data.")
        return main_path, thumb_path

    try:
        node_map = {}
        if missing_main  and overlay_main_id:  node_map[overlay_main_id]  = main_path
        if missing_thumb and overlay_thumb_id: node_map[overlay_thumb_id] = thumb_path

        img_r = requests.get(
            f"https://api.figma.com/v1/images/{FIGMA_FILE_KEY}",
            headers={"X-Figma-Token": FIGMA_TOKEN},
            params={"ids": ",".join(node_map), "format": "png", "scale": "1"},
            timeout=30,
        )
        img_r.raise_for_status()
        for node_id, url in img_r.json().get("images", {}).items():
            if url and node_id in node_map:
                node_map[node_id].write_bytes(requests.get(url, timeout=30).content)

    except Exception as e:
        st.warning(f"⚠️ Could not download Figma overlay for '{client_name}': {e}")

    # Pre-download fonts so composite_template never blocks on network
    for prefix in ("main", "thumb"):
        ff = entry.get(f"{prefix}_font_family", "Inter")
        fs = entry.get(f"{prefix}_font_style",  "Regular")
        _ensure_client_font(ff, fs)

    return main_path, thumb_path


def make_tpls(client_name: str, main_logo: Path, thumb_logo: Path) -> tuple:
    """Return per-client template dicts with canvas size, overlay position, font, and text position from cache."""
    slug  = client_name.lower().replace(" ", "-")
    entry = _load_node_cache().get(slug, {})

    def _font_info(prefix):
        ff = entry.get(f"{prefix}_font_family", "Inter")
        fs = entry.get(f"{prefix}_font_style",  "Regular")
        return _ensure_client_font(ff, fs), _FONT_WEIGHT_MAP.get(fs.lower().replace(" ", ""), 400), fs

    m_font, m_weight, m_style = _font_info("main")
    t_font, t_weight, t_style = _font_info("thumb")

    m = {
        "w":              entry.get("main_w",  MAIN_TPL["w"]),
        "h":              entry.get("main_h",  MAIN_TPL["h"]),
        "logo":           main_logo,
        "ox":             entry.get("overlay_main_x", 0),
        "oy":             entry.get("overlay_main_y", 0),
        "tx":             entry.get("main_tx",  MAIN_TPL["tx"]),
        "ty":             entry.get("main_ty",  MAIN_TPL["ty"]),
        "tw":             entry.get("main_tw",  MAIN_TPL["tw"]),
        "fsz":            entry.get("main_fsz", MAIN_TPL["fsz"]),
        "font":           m_font,
        "font_weight":    m_weight,
        "font_style_name": m_style,
        "font_color":     tuple(entry.get("main_font_color", [255, 255, 255])),
    }
    t = {
        "w":              entry.get("thumb_w",  THUMB_TPL["w"]),
        "h":              entry.get("thumb_h",  THUMB_TPL["h"]),
        "logo":           thumb_logo,
        "ox":             entry.get("overlay_thumb_x", 0),
        "oy":             entry.get("overlay_thumb_y", 0),
        "tx":             entry.get("thumb_tx",  THUMB_TPL["tx"]),
        "ty":             entry.get("thumb_ty",  THUMB_TPL["ty"]),
        "tw":             entry.get("thumb_tw",  THUMB_TPL["tw"]),
        "fsz":            entry.get("thumb_fsz", THUMB_TPL["fsz"]),
        "font":           t_font,
        "font_weight":    t_weight,
        "font_style_name": t_style,
        "font_color":     tuple(entry.get("thumb_font_color", [255, 255, 255])),
    }
    return m, t


def ensure_figma_assets() -> bool:
    """Legacy fallback — downloads hardcoded MSP Launchpad logo panels (node 2:17, 2:19)."""
    needed = [("2:17", MAIN_TPL["logo"]), ("2:19", THUMB_TPL["logo"])]
    missing = [nid for nid, p in needed if not p.exists() or p.stat().st_size < 1000]
    _ensure_inter_font()
    if missing and FIGMA_TOKEN:
        try:
            r = requests.get(
                f"https://api.figma.com/v1/images/{FIGMA_FILE_KEY}",
                headers={"X-Figma-Token": FIGMA_TOKEN},
                params={"ids": ",".join(missing), "format": "png", "scale": "1"},
                timeout=30,
            )
            r.raise_for_status()
            node_to_path = {"2:17": MAIN_TPL["logo"], "2:19": THUMB_TPL["logo"]}
            for nid, url in r.json().get("images", {}).items():
                if url and nid in node_to_path:
                    node_to_path[nid].write_bytes(requests.get(url, timeout=30).content)
        except Exception as e:
            st.warning(f"⚠️ Could not download Figma logo panels: {e}. Compositing without logo overlay.")
    elif missing and not FIGMA_TOKEN:
        st.warning("⚠️ FIGMA_ACCESS_TOKEN not set — compositing without logo overlay.")
    return all(p.exists() and p.stat().st_size > 1000 for _, p in needed)


def _cover_crop(img: PILImage.Image, w: int, h: int) -> PILImage.Image:
    """Scale image to fill canvas maintaining aspect ratio, then center-crop."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = max(int(iw * scale), w), max(int(ih * scale), h)
    img = img.resize((nw, nh), PILImage.LANCZOS)
    left = (nw - w) // 2
    top  = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def composite_template(bg_bytes: bytes, title: str, tpl: dict) -> bytes | None:
    """Composite: bg photo (cover-cropped) + logo overlay + wrapped title text → PNG bytes."""
    try:
        bg_img = PILImage.open(io.BytesIO(bg_bytes)).convert("RGB")
        canvas = _cover_crop(bg_img, tpl["w"], tpl["h"])
        logo_path = tpl["logo"]
        if logo_path.exists() and logo_path.stat().st_size > 1000:
            logo = PILImage.open(logo_path).convert("RGBA")
            logo_rgb   = logo.convert("RGB")
            logo_alpha = logo.split()[3]
            canvas.paste(logo_rgb, (tpl.get("ox", 0), tpl.get("oy", 0)), mask=logo_alpha)
        draw = ImageDraw.Draw(canvas)
        try:
            font_path        = tpl.get("font", INTER_FONT_PATH)
            font_weight      = tpl.get("font_weight", 400)
            font_style_name  = tpl.get("font_style_name", "Regular")
            font = ImageFont.truetype(str(font_path), tpl["fsz"])
            # Variable font: try named instance first (works for multi-axis fonts like
            # Roboto[wdth,wght] where set_variation_by_axes([single_value]) would fail).
            # Static fonts silently ignore both calls — weight is already baked in.
            try:
                font.set_variation_by_name(font_style_name)
            except (AttributeError, OSError, ValueError):
                try:
                    font.set_variation_by_axes([font_weight])
                except (AttributeError, OSError):
                    pass
        except Exception:
            font = ImageFont.load_default()
        _CHAR_MAP = {"®": "(R)", "™": "(TM)", "©": "(C)", "’": "'", "‘": "'",
                     "“": '"', "”": '"', "–": "-", "—": "-",
                     "…": "...", "°": " deg", "é": "e", "à": "a"}
        clean_title = "".join(_CHAR_MAP.get(c, c) for c in title
                               if ord(c) < 256 or c in _CHAR_MAP)
        words = clean_title.split()
        lines, cur = [], []
        for word in words:
            test = " ".join(cur + [word])
            if draw.textbbox((0, 0), test, font=font)[2] <= tpl["tw"]:
                cur.append(word)
            else:
                if cur: lines.append(" ".join(cur))
                cur = [word]
        if cur: lines.append(" ".join(cur))
        lh = tpl["fsz"] + 6
        fc = tpl.get("font_color", (255, 255, 255))
        for i, line in enumerate(lines):
            draw.text((tpl["tx"], tpl["ty"] + i * lh), line, fill=fc, font=font)
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


# ── Python-rendered infographics ───────────────────────────────────────────────

_IG = {
    "navy":   (20,  52,  85),
    "navy2":  (30,  66, 105),
    "blue":   (25, 128, 220),
    "white":  (255, 255, 255),
    "black":  (22,  28,  40),
    "gray":   (100, 110, 128),
    "ltgray": (158, 168, 182),
    "bgray":  (245, 248, 252),
    "border": (208, 218, 230),
    "shadow": (192, 204, 218),
    "step_colors": [
        (21,  101, 192),
        (0,   137, 123),
        (56,  142,  60),
        (245, 124,   0),
        (194,  24,  91),
        (81,   45, 168),
        (2,   119, 189),
    ],
}


def _ig_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    _ensure_inter_font()
    try:
        f = ImageFont.truetype(str(INTER_FONT_PATH), size)
        try:
            f.set_variation_by_name("Bold" if bold else "Regular")
        except Exception:
            pass
        return f
    except Exception:
        return ImageFont.load_default()


def _ig_wrap(text: str, font, max_w: int) -> list:
    words = (text or "").split()
    lines, cur = [], []
    for word in words:
        test = " ".join(cur + [word])
        try:
            w = font.getbbox(test)[2]
        except Exception:
            w = len(test) * 8
        if w <= max_w:
            cur.append(word)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [word]
    if cur:
        lines.append(" ".join(cur))
    return lines or [""]


def _ig_bytes(img: PILImage.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=93)
    return buf.getvalue()


def _ig_card(draw, x0, y0, x1, y1, radius=10):
    """Draw shadow + white card."""
    SO = 4
    draw.rounded_rectangle([x0 + SO, y0 + SO, x1 + SO, y1 + SO],
                            radius=radius, fill=_IG["shadow"])
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=_IG["white"])


def _render_steps_infographic(spec: dict, w: int, h: int) -> bytes:
    img = PILImage.new("RGB", (w, h), _IG["bgray"])
    d = ImageDraw.Draw(img)
    HDR, SUBHDR, FTR = 84, 42, 66

    # Header
    d.rectangle([0, 0, w, HDR], fill=_IG["navy"])
    d.rectangle([0, HDR - 4, w, HDR], fill=_IG["blue"])
    title = (spec.get("title") or "Process Overview")[:60]
    tf = _ig_font(43, bold=True)
    tw = tf.getbbox(title)[2]
    d.text(((w - tw) // 2, (HDR - 43) // 2), title, font=tf, fill=_IG["white"])

    # Subheader
    d.rectangle([0, HDR, w, HDR + SUBHDR], fill=_IG["navy2"])
    subtitle = (spec.get("subtitle") or "")[:100]
    if subtitle:
        sf = _ig_font(17)
        sw = sf.getbbox(subtitle)[2]
        d.text(((w - sw) // 2, HDR + (SUBHDR - 17) // 2),
               subtitle, font=sf, fill=(182, 212, 248))

    # Footer
    FY = h - FTR
    d.rectangle([0, FY, w, h], fill=_IG["navy"])
    d.rectangle([0, FY, w, FY + 4], fill=_IG["blue"])
    footer = (spec.get("footer") or "")[:120]
    if footer:
        ff = _ig_font(15)
        flines = _ig_wrap(footer, ff, w - 100)
        fy = FY + (FTR - len(flines) * 21) // 2 + 3
        for line in flines:
            lw = ff.getbbox(line)[2]
            d.text(((w - lw) // 2, fy), line, font=ff, fill=(182, 212, 248))
            fy += 21

    # Steps
    items = (spec.get("items") or [])[:7]
    n = len(items)
    if not n:
        return _ig_bytes(img)

    TOP = HDR + SUBHDR + 16
    BOT = FY - 14
    PAD, GAP = 34, 14
    card_w = max(80, (w - 2 * PAD - (n - 1) * GAP) // n)
    CIR_R = 23

    for i, item in enumerate(items):
        cx = PAD + i * (card_w + GAP)
        color = _IG["step_colors"][i % len(_IG["step_colors"])]
        ctr = cx + card_w // 2

        # Card
        _ig_card(d, cx, TOP, cx + card_w, BOT)
        # Colored top accent strip
        d.rounded_rectangle([cx, TOP, cx + card_w, TOP + 6],
                             radius=10, fill=color)

        # Number circle — outer ring + fill
        cy_cir = TOP + 22 + CIR_R
        lighter = tuple(min(255, c + 75) for c in color)
        d.ellipse([ctr - CIR_R - 4, cy_cir - CIR_R - 4,
                   ctr + CIR_R + 4, cy_cir + CIR_R + 4], fill=lighter)
        d.ellipse([ctr - CIR_R, cy_cir - CIR_R,
                   ctr + CIR_R, cy_cir + CIR_R], fill=color)
        num = f"{item.get('number', i + 1):02d}"
        nf = _ig_font(17, bold=True)
        nw = nf.getbbox(num)[2]
        d.text((ctr - nw // 2, cy_cir - 10), num, font=nf, fill=_IG["white"])

        # Arrow dots to next step
        if i < n - 1:
            ax = cx + card_w + GAP // 2
            for dx in (-4, 0, 4):
                d.ellipse([ax + dx - 3, cy_cir - 3,
                           ax + dx + 3, cy_cir + 3], fill=_IG["ltgray"])
            d.polygon([(ax + 9, cy_cir), (ax + 3, cy_cir - 6),
                       (ax + 3, cy_cir + 6)], fill=_IG["ltgray"])

        # Step title
        st_text = (item.get("title") or f"Step {i+1}")[:36]
        stf = _ig_font(13, bold=True)
        st_y = cy_cir + CIR_R + 9
        for line in _ig_wrap(st_text, stf, card_w - 14)[:2]:
            lw = stf.getbbox(line)[2]
            d.text((ctr - lw // 2, st_y), line, font=stf, fill=color)
            st_y += 17

        # Divider
        sep_y = st_y + 4
        d.line([cx + 12, sep_y, cx + card_w - 12, sep_y], fill=_IG["border"], width=1)

        # Faded watermark number fills lower white space tastefully
        wm_str = f"{item.get('number', i + 1):02d}"
        wm_size = min(170, card_w - 10)
        wm_f = _ig_font(wm_size, bold=True)
        wm_w = wm_f.getbbox(wm_str)[2]
        wm_h = wm_f.getbbox(wm_str)[3]
        bg_r, bg_g, bg_b = _IG["bgray"]
        c_r, c_g, c_b = color
        faint = (int(bg_r * 0.91 + c_r * 0.09),
                 int(bg_g * 0.91 + c_g * 0.09),
                 int(bg_b * 0.91 + c_b * 0.09))
        wm_y = BOT - wm_h - 10
        if wm_y > sep_y + 20:
            d.text((ctr - wm_w // 2, wm_y), wm_str, font=wm_f, fill=faint)

        # Bullet points (drawn after watermark so they appear on top)
        bf = _ig_font(11)
        by = sep_y + 7
        for pt in (item.get("points") or [])[:4]:
            for pl in _ig_wrap(f"• {(pt or '')[:55]}", bf, card_w - 16)[:2]:
                if by + 13 > BOT - 12:
                    break
                d.text((cx + 9, by), pl, font=bf, fill=_IG["black"])
                by += 13
            if by + 13 > BOT - 12:
                break

    return _ig_bytes(img)


def _render_stats_infographic(spec: dict, w: int, h: int) -> bytes:
    img = PILImage.new("RGB", (w, h), _IG["bgray"])
    d = ImageDraw.Draw(img)
    HDR = 98

    d.rectangle([0, 0, w, HDR], fill=_IG["navy"])
    d.rectangle([0, HDR - 4, w, HDR], fill=_IG["blue"])
    title = (spec.get("title") or "Key Statistics")[:60]
    tf = _ig_font(43, bold=True)
    tw = tf.getbbox(title)[2]
    d.text(((w - tw) // 2, 13), title, font=tf, fill=_IG["white"])
    subtitle = (spec.get("subtitle") or "")[:100]
    if subtitle:
        sf = _ig_font(17)
        sw = sf.getbbox(subtitle)[2]
        d.text(((w - sw) // 2, 60), subtitle, font=sf, fill=(182, 212, 248))

    stats = (spec.get("stats") or [])[:4]
    n = len(stats)
    if not n:
        return _ig_bytes(img)

    cols = 2 if n == 4 else min(n, 3)
    rows = (n + cols - 1) // cols
    pad, gap = 42, 22
    cell_w = (w - 2 * pad - (cols - 1) * gap) // cols
    cell_h = (h - HDR - 2 * pad - (rows - 1) * gap) // rows

    for idx, stat in enumerate(stats):
        row, col = divmod(idx, cols)
        cx = pad + col * (cell_w + gap)
        cy = HDR + pad + row * (cell_h + gap)
        color = _IG["step_colors"][idx % len(_IG["step_colors"])]

        _ig_card(d, cx, cy, cx + cell_w, cy + cell_h, radius=12)
        d.rounded_rectangle([cx, cy, cx + cell_w, cy + 8],
                             radius=12, fill=color)

        val = str(stat.get("value") or "—")[:10]
        fs = min(64, max(38, 64 - max(0, len(val) - 4) * 7))
        vf = _ig_font(fs, bold=True)
        vw = vf.getbbox(val)[2]
        vh = vf.getbbox(val)[3]

        label = (stat.get("label") or "")[:80]
        lf = _ig_font(14)
        llines = _ig_wrap(label, lf, cell_w - 28)[:3]
        # Vertically center value + label block within card (below accent bar)
        inner_h = vh + 12 + len(llines) * 20
        val_y = cy + 8 + max(14, (cell_h - 8 - inner_h) // 2)
        d.text((cx + (cell_w - vw) // 2, val_y), val, font=vf, fill=color)
        ly = val_y + vh + 12
        for ll in llines:
            lw = lf.getbbox(ll)[2]
            d.text((cx + (cell_w - lw) // 2, ly), ll, font=lf, fill=_IG["gray"])
            ly += 20

    return _ig_bytes(img)


def _render_checklist_infographic(spec: dict, w: int, h: int) -> bytes:
    img = PILImage.new("RGB", (w, h), _IG["bgray"])
    d = ImageDraw.Draw(img)
    HDR = 98

    d.rectangle([0, 0, w, HDR], fill=_IG["navy"])
    d.rectangle([0, HDR - 4, w, HDR], fill=_IG["blue"])
    title = (spec.get("title") or "Checklist")[:60]
    tf = _ig_font(43, bold=True)
    tw = tf.getbbox(title)[2]
    d.text(((w - tw) // 2, 13), title, font=tf, fill=_IG["white"])
    subtitle = (spec.get("subtitle") or "")[:100]
    if subtitle:
        sf = _ig_font(17)
        sw = sf.getbbox(subtitle)[2]
        d.text(((w - sw) // 2, 60), subtitle, font=sf, fill=(182, 212, 248))

    items = (spec.get("items") or [])[:8]
    n = len(items)
    if not n:
        return _ig_bytes(img)

    cols = 2 if n > 4 else 1
    per_col = (n + cols - 1) // cols
    pad_x, pad_y = 56, 24
    col_w = (w - 2 * pad_x - (cols - 1) * 48) // cols
    avail_h = h - HDR - 2 * pad_y
    row_h = avail_h // per_col
    item_f = _ig_font(16)
    box = 26

    for idx, item in enumerate(items):
        col, row = divmod(idx, per_col)
        cx = pad_x + col * (col_w + 48)
        row_ctr = HDR + pad_y + row * row_h + row_h // 2
        card_top = row_ctr - row_h // 2 + 5
        card_bot = row_ctr + row_h // 2 - 5
        color = _IG["step_colors"][idx % len(_IG["step_colors"])]

        _ig_card(d, cx - 8, card_top, cx + col_w + 8, card_bot, radius=8)

        # Checkbox
        bx_y = row_ctr - box // 2
        d.rounded_rectangle([cx, bx_y, cx + box, bx_y + box],
                             radius=5, fill=color)
        ck = [(cx + 5, bx_y + 13), (cx + 11, bx_y + 19), (cx + 22, bx_y + 8)]
        d.line(ck, fill=_IG["white"], width=3)

        # Text
        item_text = (item or "")[:70]
        txt_lines = _ig_wrap(item_text, item_f, col_w - box - 16)[:2]
        ty = row_ctr - (len(txt_lines) * 20) // 2
        for tl in txt_lines:
            d.text((cx + box + 12, ty), tl, font=item_f, fill=_IG["black"])
            ty += 22

    return _ig_bytes(img)


def _render_bar_chart_infographic(spec: dict, w: int, h: int) -> bytes:
    img = PILImage.new("RGB", (w, h), _IG["bgray"])
    d = ImageDraw.Draw(img)
    HDR = 98

    d.rectangle([0, 0, w, HDR], fill=_IG["navy"])
    d.rectangle([0, HDR - 4, w, HDR], fill=_IG["blue"])
    title = (spec.get("title") or "Comparison")[:60]
    tf = _ig_font(43, bold=True)
    tw = tf.getbbox(title)[2]
    d.text(((w - tw) // 2, 13), title, font=tf, fill=_IG["white"])
    subtitle = (spec.get("subtitle") or "")[:100]
    if subtitle:
        sf = _ig_font(17)
        sw = sf.getbbox(subtitle)[2]
        d.text(((w - sw) // 2, 60), subtitle, font=sf, fill=(182, 212, 248))

    bars = (spec.get("bars") or [])[:7]
    n = len(bars)
    if not n:
        return _ig_bytes(img)

    max_v = max(float(b.get("value", 0)) for b in bars) or 1
    lf = _ig_font(15, bold=True)
    vf = _ig_font(15)
    label_w = min(290, max((lf.getbbox(b.get("label", ""))[2] for b in bars), default=0) + 18)
    pad_x, pad_y = 44, 20
    bar_x = pad_x + label_w + 12
    bar_max_w = w - bar_x - pad_x - 80
    chart_top = HDR + pad_y
    chart_h = h - chart_top - pad_y
    bar_h = min(54, (chart_h - (n - 1) * 12) // n)
    gap = max(8, (chart_h - n * bar_h) // (n + 1))

    for i, bar in enumerate(bars):
        by = chart_top + gap + i * (bar_h + gap)
        val = float(bar.get("value", 0))
        bw = max(0, int(bar_max_w * val / max_v))
        color = _IG["step_colors"][i % len(_IG["step_colors"])]

        # Label right-aligned
        label = (bar.get("label") or "")[:40]
        ll = _ig_wrap(label, lf, label_w - 10)[:2]
        ly = by + (bar_h - len(ll) * 19) // 2
        for ln in ll:
            lnw = lf.getbbox(ln)[2]
            d.text((pad_x + label_w - lnw - 8, ly), ln, font=lf, fill=_IG["black"])
            ly += 20

        # Bar track + fill
        d.rounded_rectangle([bar_x, by, bar_x + bar_max_w, by + bar_h],
                             radius=6, fill=_IG["border"])
        if bw > 0:
            d.rounded_rectangle([bar_x, by, bar_x + bw, by + bar_h],
                                 radius=6, fill=color)
            # Shine strip (top fifth)
            shine_h = max(3, bar_h // 5)
            shine = tuple(min(255, c + 45) for c in color)
            d.rounded_rectangle([bar_x, by, bar_x + bw, by + shine_h],
                                 radius=6, fill=shine)

        unit = str(bar.get("unit", ""))
        val_int = int(val) if val == int(val) else val
        d.text((bar_x + bw + 10, by + (bar_h - 17) // 2),
               f"{val_int}{unit}", font=vf, fill=_IG["gray"])

    return _ig_bytes(img)


# ── Infographic branding helpers ──────────────────────────────────────────────

def _dominant_hex(img_bytes: bytes) -> str:
    """Extract most common non-white/non-black color from image → hex string."""
    try:
        img = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
        img = img.resize((60, 60))
        from collections import Counter
        filtered = [p for p in img.getdata()
                    if not (all(c > 215 for c in p) or all(c < 40 for c in p))]
        if not filtered:
            return "#1A3A5C"
        r, g, b = Counter(filtered).most_common(1)[0][0]
        return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return "#1A3A5C"


def _client_branding(slug: str) -> str:
    """Return hex brand color for a client by extracting dominant color from its overlay PNG.
    Only returns color — logo is always scraped from the blog URL via _scrape_page_branding."""
    logo_path = ASSETS_DIR / f"logo_panel_main_{slug}.png"
    if not logo_path.exists() or logo_path.stat().st_size < 1000:
        return "#1A3A5C"
    try:
        return _dominant_hex(logo_path.read_bytes())
    except Exception:
        return "#1A3A5C"


def _scrape_page_branding(url: str) -> tuple:
    """Scrape brand color + logo from a blog page URL.
    Returns (hex_color, logo_bytes). Falls back to defaults on any error."""
    hex_color = "#1A3A5C"
    logo_bytes = None
    try:
        from urllib.parse import urlparse
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        soup = BeautifulSoup(r.content, "html.parser")
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # 1. Brand color — theme-color meta tag
        meta_color = soup.find("meta", attrs={"name": "theme-color"})
        if meta_color and meta_color.get("content", "").startswith("#"):
            hex_color = meta_color["content"].strip()

        # 2. Logo — look for high-quality icon links
        icon_url = ""
        for rel in (["apple-touch-icon"], ["icon"], ["shortcut icon"]):
            link = soup.find("link", rel=rel)
            if link and link.get("href"):
                icon_url = link["href"]
                break
        if not icon_url:
            # Try og:image as fallback (often the brand logo)
            og = soup.find("meta", property="og:image")
            if og:
                icon_url = og.get("content", "")

        if icon_url:
            if icon_url.startswith("//"):
                icon_url = "https:" + icon_url
            elif icon_url.startswith("/"):
                icon_url = base + icon_url
            ir = requests.get(icon_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if ir.ok and len(ir.content) > 500:
                logo_bytes = ir.content
                # If we didn't get color from meta, extract from logo
                if hex_color == "#1A3A5C":
                    hex_color = _dominant_hex(logo_bytes)

    except Exception:
        pass
    return hex_color, logo_bytes


def _apply_corner_logo(img_bytes: bytes, logo_bytes: bytes | None,
                       position: str = "bottom-right") -> bytes:
    """Composite a small logo in the corner of an infographic image."""
    if not logo_bytes:
        return img_bytes
    try:
        img = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
        W, H = img.size

        logo_raw = PILImage.open(io.BytesIO(logo_bytes)).convert("RGBA")
        # Target: logo height ~7% of image height
        target_h = max(40, int(H * 0.07))
        target_w = int(logo_raw.width * target_h / logo_raw.height)
        # Cap width at 15% of image width
        if target_w > W * 0.15:
            target_w = int(W * 0.15)
            target_h = int(logo_raw.height * target_w / logo_raw.width)
        logo_small = logo_raw.resize((target_w, target_h), PILImage.LANCZOS)

        margin = 16
        if position == "bottom-right":
            x = W - target_w - margin
            y = H - target_h - margin
        elif position == "bottom-left":
            x, y = margin, H - target_h - margin
        else:
            x = W - target_w - margin
            y = H - target_h - margin

        # White semi-transparent pill background
        pad = 8
        bg = PILImage.new("RGBA", (target_w + pad * 2, target_h + pad * 2), (255, 255, 255, 200))
        img_rgba = img.convert("RGBA")
        img_rgba.paste(bg, (x - pad, y - pad), bg)
        img_rgba.paste(logo_small, (x, y), logo_small)

        buf = io.BytesIO()
        img_rgba.convert("RGB").save(buf, format="JPEG", quality=93)
        return buf.getvalue()
    except Exception:
        return img_bytes


def _render_infographic(spec: dict, w: int, h: int) -> bytes:
    """Dispatch to the correct renderer. Never raises — always returns valid image bytes."""
    try:
        t = spec.get("infographic_type", "steps")
        if t == "steps":     return _render_steps_infographic(spec, w, h)
        if t == "stats":     return _render_stats_infographic(spec, w, h)
        if t == "checklist": return _render_checklist_infographic(spec, w, h)
        if t == "bar_chart": return _render_bar_chart_infographic(spec, w, h)
        return _render_steps_infographic(spec, w, h)
    except Exception as e:
        img = PILImage.new("RGB", (w, h), _IG["navy"])
        draw = ImageDraw.Draw(img)
        draw.text((40, 40), (spec.get("title") or "Infographic"),
                  font=_ig_font(22, bold=True), fill=_IG["white"])
        draw.text((40, 82), f"Render error: {str(e)[:100]}",
                  font=_ig_font(15), fill=(175, 205, 238))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()


_STEPS_STYLES = [
    "Horizontal numbered steps in connected cards with arrows between them. Numbered circles on top of each card.",
    "Vertical timeline layout with alternating left-right content blocks and a center line with numbered dots.",
    "Icon-driven horizontal flow with large icons above each step title and description below.",
]
_STATS_STYLES = [
    "Large bold number cards in a clean 2x2 grid, each with a colored accent bar on the left.",
    "Horizontal row of stat bubbles — oversized number in brand color, label below in gray.",
    "Split layout: icon on the left, large stat value and label on the right, separated by a thin divider.",
]
_CHECKLIST_STYLES = [
    "Single-column checklist with colored filled checkboxes and bold item text, subtle row alternating background.",
    "Two-column card grid, each item in its own rounded card with a checkmark icon and short description.",
    "Left accent bar layout: colored vertical bar per item, item text to the right, clean and minimal.",
]
_BARCHART_STYLES = [
    "Horizontal bar chart with value labels at the end of each bar and category labels on the left.",
    "Vertical column chart with rounded bar tops, value labels above each column, clean axis lines.",
    "Segmented progress-bar layout, each row showing label, filled bar, and percentage value.",
]

def _infographic_prompt(spec: dict, brand_color: str = "#1A3A5C") -> str:
    """Convert a Gemini infographic spec into a GPT Image 2 prompt."""
    ig_type = spec.get("infographic_type", "steps")
    title    = spec.get("title", "Business Infographic")
    subtitle = spec.get("subtitle", "")

    import random as _random
    _style_idx = _random.randint(0, 2)

    base = (
        f'Professional business infographic titled "{title}". '
        + (f'Subtitle: "{subtitle}". ' if subtitle else "")
        + f"Use {brand_color} as the dominant accent color for headers, icons, and design elements. "
        "Clean flat design, white background, "
        "no photography, no people, no faces, graphic design only, "
        "corporate presentation style, clear readable bold text, "
        "16:9 landscape format. "
    )

    if ig_type == "steps":
        items = spec.get("items") or []
        steps_text = " | ".join(
            f"{it.get('number', i+1)}. {it.get('title','')}"
            + (f": {', '.join((it.get('points') or [])[:2])}" if it.get('points') else "")
            for i, it in enumerate(items[:7])
        )
        footer = spec.get("footer", "")
        return (
            base +
            f"Steps: {steps_text}. "
            + (f'Footer text: "{footer}". ' if footer else "")
            + _STEPS_STYLES[_style_idx]
        )

    if ig_type == "stats":
        stats = spec.get("stats") or []
        stats_text = " | ".join(
            f"{s.get('value','')}: {s.get('label','')}" for s in stats[:4]
        )
        return (
            base +
            f"Key statistics: {stats_text}. "
            + _STATS_STYLES[_style_idx]
        )

    if ig_type == "checklist":
        items = spec.get("items") or []
        items_text = " | ".join(f"{it}" for it in items[:8])
        return (
            base +
            f"Checklist items: {items_text}. "
            + _CHECKLIST_STYLES[_style_idx]
        )

    if ig_type == "bar_chart":
        bars = spec.get("bars") or []
        bars_text = " | ".join(
            f"{b.get('label','')}: {b.get('value','')}{b.get('unit','')}"
            for b in bars[:6]
        )
        return (
            base +
            f"Chart data: {bars_text}. "
            + _BARCHART_STYLES[_style_idx]
        )

    return base + "Modern professional infographic layout with clear sections and bold typography."


# ── Shared workflow ────────────────────────────────────────────────────────────

def run_workflow(url: str, output_dir: Path,
                 wf_fallback=None, collection_id_fallback: str = None,
                 item_id_fallback: str = None,
                 client_slug: str = "") -> tuple:
    """
    Runs steps 1–4: fetch → analyze → describe → generate images.
    Returns (title, image_urls, results, alt_texts).
    If wf_fallback is provided and URL returns 404, falls back to CMS API (draft pages).
    """
    # Step 1: Fetch
    with st.status("Fetching blog page...", expanded=True) as s:
        try:
            title, content, image_urls = fetch_blog(url)
            st.write(f"**Title:** {title}")
            st.write(f"**Content:** {len(content):,} characters")
            st.write(f"**Images found:** {len(image_urls)}")
            s.update(label="Blog fetched ✓", state="complete")
        except requests.HTTPError as e:
            if wf_fallback and str(e.response.status_code) == "404":
                st.info("🔒 Page is a draft — fetching content from Webflow CMS instead...")
                slug = url.rstrip("/").split("/")[-1]
                try:
                    title, content, image_urls = fetch_blog_from_cms(
                        slug, wf_fallback, collection_id_fallback, item_id_fallback)
                    st.write(f"**Title:** {title}")
                    st.write(f"**Content:** {len(content):,} characters (from CMS)")
                    st.write(f"**Images found:** {len(image_urls)}")
                    s.update(label="Blog fetched from CMS ✓", state="complete")
                except Exception as e2:
                    s.update(label="Failed to fetch from CMS", state="error")
                    st.error(str(e2))
                    st.stop()
            else:
                s.update(label="Failed to fetch blog", state="error")
                st.error(str(e))
                st.stop()
        except Exception as e:
            s.update(label="Failed to fetch blog", state="error")
            st.error(str(e))
            st.stop()

    # Step 2: Analyze existing images
    st.subheader("Existing Image Analysis")
    image_infos = []
    prog = st.progress(0, text="Analyzing existing images...")
    container = st.container()

    for idx, img_url in enumerate(image_urls):
        prog.progress((idx + 1) / len(image_urls),
                      text=f"Analyzing image {idx + 1} of {len(image_urls)}...")
        try:
            b = download_image_bytes(img_url)
            info = get_image_info(b, img_url)
            info.update(analyze_image_quality(b))
            info["url"] = img_url
            image_infos.append(info)
        except Exception as e:
            image_infos.append({"url": img_url, "width": 0, "height": 0,
                                 "format": "?", "size_kb": 0,
                                 "quality": "unknown", "is_ai_generated": None,
                                 "issues": [str(e)]})
    prog.empty()

    with container:
        cols = st.columns([1, 2, 1, 1, 2])
        for hdr, col in zip(["**#**", "**Dimensions**", "**Size**", "**Quality**", "**Issues**"], cols):
            col.markdown(hdr)
        st.divider()
        for i, info in enumerate(image_infos, 1):
            cols = st.columns([1, 2, 1, 1, 2])
            cols[0].write(f"Image {i}")
            cols[1].write(f"{info['width']}×{info['height']} {info['format']}" if info["width"] else info["format"])
            cols[2].write(f"{info['size_kb']} KB")
            q = info.get("quality", "unknown")
            badge = {"good": "🟢 Good", "fair": "🟡 Fair", "poor": "🔴 Poor"}.get(q, "⚪ Unknown")
            cols[3].write(f"{badge}{' · AI' if info.get('is_ai_generated') else ''}")
            issues = info.get("issues", [])
            cols[4].write(", ".join(issues) if issues else "—")

    target_w, target_h = get_target_dimensions(image_infos)
    size_label = "1500×844px (16:9)"
    st.caption(f"New images will be generated at **{size_label}**")

    count = len(image_urls) or 4  # use actual detected count, default 4 if none found

    # Step 3a: Plan image slots (photos + infographics)
    with st.status(f"Planning {count} image slots...", expanded=True) as _st:
        try:
            slots = _plan_image_slots(title, content, count)
            n_ig = sum(1 for sl in slots if sl.get("type") == "infographic")
            lbl = (f"Slots planned ✓ — {count - n_ig} photo{'s' if count - n_ig != 1 else ''}"
                   + (f", {n_ig} infographic{'s' if n_ig != 1 else ''}" if n_ig else ""))
            _st.update(label=lbl, state="complete")
        except Exception as e:
            _st.update(label="Slot planning failed", state="error")
            st.error(str(e))
            st.stop()

    # Step 3b: Alt texts
    with st.status("Generating alt texts...", expanded=True) as _st:
        alt_texts = []
        for i, sl in enumerate(slots, 1):
            if sl.get("type") == "infographic":
                alt = sl.get("title") or f"Infographic {i}"
            else:
                alt = generate_alt_text_for(sl.get("description", ""), title)
                st.write(f"[{i}] {alt}")
                time.sleep(1)
            alt_texts.append(alt)
        _st.update(label="Alt texts ready ✓", state="complete")

    (output_dir / "prompts_and_alt.txt").write_text(
        "\n\n".join(
            f"Image {i}:\nType: {sl.get('type','photo')}\n"
            f"Prompt: {sl.get('description', sl.get('title',''))}\nAlt: {a}"
            for i, (sl, a) in enumerate(zip(slots, alt_texts), 1)
        ),
        encoding="utf-8"
    )
    (output_dir / "metadata.json").write_text(
        json.dumps({
            "image_urls": image_urls,
            "alt_texts": alt_texts,
            "slot_types": [sl.get("type", "photo") for sl in slots],
        }),
        encoding="utf-8"
    )

    # Step 4: Generate images
    st.subheader("Generated Images")
    gen_prog = st.progress(0, text="Starting image generation...")
    results = []
    img_seeds = [random.randint(10000, 999999) for _ in slots]

    # Branding for infographics — color from client overlay PNG, logo from page URL
    _has_infographics = any(sl.get("type") == "infographic" for sl in slots)
    _brand_color, _brand_logo = "#1A3A5C", None
    if _has_infographics:
        with st.spinner("Detecting brand colors and logo from page..."):
            scraped_color, _brand_logo = _scrape_page_branding(url)
        # Always use scraped color from website — more accurate than overlay PNG extraction
        if scraped_color and scraped_color != "#1A3A5C":
            _brand_color = scraped_color
        elif client_slug:
            _brand_color = _client_branding(client_slug)  # fallback: overlay PNG

    for i, (sl, alt) in enumerate(zip(slots, alt_texts), 1):
        gen_prog.progress(i / len(slots), text=f"Generating image {i} of {len(slots)}...")

        if sl.get("type") == "infographic":
            ig_label = sl.get("infographic_type", "").upper()
            st.write(f"🎨 Generating infographic [{ig_label}]: **{sl.get('title', '')}** (brand: `{_brand_color}`)")
            ig_prompt = _infographic_prompt(sl, _brand_color)
            last_err = None
            final_bytes, final_ext = None, "jpg"
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    raw = generate_image_live(ig_prompt, i, target_w, target_h)
                    raw = _apply_corner_logo(raw, _brand_logo)
                    opt_bytes, ext = optimize_image(raw, max_kb=200)
                    final_bytes, final_ext = opt_bytes, ext
                    break
                except Exception as e:
                    last_err = e
                    if attempt < MAX_ATTEMPTS:
                        st.warning(f"⚠️ Infographic attempt {attempt} failed ({e}), retrying...")
                        time.sleep(2)
            if final_bytes:
                path = output_dir / f"image_{i:02d}.{final_ext}"
                path.write_bytes(final_bytes)
            results.append({"index": i, "bytes": final_bytes, "ext": final_ext,
                             "size_kb": round(len(final_bytes) / 1024, 1) if final_bytes else 0,
                             "alt": alt, "prompt": ig_prompt, "type": "infographic",
                             "status": "ok" if final_bytes else f"failed: {last_err}",
                             "defect_reason": ""})
            continue

        # Normal office photo via Kie API
        prompt = sl.get("description", "")
        last_err = None
        final_bytes, final_ext, final_defect = None, "jpg", ""
        base_seed = img_seeds[i - 1]

        for attempt in range(1, MAX_ATTEMPTS + 1):
            alabel = f"Image {i}" + (f" — attempt {attempt}/{MAX_ATTEMPTS}" if attempt > 1 else "")
            try:
                raw = _dispatch_image_gen(prompt, i, target_w, target_h,
                                          seed=base_seed + attempt * 1000)
                is_ok, reason = check_anatomy(raw)
                if not is_ok and attempt < MAX_ATTEMPTS:
                    st.warning(f"⚠️ {alabel} — defect detected ({reason}), regenerating...")
                    continue
                opt_bytes, ext = optimize_image(raw, max_kb=200)
                final_bytes, final_ext = opt_bytes, ext
                final_defect = "" if is_ok else reason
                if not is_ok:
                    st.warning(f"⚠️ Image {i} — kept after {MAX_ATTEMPTS} attempts ({reason})")
                break
            except Exception as e:
                last_err = e
                if attempt < MAX_ATTEMPTS:
                    st.warning(f"⚠️ {alabel} failed ({e}), retrying...")
                    time.sleep(2)

        if final_bytes:
            path = output_dir / f"image_{i:02d}.{final_ext}"
            path.write_bytes(final_bytes)
            results.append({"index": i, "bytes": final_bytes, "ext": final_ext,
                             "size_kb": round(len(final_bytes) / 1024, 1),
                             "alt": alt, "prompt": prompt, "status": "ok",
                             "defect_reason": final_defect})
        else:
            results.append({"index": i, "bytes": None, "ext": "jpg", "size_kb": 0,
                             "alt": alt, "prompt": prompt,
                             "status": f"failed: {last_err}", "defect_reason": ""})
    gen_prog.empty()

    return title, image_urls, results, alt_texts


def display_image_grid(results: list, key_prefix: str = ""):
    """Render a 2-column grid of images with alt text + download buttons."""
    for row in range(0, len(results), 2):
        cols = st.columns(2)
        for ci, result in enumerate(results[row:row + 2]):
            with cols[ci]:
                i = result["index"]
                if result["bytes"]:
                    fname = f"image_{i:02d}.{result['ext']}"
                    st.image(result["bytes"], caption=f"{fname} — {result['size_kb']} KB",
                             use_container_width=True)
                    st.text_area(f"Alt text #{i}", value=result["alt"], height=70,
                                 key=f"{key_prefix}alt_{i}")
                    st.download_button(
                        label=f"⬇ Download {fname}", data=result["bytes"],
                        file_name=fname, mime=MIME_MAP.get(result["ext"], "image/jpeg"),
                        key=f"{key_prefix}dl_{i}", use_container_width=True
                    )
                else:
                    st.error(f"Image {i} failed: {result['status']}")


# ── UI ─────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="MSP Launchpad — Blog Image Generator",
                   page_icon="assets/logo.png", layout="centered")

# ── Branding: load logo as base64 ─────────────────────────────────────────────
_logo_path = Path("assets/logo.png")
_logo_b64 = ""
if _logo_path.exists():
    _logo_b64 = base64.b64encode(_logo_path.read_bytes()).decode()

st.markdown(f"""
<style>
/* ── Global ── */
html, body, [data-testid="stApp"] {{
    background-color: #0D0D0D;
}}
/* ── Branded top header ── */
.msp-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 0 20px 0;
    border-bottom: 1px solid #2a2a2a;
    margin-bottom: 20px;
}}
.msp-header .app-label {{
    font-size: 22px;
    font-weight: 700;
    color: #FFFFFF;
    letter-spacing: -0.01em;
}}
/* ── Primary buttons → cyan ── */
.stButton > button[kind="primary"] {{
    background: #35EDED !important;
    color: #000 !important;
    border: none !important;
    font-weight: 600 !important;
    border-radius: 6px !important;
}}
.stButton > button[kind="primary"]:hover {{
    background: #20d0d0 !important;
}}
/* ── Secondary buttons ── */
.stButton > button[kind="secondary"] {{
    border-color: #35EDED !important;
    color: #35EDED !important;
}}
/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {{
    border-bottom: 2px solid #1A1A1A;
}}
.stTabs [aria-selected="true"] {{
    color: #35EDED !important;
    border-bottom-color: #35EDED !important;
}}
/* ── Info/success boxes ── */
.stAlert[data-baseweb="notification"] {{
    border-left-color: #35EDED;
}}
/* ── Dividers ── */
hr {{ border-color: #2a2a2a; }}
/* ── Hide password show/hide toggle on all inputs ── */
[data-baseweb="base-input"] button {{
    display: none !important;
}}
</style>
{"<div class='msp-header'><img src='data:image/png;base64," + _logo_b64 + "' style='height:24px'><span class='app-label'>Blog Image Generator</span></div>" if _logo_b64 else ""}
""", unsafe_allow_html=True)

if not KIE_API_KEY:
    st.error("🔴 **KIE_API_KEY missing** — add it to .env to enable image generation.")
else:
    st.success(f"🟢 **Live Mode** — Text: Gemini 2.0 Flash | Images: **Kie API** (GPT Image 2 → Grok Imagine) | KIE key: `...{KIE_API_KEY[-6:]}`")

# ── Model selector (for testing individual models) ────────────────────────────
_model_opts = {
    "🤖 Auto (GPT Image 2 → Grok Imagine)": "auto",
    "🎨 GPT Image 2 (best quality)":        "gpt2",
    "⚡ Grok Imagine (fallback)":            "grok",
}
_model_label = st.radio(
    "🧪 Model (select to test a specific one):",
    list(_model_opts.keys()),
    horizontal=True,
    key="model_test_radio",
)
st.session_state["_model_choice"] = _model_opts[_model_label]

tab_manual, tab_auto = st.tabs(["📥  Manual Upload", "🚀  Auto Upload to Webflow"])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — Manual Upload (generate content images only — no compositing)
# ════════════════════════════════════════════════════════════════════════════════
with tab_manual:
    st.subheader("Generate Images")
    st.caption("Paste a blog URL — the app scans the page, generates matching images, then you download and upload them yourself.")

    m_url = st.text_input("Blog URL", placeholder="https://www.example.com/blog/your-post",
                          key="m_url")
    m_btn = st.button("Generate Images", type="primary",
                      use_container_width=True, key="m_btn")

    # ── Handle per-image redo (triggered by redo buttons below) ───────────────
    if st.session_state.get("m_redo_idx") is not None and "m_results" in st.session_state:
        redo_i    = st.session_state.pop("m_redo_idx")
        redo_seed = st.session_state.pop("m_redo_seed", random.randint(10000, 999999))
        redo_slots = st.session_state.get("m_slots", [])
        redo_slot  = redo_slots[redo_i - 1] if redo_i <= len(redo_slots) else {"type": "photo", "description": ""}
        redo_alt   = st.session_state["m_alt_texts"][redo_i - 1]
        with st.spinner(f"Regenerating image {redo_i}..."):
            try:
                if redo_slot.get("type") == "infographic":
                    raw = _render_infographic(redo_slot, DEFAULT_WIDTH, DEFAULT_HEIGHT)
                    new_desc = redo_slot.get("title", "")
                else:
                    original_desc = redo_slot.get("description", "")
                    redo_title = st.session_state.get("m_title", "")
                    new_desc = generate_prompt_variation(original_desc, redo_title)
                    raw = _dispatch_image_gen(
                        new_desc, redo_i,
                        DEFAULT_WIDTH, DEFAULT_HEIGHT, seed=redo_seed
                    )
                opt_bytes, ext = optimize_image(raw, max_kb=200)
                st.session_state["m_results"][redo_i - 1] = {
                    "index": redo_i, "bytes": opt_bytes, "ext": ext,
                    "size_kb": round(len(opt_bytes) / 1024, 1),
                    "alt": redo_alt,
                    "prompt": new_desc,
                    "status": "ok", "defect_reason": "",
                }
            except Exception as e:
                st.error(f"Redo failed: {e}")

    # ── Full generation on button click ───────────────────────────────────────
    if m_btn:
        if not m_url.strip():
            st.error("Please enter a blog URL.")
            st.stop()
        # Clear previous results when starting a new generation
        for k in ["m_results", "m_slots", "m_alt_texts", "m_title"]:
            st.session_state.pop(k, None)

        url = ("https://" + m_url) if not m_url.startswith("http") else m_url
        slug = url.rstrip("/").split("/")[-1]

        # Step 1: Fetch blog
        with st.status("Fetching blog page...", expanded=True) as s:
            try:
                title, content, image_urls = fetch_blog(url)
                count = len(image_urls) or 4  # use actual detected count, default 4 if none found
                st.write(f"**Title:** {title}")
                detected_note = " (lazy-loaded images may not be detected)" if len(image_urls) < count else ""
                st.write(f"**Images detected on page:** {len(image_urls)} → generating **{count}**{detected_note}")
                s.update(label="Blog fetched ✓", state="complete")
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    s.update(label="Page not found (404)", state="error")
                    st.error(
                        "**Page not found (404).** This usually means the blog post is "
                        "still a **draft / unpublished** in Webflow.\n\n"
                        "**Options:**\n"
                        "- Publish the post in Webflow first, then try again.\n"
                        "- Or use the **Auto Upload tab** — it can access draft pages "
                        "using your Webflow API key."
                    )
                else:
                    s.update(label="Failed to fetch blog", state="error")
                    st.error(f"HTTP error: {e}")
                st.stop()
            except Exception as e:
                s.update(label="Failed to fetch blog", state="error")
                st.error(f"Could not load the page: {e}")
                st.stop()

        # Step 2: Plan image slots (photos + infographics)
        with st.status(f"Planning {count} image slots...", expanded=True) as _ms:
            try:
                slots = _plan_image_slots(title, content, count)
                n_ig = sum(1 for sl in slots if sl.get("type") == "infographic")
                lbl = (f"Slots planned ✓ — {count - n_ig} photo{'s' if count - n_ig != 1 else ''}"
                       + (f", {n_ig} infographic{'s' if n_ig != 1 else ''}" if n_ig else ""))
                _ms.update(label=lbl, state="complete")
            except Exception as e:
                _ms.update(label="Slot planning failed", state="error")
                st.error(str(e))
                st.stop()

        # Step 3: Alt texts
        with st.status("Generating alt texts...", expanded=True) as _ms:
            alt_texts = []
            for i, sl in enumerate(slots, 1):
                if sl.get("type") == "infographic":
                    alt = sl.get("title") or f"Infographic {i}"
                else:
                    alt = generate_alt_text_for(sl.get("description", ""), title)
                    time.sleep(1)
                alt_texts.append(alt)
            _ms.update(label="Alt texts ready ✓", state="complete")

        # Step 4: Generate images
        st.subheader("Generated Images")
        gen_prog = st.progress(0, text="Starting image generation...")
        results  = []
        img_seeds = [random.randint(10000, 999999) for _ in slots]

        # Branding for Manual tab — scrape from URL
        _m_has_ig = any(sl.get("type") == "infographic" for sl in slots)
        _m_brand_color, _m_brand_logo = "#1A3A5C", None
        if _m_has_ig:
            with st.spinner("Detecting brand colors from page..."):
                _m_brand_color, _m_brand_logo = _scrape_page_branding(url)

        for i, (sl, alt) in enumerate(zip(slots, alt_texts), 1):
            gen_prog.progress(i / len(slots), text=f"Generating image {i} of {len(slots)}...")

            if sl.get("type") == "infographic":
                ig_label = sl.get("infographic_type", "").upper()
                st.write(f"🎨 Generating infographic [{ig_label}]: **{sl.get('title', '')}** (brand: `{_m_brand_color}`)")
                ig_prompt = _infographic_prompt(sl, _m_brand_color)
                last_err = None
                final_bytes, final_ext = None, "jpg"
                for attempt in range(1, MAX_ATTEMPTS + 1):
                    try:
                        raw = generate_image_live(ig_prompt, i, DEFAULT_WIDTH, DEFAULT_HEIGHT)
                        raw = _apply_corner_logo(raw, _m_brand_logo)
                        opt_bytes, ext = optimize_image(raw, max_kb=200)
                        final_bytes, final_ext = opt_bytes, ext
                        break
                    except Exception as e:
                        last_err = e
                        if attempt < MAX_ATTEMPTS:
                            st.warning(f"⚠️ Infographic attempt {attempt} failed ({e}), retrying...")
                            time.sleep(2)
                results.append({
                    "index": i, "bytes": final_bytes, "ext": final_ext,
                    "size_kb": round(len(final_bytes) / 1024, 1) if final_bytes else 0,
                    "alt": alt, "prompt": ig_prompt,
                    "status": "ok" if final_bytes else f"failed: {last_err}",
                    "defect_reason": "",
                })
                continue

            # Normal office photo via Kie API
            prompt = sl.get("description", "")
            last_err = None
            final_bytes, final_ext = None, "jpg"
            base_seed = img_seeds[i - 1]

            for attempt in range(1, MAX_ATTEMPTS + 1):
                alabel = f"Image {i}" + (f" — attempt {attempt}/{MAX_ATTEMPTS}" if attempt > 1 else "")
                try:
                    raw = _dispatch_image_gen(prompt, i, DEFAULT_WIDTH, DEFAULT_HEIGHT,
                                              seed=base_seed + attempt * 1000)
                    is_ok, reason = check_anatomy(raw)
                    if not is_ok and attempt < MAX_ATTEMPTS:
                        st.warning(f"⚠️ {alabel} — defect detected ({reason}), regenerating...")
                        continue
                    opt_bytes, ext = optimize_image(raw, max_kb=200)
                    final_bytes, final_ext = opt_bytes, ext
                    if not is_ok:
                        st.warning(f"⚠️ Image {i} — kept after {MAX_ATTEMPTS} attempts ({reason})")
                    break
                except Exception as e:
                    last_err = e
                    if attempt < MAX_ATTEMPTS:
                        st.warning(f"⚠️ {alabel} failed ({e}), retrying...")
                        time.sleep(2)

            results.append({
                "index": i,
                "bytes": final_bytes,
                "ext":   final_ext,
                "size_kb": round(len(final_bytes) / 1024, 1) if final_bytes else 0,
                "alt":   alt,
                "prompt": prompt,
                "status": "ok" if final_bytes else f"failed: {last_err}",
                "defect_reason": "",
            })

        gen_prog.empty()

        # Persist in session state so redo works without re-fetching
        st.session_state["m_results"]   = results
        st.session_state["m_slots"]     = slots
        st.session_state["m_alt_texts"] = alt_texts
        st.session_state["m_title"]     = title

    # ── Display results (shown after generation or redo) ──────────────────────
    if "m_results" in st.session_state:
        results = st.session_state["m_results"]
        for row in range(0, len(results), 2):
            cols = st.columns(2)
            for ci, result in enumerate(results[row:row + 2]):
                with cols[ci]:
                    i = result["index"]
                    if result["bytes"]:
                        fname = f"image_{i:02d}.{result['ext']}"
                        st.image(result["bytes"],
                                 caption=f"{fname} — {result['size_kb']} KB",
                                 use_container_width=True)
                        st.text_area(f"Alt text #{i}", value=result["alt"],
                                     height=70, key=f"m_alt_{i}")
                        dl_c, redo_c = st.columns([3, 1])
                        with dl_c:
                            st.download_button(
                                label=f"⬇ Download {fname}",
                                data=result["bytes"],
                                file_name=fname,
                                mime=MIME_MAP.get(result["ext"], "image/jpeg"),
                                key=f"m_dl_{i}",
                                use_container_width=True,
                            )
                        with redo_c:
                            if st.button("🔄 Redo", key=f"m_redo_{i}",
                                         use_container_width=True,
                                         help="Regenerate this image"):
                                st.session_state["m_redo_idx"]  = i
                                st.session_state["m_redo_seed"] = random.randint(10000, 999999)
                                st.rerun()
                    else:
                        st.error(f"Image {i} failed: {result['status']}")
                        if st.button("🔄 Retry", key=f"m_retry_{i}", use_container_width=True):
                            st.session_state["m_redo_idx"]  = i
                            st.session_state["m_redo_seed"] = random.randint(10000, 999999)
                            st.rerun()

        ok = sum(1 for r in results if r["status"] == "ok")
        st.success(f"Done! {ok}/{len(results)} images ready — download each one and upload to your blog.")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — Auto Upload to Webflow
# ════════════════════════════════════════════════════════════════════════════════
def do_webflow_connect(api_key: str, manual_site_id: str, client_name: str, slug: str,
                       blog_url: str = ""):
    """Connect to Webflow and find the blog post. Returns (wf, site_id, site_name,
    collection_id, item_id, was_published) or raises on failure."""
    wf = WebflowClient(api_key)

    if manual_site_id:
        site_id = manual_site_id
        site_name = client_name or site_id
        st.write(f"**Site ID:** {site_id}")
    else:
        sites = wf.get_sites()
        if not sites:
            raise ValueError("No Webflow sites found for this API key.")

        # Try to match the blog URL's domain to the correct Webflow site.
        # Falls back to sites[0] if no domain match is found.
        site = sites[0]
        if blog_url and len(sites) > 1:
            from urllib.parse import urlparse
            target_domain = urlparse(blog_url).netloc.lower().removeprefix("www.")
            for s in sites:
                # Check displayName, shortName, and customDomains
                s_name = (s.get("displayName", "") + s.get("shortName", "")).lower()
                s_domains = [d.get("url", "").lower().removeprefix("www.")
                             for d in s.get("customDomains", [])]
                if target_domain in s_name or target_domain in s_domains:
                    site = s
                    break

        site_id   = site["id"]
        site_name = site.get("displayName", site_id)
        all_names = ", ".join(s.get("displayName", s["id"]) for s in sites)
        st.write(f"**Site:** {site_name}  _(API key has access to: {all_names})_")

    collection = wf.find_blog_collection(site_id)
    if not collection:
        raise ValueError("No blog collection found.")
    collection_id = collection["id"]
    st.write(f"**Collection:** {collection.get('displayName', collection_id)}")

    item = wf.find_item_by_slug(collection_id, slug)
    if not item:
        # Fetch a sample of slugs from the collection to help diagnose the mismatch
        try:
            sample_data  = wf._get(f"/collections/{collection_id}/items", params={"limit": 10})
            sample_slugs = [i.get("fieldData", {}).get("slug", "?")
                            for i in sample_data.get("items", [])]
            hint = f"\n\nFirst 10 slugs in collection: `{'`, `'.join(sample_slugs)}`"
        except Exception:
            hint = ""
        raise ValueError(f"Blog post with slug '{slug}' not found in CMS.{hint}")

    item_id      = item["id"]
    was_published = wf.is_published(item)
    st.write(f"**Post found:** {item.get('fieldData', {}).get('name', slug)}")
    st.write(f"**Status:** {'🟢 Published' if was_published else '🟡 Draft'}")

    return wf, site_id, site_name, collection_id, item_id, was_published


def do_webflow_upload(wf, site_id, collection_id, item_id, was_published,
                      image_urls, ok_results, site_name, client_name,
                      main_bytes=None, thumb_bytes=None, blog_title=""):
    """Upload assets, update CMS, publish. Returns list of new asset URLs."""
    # ── Upload assets ─────────────────────────────────────────────────────────
    with st.status("Uploading images to Webflow...", expanded=True) as s:
        try:
            new_urls = []
            for r in ok_results:
                i = r["index"]
                fname = f"image_{i:02d}.{r['ext']}"
                st.write(f"Uploading {fname} ({r['size_kb']} KB)...")
                asset_url = wf.upload_asset(site_id, r["bytes"], fname)
                new_urls.append(asset_url)
                st.write(f"  ✓ {asset_url}")
            s.update(label=f"Uploaded {len(new_urls)} image(s) ✓", state="complete")
        except Exception as e:
            s.update(label="Upload failed", state="error")
            st.error(str(e))
            st.stop()

    # ── Update CMS item ───────────────────────────────────────────────────────
    with st.status("Updating Webflow CMS...", expanded=True) as s:
        try:
            full_item = wf._get(f"/collections/{collection_id}/items/{item_id}")
            field_data = full_item.get("fieldData", {})

            # Debug: show all field types so we can see the CMS structure
            field_summary = {k: type(v).__name__ + (f"[{len(v)} chars, has <img>]"
                             if isinstance(v, str) and "<img" in v
                             else f"[{len(v)} chars]" if isinstance(v, str)
                             else "") for k, v in field_data.items()}
            st.write("**CMS fields found:**")
            for k, summary in field_summary.items():
                st.write(f"  `{k}`: {summary}")

            richtext_field = None
            richtext_key = None
            for key, val in field_data.items():
                if isinstance(val, str) and "<img" in val:
                    richtext_field = val
                    richtext_key = key
                    break

            if richtext_field and richtext_key:
                new_html, replaced = wf.replace_images_in_richtext(
                    richtext_field,
                    image_urls[:len(new_urls)],
                    new_urls,
                    [r["alt"] for r in ok_results]
                )
                wf.update_item(collection_id, item_id, {richtext_key: new_html})
                st.write(f"Updated rich text field `{richtext_key}` — {replaced} image(s) replaced")
            else:
                image_fields = {k: v for k, v in field_data.items()
                                if isinstance(v, dict) and "url" in v}
                img_keys = list(image_fields.keys())[:len(new_urls)]
                update_data = {}
                for idx, (key, new_url) in enumerate(zip(img_keys, new_urls)):
                    update_data[key] = {"url": new_url, "alt": ok_results[idx]["alt"]}
                if update_data:
                    wf.update_item(collection_id, item_id, update_data)
                    st.write(f"Updated {len(update_data)} image field(s)")
                else:
                    st.warning(
                        "⚠️ No image fields detected in CMS item. "
                        "The blog images may be stored differently in Webflow. "
                        "Check the field list above — paste it here so we can fix the detection."
                    )
            s.update(label="CMS updated ✓", state="complete")
        except Exception as e:
            s.update(label="CMS update failed", state="error")
            st.error(str(e))
            st.stop()

    # ── Upload main image + thumbnail ─────────────────────────────────────────
    if main_bytes or thumb_bytes:
        with st.status("Uploading main image & thumbnail...", expanded=True) as s:
            try:
                fd2 = wf._get(f"/collections/{collection_id}/items/{item_id}").get("fieldData", {})

                # Show ALL field keys so we can debug naming mismatches
                st.write(f"**All CMS fields:** {list(fd2.keys())}")

                # Image-type fields: populated (dict with "url") + null/None (never-set image slots)
                # Exclude plain strings, booleans, numbers, lists — those are not image fields
                img_fields = {k: v for k, v in fd2.items()
                              if v is None or (isinstance(v, dict) and "url" in v)}
                st.write(f"**Image-type fields found:** {list(img_fields.keys()) or 'none'}")

                def _find_field(fields, keywords):
                    """Match field name against keywords. Step 1: exact. Step 2: substring."""
                    hit = next((k for k in fields if k.lower() in keywords), None)
                    if hit: return hit
                    return next((k for k in fields if any(kw in k.lower() for kw in keywords)), None)

                def _to_jpeg(png_bytes: bytes) -> tuple[bytes, str]:
                    buf = io.BytesIO()
                    PILImage.open(io.BytesIO(png_bytes)).convert("RGB").save(buf, format="JPEG", quality=90)
                    return buf.getvalue(), "jpg"

                THUMB_KW = ["thumbnail-image", "thumbnail_image", "thumbnail", "thumb",
                            "card-image", "card-thumbnail", "card", "preview", "list-image"]
                MAIN_KW  = ["main-image", "main_image", "hero-image", "featured-image",
                            "post-image", "blog-image", "cover-image", "og-image",
                            "main", "hero", "featured", "cover", "post-main"]

                img_update = {}

                # ── Strategy: find thumbnail first, then main is "everything else" ──
                # This prevents both fields from accidentally matching the same key,
                # and handles field names that don't fit our keyword list.

                tkey = _find_field(img_fields, THUMB_KW) if thumb_bytes else None

                # Main: keyword match first, but EXCLUDE tkey to avoid collision
                main_candidates = {k: v for k, v in img_fields.items() if k != tkey}
                mkey = _find_field(main_candidates, MAIN_KW) if main_bytes else None
                # Fallback: if no keyword match, use first remaining image field
                if main_bytes and not mkey and main_candidates:
                    mkey = next(iter(main_candidates))
                    st.write(f"ℹ️ Main image: no keyword match — using `{mkey}` as fallback")

                st.write(f"**Matched → main:** `{mkey or 'NOT FOUND'}` | **thumb:** `{tkey or 'NOT FOUND'}`")

                if main_bytes:
                    if mkey:
                        jbytes, ext = _to_jpeg(main_bytes)
                        murl = wf.upload_asset(site_id, jbytes, f"main_image.{ext}")
                        img_update[mkey] = {"url": murl, "alt": blog_title}
                        st.write(f"✓ Main image uploaded → `{mkey}`")
                    else:
                        st.warning(f"⚠️ No image field found for main image. Fields: {list(img_fields.keys())}")

                if thumb_bytes:
                    if tkey:
                        jbytes, ext = _to_jpeg(thumb_bytes)
                        turl = wf.upload_asset(site_id, jbytes, f"thumbnail.{ext}")
                        img_update[tkey] = {"url": turl, "alt": blog_title}
                        st.write(f"✓ Thumbnail uploaded → `{tkey}`")
                    else:
                        st.warning(f"⚠️ No image field found for thumbnail. Fields: {list(img_fields.keys())}")

                if img_update:
                    wf.update_item(collection_id, item_id, img_update)
                    st.write(f"CMS updated: {list(img_update.keys())}")

                s.update(label="Main image & thumbnail uploaded ✓", state="complete")
            except Exception as e:
                s.update(label="Main/thumbnail upload failed", state="error")
                st.error(str(e))

    # ── Publish ───────────────────────────────────────────────────────────────
    with st.status("Checking publish status...", expanded=True) as s:
        try:
            if was_published:
                wf.publish_items(collection_id, [item_id])
                st.write("Page was published → re-published with new images ✓")
                s.update(label="Re-published ✓", state="complete")
            else:
                st.write("Page was draft → kept as draft ✓")
                s.update(label="Kept as draft ✓", state="complete")
        except Exception as e:
            s.update(label="Publish step failed", state="error")
            st.error(str(e))

    st.divider()
    status_label = "published" if was_published else "draft"
    st.success(
        f"✅ Done! {len(new_urls)} image(s) uploaded to Webflow "
        f"({client_name or site_name} — {status_label})."
    )
    return new_urls


def _regen_image(blog_state: dict, img_idx: int):
    """Regenerate a single image with a fresh prompt variation. Mutates blog_state in place."""
    result = next((r for r in blog_state["results"] if r["index"] == img_idx), None)
    if not result:
        return
    output_dir = Path("generated_images") / blog_state["slug"]
    with st.status(f"Regenerating image {img_idx} — {blog_state['slug']}...",
                   expanded=True) as s:
        # Generate a new prompt variation instead of reusing the same one
        st.write("Generating new image description...")
        new_prompt = generate_prompt_variation(result["prompt"], blog_state.get("title", ""))
        st.write(f"**New description:** {new_prompt[:120]}{'...' if len(new_prompt) > 120 else ''}")

        new_seed = random.randint(10000, 999999)
        final_bytes, final_ext, final_defect = None, "jpg", ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw = _dispatch_image_gen(new_prompt, img_idx,
                                          DEFAULT_WIDTH, DEFAULT_HEIGHT,
                                          seed=new_seed + attempt * 1000)
                is_ok, reason = check_anatomy(raw)
                if not is_ok and attempt < MAX_ATTEMPTS:
                    st.write(f"Attempt {attempt}: {reason} — retrying...")
                    continue
                opt_bytes, ext = optimize_image(raw, max_kb=200)
                final_bytes, final_ext = opt_bytes, ext
                final_defect = "" if is_ok else reason
                break
            except Exception as e:
                if attempt == MAX_ATTEMPTS:
                    st.error(f"Failed: {e}")
        if final_bytes:
            (output_dir / f"image_{img_idx:02d}.{final_ext}").write_bytes(final_bytes)
            st.write("Generating new alt text...")
            new_alt = generate_alt_text_for(new_prompt, blog_state.get("title", ""))
            result.update({"bytes": final_bytes, "ext": final_ext,
                           "size_kb": round(len(final_bytes) / 1024, 1),
                           "status": "ok", "defect_reason": final_defect,
                           "prompt": new_prompt, "alt": new_alt})
            s.update(label=f"Image {img_idx} regenerated ✓", state="complete")


def _regen_main_thumb(blog_state: dict):
    """Generate a fresh background photo and re-composite main + thumbnail. Mutates blog_state."""
    title = blog_state.get("title", "")
    results = blog_state.get("results", [])
    photo_results = [r for r in results if r.get("type") != "infographic"]
    base_prompt = photo_results[0]["prompt"] if photo_results else (results[0]["prompt"] if results else title)

    with st.status(f"Regenerating main & thumbnail — {blog_state['slug']}...", expanded=True) as s:
        st.write("Generating new image description...")
        new_prompt = generate_prompt_variation(base_prompt, title)
        st.write(f"**New description:** {new_prompt[:120]}{'...' if len(new_prompt) > 120 else ''}")

        new_seed = random.randint(10000, 999999)
        try:
            raw = _dispatch_image_gen(new_prompt, 1, DEFAULT_WIDTH, DEFAULT_HEIGHT, seed=new_seed)

            client = blog_state.get("client", "")
            if client:
                m_logo, t_logo = ensure_figma_assets_for_client(client)
                m_tpl, t_tpl = make_tpls(client, m_logo, t_logo)
            else:
                ensure_figma_assets()
                m_tpl, t_tpl = MAIN_TPL, THUMB_TPL
            new_main  = composite_template(raw, title, m_tpl)
            new_thumb = composite_template(raw, title, t_tpl)
            if new_main:
                blog_state["main_bytes"] = new_main
            if new_thumb:
                blog_state["thumb_bytes"] = new_thumb
            if new_main or new_thumb:
                s.update(label="Main + thumbnail regenerated ✓", state="complete")
            else:
                s.update(label="Compositing failed — old images kept", state="error")
        except Exception as e:
            s.update(label="Regeneration failed", state="error")
            st.error(str(e))


def _render_blog_results(blog_state: dict):
    """Render content image grid + main/thumbnail preview."""
    results = blog_state["results"]
    slug = blog_state["slug"]

    for row_start in range(0, len(results), 2):
        cols = st.columns(2)
        for ci, result in enumerate(results[row_start:row_start + 2]):
            with cols[ci]:
                i = result["index"]
                if result["bytes"]:
                    fname = f"image_{i:02d}.{result['ext']}"
                    st.image(result["bytes"], caption=f"{fname} — {result['size_kb']} KB",
                             use_container_width=True)
                    defect = result.get("defect_reason", "")
                    if defect:
                        st.warning(f"⚠️ {defect}")
                    else:
                        st.success("✅ OK")
                    st.text_area("Alt text", value=result["alt"], height=55,
                                 key=f"alt_{slug}_{i}")
                    dl_col, redo_col = st.columns([2, 1])
                    with dl_col:
                        st.download_button("⬇ Download", data=result["bytes"],
                                           file_name=fname,
                                           mime=MIME_MAP.get(result["ext"], "image/jpeg"),
                                           key=f"dl_{slug}_{i}", use_container_width=True)
                    with redo_col:
                        if st.button("🔄 Redo", key=f"redo_{slug}_{i}",
                                     use_container_width=True):
                            st.session_state["regen_req"] = {"slug": slug, "idx": i}
                            st.rerun()
                else:
                    st.error(f"Image {i} failed")
                    if st.button("🔄 Retry", key=f"redo_{slug}_{i}", use_container_width=True):
                        st.session_state["regen_req"] = {"slug": slug, "idx": i}
                        st.rerun()

    # ── Main image + thumbnail preview ────────────────────────────────────────
    main_b = blog_state.get("main_bytes")
    thumb_b = blog_state.get("thumb_bytes")
    if main_b or thumb_b:
        st.divider()
        st.markdown("**Main Image & Thumbnail**")
        col1, col2 = st.columns(2)
        if main_b:
            with col1:
                st.image(main_b, caption="Main Image (920×613)", use_container_width=True)
                dl_c, redo_c = st.columns([2, 1])
                with dl_c:
                    st.download_button("⬇ Download Main", main_b, "main_image.png",
                                       "image/png", key=f"dl_main_{slug}", use_container_width=True)
                with redo_c:
                    if st.button("🔄 Redo", key=f"redo_main_{slug}", use_container_width=True):
                        st.session_state["regen_main_thumb_req"] = {"slug": slug}
                        st.rerun()
        if thumb_b:
            with col2:
                st.image(thumb_b, caption="Thumbnail (736×560)", use_container_width=True)
                dl_c, redo_c = st.columns([2, 1])
                with dl_c:
                    st.download_button("⬇ Download Thumbnail", thumb_b, "thumbnail.png",
                                       "image/png", key=f"dl_thumb_{slug}", use_container_width=True)
                with redo_c:
                    if st.button("🔄 Redo", key=f"redo_thumb_{slug}", use_container_width=True):
                        st.session_state["regen_main_thumb_req"] = {"slug": slug}
                        st.rerun()


with tab_auto:
    st.subheader("Batch Upload to Webflow")
    st.caption("API key is saved per client — switches automatically when you change the template.")

    # ── Figma templates — dropdown + add/delete (shown FIRST so key auto-fills) ──
    _pre_clients = get_figma_clients()

    st.markdown("**Figma Templates**")

    _selected_slug = ""
    if _pre_clients:
        _tpl_labels = [_client_display_name(c) for c in _pre_clients]
        _drop_col, _del_col = st.columns([5, 1])
        with _drop_col:
            _selected_tpl = st.selectbox(
                "tpl_select", _tpl_labels,
                label_visibility="collapsed",
                key="figma_tpl_dropdown",
            )
        with _del_col:
            if st.button("🗑️ Delete", key="del_tpl_btn", use_container_width=True):
                _del_slug = _pre_clients[_tpl_labels.index(_selected_tpl)]
                _cache = _load_node_cache()
                if _del_slug in _cache:
                    del _cache[_del_slug]
                    FIGMA_NODE_CACHE.write_text(
                        json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    st.success(f"Deleted **{_selected_tpl}** ✓")
                    st.rerun()

        _selected_slug = _pre_clients[_tpl_labels.index(_selected_tpl)]
    else:
        st.caption("No templates saved yet — add one below.")

    # ── API key — auto-fills from client cache when dropdown changes ──────────
    # Priority: client cache → .env fallback → empty
    _client_saved_key = _load_node_cache().get(_selected_slug, {}).get("webflow_api_key", "")
    _default_key      = _client_saved_key or os.getenv("WEBFLOW_API_KEY", "")

    # Auto-switch key when client changes, or restore if field was cleared
    _prev_slug = st.session_state.get("_prev_client_slug", "")
    if _selected_slug != _prev_slug:
        st.session_state["_prev_client_slug"] = _selected_slug
        st.session_state["a_key"] = _default_key
    elif not st.session_state.get("a_key") and _default_key:
        st.session_state["a_key"] = _default_key

    key_col, save_col = st.columns([4, 1])
    with key_col:
        a_api_key = st.text_input(
            "Webflow API Key",
            type="password",
            placeholder="Paste key → click 💾 to save for this client",
            key="a_key",
        )
    with save_col:
        if st.button("💾 Save", use_container_width=True, key="save_key_btn",
                     help="Saves key for the selected client template"):
            if a_api_key.strip() and _selected_slug:
                # Save to client cache
                _cache = _load_node_cache()
                _cache[_selected_slug]["webflow_api_key"] = a_api_key.strip()
                _save_node_cache(_cache)
                st.success(f"API key saved for **{_client_display_name(_selected_slug)}** ✓")

    # Add new client template
    _url_col, _btn_col = st.columns([5, 1])
    with _url_col:
        _new_tpl_url = st.text_input(
            "add_tpl", label_visibility="collapsed",
            placeholder="Add new client: paste Figma frame link here",
            key="add_tpl_url",
        )
    with _btn_col:
        if st.button("➕ Add", key="add_tpl_btn", use_container_width=True):
            if _new_tpl_url.strip():
                with st.spinner("Fetching from Figma..."):
                    _ok, _msg, _ = _add_client_from_figma_url(_new_tpl_url.strip())
                if _ok:
                    st.success(_msg)
                    st.rerun()
                else:
                    st.error(_msg)

    a_urls_raw = st.text_area("Blog URLs — one per line",
                               placeholder="https://www.rtcmanaged.com/blog/endpoint-security\n"
                                           "https://www.rtcmanaged.com/blog/cloud-backup\n"
                                           "https://www.rtcmanaged.com/blog/microsoft-365",
                               height=130, key="a_urls")

    btn_col1, btn_col2 = st.columns([1, 3])
    with btn_col1:
        a_test_btn = st.button("🔌 Test Connection", use_container_width=True, key="a_test_btn")
    with btn_col2:
        a_btn = st.button("🚀 Generate All Blogs", type="primary",
                          use_container_width=True, key="a_btn")

    # ── Test Connection ───────────────────────────────────────────────────────
    if a_test_btn:
        if not a_api_key.strip():
            st.error("Enter the Webflow API Key first.")
        else:
            with st.spinner("Testing..."):
                try:
                    wf = WebflowClient(a_api_key.strip())
                    sites = wf.get_sites()
                    st.success("✅ Connected — sites: "
                               + ", ".join(s.get("displayName", s["id"]) for s in sites))
                except Exception as e:
                    st.error(f"Connection failed: {e}")

    # ── Handle pending regen request ─────────────────────────────────────────
    batch = st.session_state.get("batch", [])
    if batch and "regen_req" in st.session_state:
        req = st.session_state.pop("regen_req")
        target = next((b for b in batch if b["slug"] == req["slug"]), None)
        if target:
            _regen_image(target, req["idx"])
            st.session_state["batch"] = batch
            st.rerun()

    if batch and "regen_main_thumb_req" in st.session_state:
        req = st.session_state.pop("regen_main_thumb_req")
        target = next((b for b in batch if b["slug"] == req["slug"]), None)
        if target:
            _regen_main_thumb(target)
            st.session_state["batch"] = batch
            st.rerun()

    # ── Generate all blogs ────────────────────────────────────────────────────
    if a_btn:
        if not a_api_key.strip():
            st.error("Enter the Webflow API key.")
            st.stop()
        urls = [u.strip() for u in a_urls_raw.strip().splitlines() if u.strip()]
        if not urls:
            st.error("Enter at least one blog URL.")
            st.stop()

        batch = []
        for raw_url in urls:
            url = ("https://" + raw_url) if not raw_url.startswith("http") else raw_url

            # Resolve the real slug via GET (follows redirects) + canonical tag fallback.
            # HEAD is unreliable on many Webflow sites; GET + r.url is the ground truth.
            def _resolve_slug(start_url: str) -> tuple[str, str]:
                """Return (final_url, slug) using GET redirect chain + <link rel=canonical>."""
                try:
                    _r = requests.get(
                        start_url, allow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                        timeout=15,
                    )
                    # 1. Check <link rel="canonical"> — most reliable on Webflow
                    _soup = BeautifulSoup(_r.content, "html.parser")
                    _canon = _soup.find("link", rel="canonical")
                    if _canon and _canon.get("href"):
                        _cu = _canon["href"].rstrip("/")
                        if "/blog/" in _cu:
                            return _cu, _cu.split("/")[-1]
                    # 2. Fall back to the final URL after redirects
                    _fu = _r.url.rstrip("/")
                    return _fu, _fu.split("/")[-1]
                except Exception:
                    _fb = start_url.rstrip("/")
                    return _fb, _fb.split("/")[-1]

            final_url, slug = _resolve_slug(url)
            output_dir = Path("generated_images") / slug
            output_dir.mkdir(parents=True, exist_ok=True)

            st.markdown(f"---\n### 📝 `{slug}`")
            orig_slug = url.rstrip("/").split("/")[-1]
            if slug != orig_slug:
                st.info(f"↪️ Slug resolved: `{orig_slug}` → `{slug}`")

            with st.status("Connecting to Webflow...", expanded=True) as s:
                try:
                    wf, site_id, site_name, collection_id, item_id, was_published = \
                        do_webflow_connect(a_api_key.strip(), None, "", slug, blog_url=url)
                    matched_client = _match_client(site_name)
                    if matched_client:
                        label_suffix = f"  →  template: **{_client_display_name(matched_client)}**"
                    else:
                        label_suffix = f"  →  ⚠️ no template for **{site_name}** — paste Figma link above to add it"
                    s.update(label=f"Webflow connected ✓{label_suffix}", state="complete")
                except Exception as e:
                    s.update(label="Webflow connection failed", state="error")
                    st.error(str(e))
                    batch.append({"url": url, "slug": slug, "title": slug,
                                  "results": [], "image_urls": [], "wf_info": {},
                                  "uploaded": False, "error": str(e)})
                    continue

            title, image_urls, results, _ = run_workflow(
                url, output_dir,
                wf_fallback=wf,
                collection_id_fallback=collection_id,
                item_id_fallback=item_id,
                client_slug=matched_client,
            )

            # ── Composite main image + thumbnail ──────────────────────────────
            main_b, thumb_b = None, None
            ok_r = [r for r in results if r["status"] == "ok"]
            if ok_r:
                with st.status("Generating cover photo & compositing...", expanded=True) as s:
                    try:
                        photo_prompts = [r["prompt"] for r in ok_r if r.get("type") != "infographic"]
                        cover_bg = _generate_cover_bg(title, photo_prompts if photo_prompts else [title])
                        m_logo, t_logo = ensure_figma_assets_for_client(matched_client)
                        m_tpl, t_tpl  = make_tpls(matched_client, m_logo, t_logo)
                        main_b  = composite_template(cover_bg, title, m_tpl)
                        thumb_b = composite_template(cover_bg, title, t_tpl)
                        s.update(label="Main + thumbnail ready ✓", state="complete")
                    except Exception as _ce:
                        st.error(f"⛔ Compositing error: {_ce}")
                        s.update(label="Compositing failed", state="error")

            batch.append({
                "url": url, "slug": slug, "title": title,
                "results": results, "image_urls": image_urls,
                "main_bytes": main_b, "thumb_bytes": thumb_b,
                "client": matched_client,
                "wf_info": {"site_id": site_id, "collection_id": collection_id,
                            "item_id": item_id, "was_published": was_published,
                            "site_name": site_name},
                "uploaded": False,
            })

        st.session_state["batch"] = batch

    # ── Review results (grouped per blog) ────────────────────────────────────
    batch = st.session_state.get("batch", [])
    if batch:
        st.divider()
        st.subheader("Review & Upload")

        for blog_state in batch:
            label = ("✅ " if blog_state.get("uploaded") else
                     "❌ " if blog_state.get("error") else "📝 ")
            with st.expander(f"{label}{blog_state['slug']} — {blog_state.get('title', '')}",
                             expanded=not blog_state.get("uploaded")):
                if blog_state.get("error"):
                    st.error(f"Failed to process: {blog_state['error']}")
                elif blog_state["results"]:
                    _render_blog_results(blog_state)
                else:
                    st.warning("No images generated.")

        # ── Single Upload All button ──────────────────────────────────────────
        pending = [b for b in batch if not b.get("uploaded") and not b.get("error") and b["results"]]
        if pending:
            st.divider()
            total_imgs = sum(len([r for r in b["results"] if r["status"] == "ok"]) for b in pending)
            if st.button(
                f"🚀 Upload All to Webflow — {len(pending)} blog(s), {total_imgs} image(s)",
                type="primary", use_container_width=True, key="upload_all_btn"
            ):
                if not a_api_key.strip():
                    st.error("Enter the Webflow API key above.")
                else:
                    for blog_state in pending:
                        slug = blog_state["slug"]
                        st.markdown(f"#### Uploading `{slug}`...")
                        with st.status("Connecting to Webflow...", expanded=True) as s:
                            try:
                                wf, site_id, site_name, collection_id, item_id, was_published = \
                                    do_webflow_connect(a_api_key.strip(),
                                                       None,
                                                       blog_state.get("client_name", ""), slug)
                                s.update(label="Connected ✓", state="complete")
                            except Exception as e:
                                s.update(label="Connection failed", state="error")
                                st.error(f"`{slug}` — {e}")
                                continue
                        ok_results = [r for r in blog_state["results"] if r["status"] == "ok"]
                        do_webflow_upload(wf, site_id, collection_id, item_id, was_published,
                                          blog_state["image_urls"], ok_results,
                                          site_name, blog_state.get("client_name", ""),
                                          main_bytes=blog_state.get("main_bytes"),
                                          thumb_bytes=blog_state.get("thumb_bytes"),
                                          blog_title=blog_state.get("title", ""))
                        blog_state["uploaded"] = True
                    st.session_state["batch"] = batch
                    st.rerun()
        elif any(b.get("uploaded") for b in batch):
            st.success("✅ All blogs uploaded to Webflow.")
