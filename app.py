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
KIE_BASE        = "https://api.kie.ai/api/v1"
FREE_MODE       = not GOOGLE_API_KEY or not KIE_API_KEY

FIGMA_TOKEN    = os.getenv("FIGMA_ACCESS_TOKEN", os.getenv("FIGMA_API_TOKEN", "")).strip()
FIGMA_FILE_KEY = "qMMRF3RX3tkUt64hWA5KHT"
ASSETS_DIR      = Path("template_assets")
ASSETS_DIR.mkdir(exist_ok=True)          # ensure folder always exists
INTER_FONT_PATH = ASSETS_DIR / "Inter.ttf"
MAIN_TPL  = {"w": 920, "h": 613, "logo": ASSETS_DIR / "logo_panel_main.png",  "tx": 34, "ty": 307, "tw": 290, "fsz": 24}
THUMB_TPL = {"w": 736, "h": 560, "logo": ASSETS_DIR / "logo_panel_thumb.png", "tx": 33, "ty": 280, "tw": 227, "fsz": 19}

SYSTEM_PROMPT_TEMPLATE = """You write scene descriptions for a documentary workplace photo generator.

⚠️ Do NOT write "Documentary-style", "candid workplace photography", or any camera/photography term. That prefix is added automatically. Write ONLY the scene content.

━━ RULE 1 — ENVIRONMENT LABEL (start every description with this) ━━
Each description MUST begin with the assigned environment label in brackets.
This label is the primary visual instruction to the image generator — it MUST appear first.

ASSIGNED ENVIRONMENTS — use in order, one per image:
{required_envs}

Each bullet must start exactly like: • [LABEL] who + activity + detail
Example: • [MEETING TABLE] Two IT managers seated at a conference table reviewing a printed network diagram, one pointing to a section on the page.

⚠️ Never skip or rename the label. The image generator will produce the WRONG location without it.

━━ RULE 2 — CONTENT-SPECIFIC ACTIVITY (most important) ━━
Every scene must show a SPECIFIC activity that directly illustrates the blog topic.
Do NOT write "working at a computer" or "looking at a screen" — describe the exact task.

Good examples for a Cloud Backup blog:
  ✓ [STANDING DESK] A sysadmin standing at a raised desk running a test restore job, the screen showing a backup progress bar at 74%.
  ✓ [MEETING TABLE] Three IT techs around a conference table reviewing printed cloud storage usage reports, one circling numbers with a pen.
Bad examples:
  ✗ A man working on his laptop in an open office. (generic — no topic connection)
  ✗ Two people at a desk looking at a screen. (no environment label, no specific activity)

Each image must show a DIFFERENT activity — same topic, different angle of the work.

━━ RULE 3 — PEOPLE VARIETY (required across all {count} images) ━━
- Mix male and female workers across the {count} images — not all the same gender
- Vary hair color each image: use blonde, brown, dark/black, greying — each image different
- Vary apparent age: some look early 30s, some mid-40s
- Be SPECIFIC in each description — write "a dark-haired woman in her early 40s" not just "a person"
- White American or British Caucasian only
- Eyes on screen/desk/colleague — NEVER at the camera
- Natural posture: slight slouch, shifted weight — not perfectly upright
- Plain business casual: navy polo, grey fleece, chinos, plain t-shirt — NO logos or company names

━━ RULE 4 — ENVIRONMENT DETAILS ━━
- Monitors show dark dashboards or terminal windows — no readable text
- Desk surfaces: keyboard, mouse, papers, a phone face-down — plain and lived-in
- NO food, NO drinks, NO coffee cups, NO water bottles on desks

━━ OUTPUT FORMAT ━━
- Exactly {count} bullet points (• or -). Nothing else.
- Each bullet: [ENV LABEL] + who + specific blog-related activity + inside the exact location
- Optional second sentence: one specific on-screen or in-hand detail
- NO photography words. NO dramatic adjectives (stunning, cinematic, dramatic, perfect, etc.)"""

# ── Scene type pool ───────────────────────────────────────────────────────────
SCENE_TYPES = [
    ("SOLO WORKSTATION",
     "one person alone at a single desk with a laptop or monitor"),
    ("SIDE-BY-SIDE PAIR",
     "two people seated side by side reviewing something on a shared screen"),
    ("MEETING TABLE",
     "two to four people seated around a meeting room table in discussion"),
    ("STANDING DESK",
     "one person standing at a height-adjustable standing desk looking at their screen"),
    ("HELP DESK COUNTER",
     "one person at a help desk counter with headset and two monitors"),
    ("OPEN PLAN WIDE",
     "wide shot of three or four people at separate desks across an open-plan office floor"),
    ("INFORMAL HUDDLE",
     "two people standing and talking near a kitchen counter or office hallway"),
    ("WALKING CORRIDOR",
     "one or two people walking mid-stride through a bright office corridor"),
    ("DUAL MONITOR DESK",
     "one person at a corner desk with two large monitors"),
    ("PHONE CALL AT DESK",
     "one person at a desk with phone to one ear, notepad in front"),
    ("WHITEBOARD SESSION",
     "one or two people standing at a whiteboard with markers, no readable text on board"),
    ("RECEPTION AREA",
     "one person standing at a front reception desk in a modern office lobby"),
    ("COFFEE BREAK CHAT",
     "two people having a casual standing conversation near a coffee machine in a break room"),
    ("WINDOW SEAT LAPTOP",
     "one person working on a laptop at a desk beside a large office window with natural daylight coming in"),
    ("PRESENTATION SCREEN",
     "one person standing beside a large wall-mounted TV or presentation screen, pointing at content"),
    ("SMALL CONFERENCE ROOM",
     "three people seated around a small table inside a glass-walled conference room"),
    ("FOCUSED READING",
     "one person seated at a desk reading printed documents or a report, pen in hand"),
    ("LOUNGE AREA LAPTOP",
     "one person working on a laptop in a casual office lounge with soft seating"),
    ("STICKY NOTE WALL",
     "two people standing at a wall covered with colorful sticky notes, organizing ideas"),
    ("OUTDOOR TERRACE",
     "one or two people working at a table on a sunny outdoor office terrace or rooftop"),
]

# Scenes grouped by visual category — used to enforce variety in each batch
_SCENE_DESK = {   # person seated at individual computer/desk
    "SOLO WORKSTATION", "DUAL MONITOR DESK", "WINDOW SEAT LAPTOP",
    "LOUNGE AREA LAPTOP", "PHONE CALL AT DESK", "FOCUSED READING", "HELP DESK COUNTER",
}
_SCENE_GROUP = {  # group of people at table/meeting room
    "MEETING TABLE", "SMALL CONFERENCE ROOM", "SIDE-BY-SIDE PAIR",
}
_SCENE_ACTIVE = { # standing or walking — clearly NOT seated at a desk
    "WALKING CORRIDOR", "WHITEBOARD SESSION", "PRESENTATION SCREEN",
    "OUTDOOR TERRACE", "STICKY NOTE WALL", "INFORMAL HUDDLE",
    "COFFEE BREAK CHAT", "OPEN PLAN WIDE", "STANDING DESK", "RECEPTION AREA",
}

# Explicit cues added to env description so Gemini knows NOT to write a desk scene
_ACTIVE_CUE = " ← PERSON IS STANDING OR WALKING — do NOT write a seated-at-desk scene"
_GROUP_CUE  = " ← multiple people at a shared table, NOT individual desks"


def _pick_required_scenes(count: int) -> list:
    """Pick scenes with guaranteed visual diversity.
    For count=4: 1 desk + 1 group/meeting + 2 active/standing/walking.
    For count=3: 1 desk + 1 group + 1 active.
    For count=2: 1 desk + 1 active.
    Guarantees at least 50% of scenes are non-desk environments."""
    desk   = [s for s in SCENE_TYPES if s[0] in _SCENE_DESK]
    group  = [s for s in SCENE_TYPES if s[0] in _SCENE_GROUP]
    active = [s for s in SCENE_TYPES if s[0] in _SCENE_ACTIVE]

    picks = []
    picks.append(random.choice(desk))    # always: 1 seated-desk scene
    picks.append(random.choice(active))  # always: 1 standing/walking scene

    if count >= 3:
        picks.append(random.choice(group))   # 1 meeting/group scene

    if count >= 4:
        used = {s[0] for s in picks}
        fresh_active = [s for s in active if s[0] not in used]
        picks.append(random.choice(fresh_active if fresh_active else active))  # 2nd active

    if count >= 5:
        remaining = [s for s in SCENE_TYPES if s[0] not in {p[0] for p in picks}]
        picks.extend(random.sample(remaining, min(count - 4, len(remaining))))

    random.shuffle(picks)
    return picks[:count]


def _format_required_envs(scenes: list) -> str:
    lines = []
    for i, (name, desc) in enumerate(scenes):
        if name in _SCENE_ACTIVE:
            cue = _ACTIVE_CUE
        elif name in _SCENE_GROUP:
            cue = _GROUP_CUE
        else:
            cue = ""
        lines.append(f"  {i+1}. [{name}]: {desc}{cue}")
    return "\n".join(lines)


def _scene_pool_text() -> str:
    return "\n".join(f"  • {name}: {desc}" for name, desc in random.sample(SCENE_TYPES, len(SCENE_TYPES)))


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
    # ethnicity anchor
    "white American Caucasian office workers"
    # NOTE: NO "avoid X" here — those belong in NEGATIVE_PROMPT only.
    # Flux reads "avoid plastic skin" as a POSITIVE token for plastic skin.
)
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

# ── Kie API prompt suffix (GPT Image 2 / Grok Imagine) ───────────────────────
KIE_QUALITY_SUFFIX = (
    ", documentary workplace photography, candid unscripted office moment, "
    "photojournalistic editorial style, natural office lighting, "
    "Sony A7 IV, no color grading, no filter, no CGI, "
    "plain unbranded clothing with no logos or company names, "
    "white Caucasian American office workers, "
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


# ── Image prompt builder — puts [ENV LABEL] FIRST so image model sees it before style tags ──
_ENV_LABEL_RE = re.compile(r'^\[([A-Z][A-Z\s]+)\]\s*')

def _build_image_prompt(scene_desc: str, quality_suffix: str) -> str:
    """Restructure the final prompt so [ENV LABEL] is the very first token.
    Without this, image models default to 'person at desk' because 'Documentary workplace
    photography' is an early strong signal. Putting [WALKING CORRIDOR] first overrides it."""
    m = _ENV_LABEL_RE.match(scene_desc)
    if m:
        label = m.group(0).strip()          # e.g. "[WALKING CORRIDOR]"
        rest  = scene_desc[m.end():].strip()
        return f"{label} Documentary-style candid workplace photography: {rest}{quality_suffix}"
    return f"Documentary-style candid workplace photography of {scene_desc}{quality_suffix}"


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
    _QUALITY_PROMPT = (
        "Analyze this image for quality issues as a blog image for an IT/MSP company. "
        "Check for: AI-generated artifacts, distorted faces, wrong finger count, unnatural lighting, "
        "sci-fi/cliche elements, or anything fake/unprofessional.\n\n"
        "Reply ONLY in this exact JSON format:\n"
        '{"quality": "good|fair|poor", "is_ai_generated": true|false, "issues": ["issue1"]}\n'
        "Keep issues as [] if image looks fine."
    )
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

    # Gemini vision — primary
    if GOOGLE_API_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GOOGLE_API_KEY)
            model = genai.GenerativeModel("gemini-2.0-flash-lite")
            img_part = {"mime_type": "image/jpeg", "data": b64}
            resp = model.generate_content([_QUALITY_PROMPT, img_part])
            match = re.search(r'\{.*\}', resp.text.strip(), re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass

    # Pollinations vision — FREE MODE fallback
    try:
        payload = {"model": "openai", "messages": [{"role": "user", "content": [
            {"type": "text", "text": _QUALITY_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        ]}], "private": True}
        r = requests.post("https://text.pollinations.ai/openai", json=payload, timeout=60)
        r.raise_for_status()
        match = re.search(r'\{.*\}', _pollinations_text(r.json()), re.DOTALL)
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
            model = genai.GenerativeModel("gemini-2.0-flash-lite")
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
    if content_el:
        content = _soup_to_structured_text(content_el)
    else:
        content = "\n".join(p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True))
    area = content_el or soup
    image_urls = [get_img_src(img) for img in area.find_all("img") if is_content_image(img)]
    return title, content, image_urls


def _soup_to_structured_text(soup) -> str:
    """Convert BeautifulSoup element to text while preserving list structure markers."""
    # Mark ordered list items with numbers
    for ol in soup.find_all("ol"):
        for idx, li in enumerate(ol.find_all("li", recursive=False), 1):
            li.insert_before(f"{idx}. ")
    # Mark unordered list items with bullets — skip li already inside ol
    for li in soup.find_all("li"):
        if not li.find_parent("ol"):
            li.insert_before("• ")
    # Add line break before headings so they don't run into previous text
    for tag in soup.find_all(["h2", "h3", "h4"]):
        tag.insert_before("\n")
    return soup.get_text("\n", strip=True)


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
            content = _soup_to_structured_text(soup)
            image_urls = [img.get("src") or img.get("data-src", "")
                          for img in soup.find_all("img") if is_content_image(img)]
            break

    # Fallback: longest plain-text field (may be richtext without images)
    if not content:
        candidates = [(k, v) for k, v in field_data.items()
                      if isinstance(v, str) and k not in ("slug", "name", "title")]
        if candidates:
            best_key, best_val = max(candidates, key=lambda x: len(x[1]))
            if "<" in best_val and ">" in best_val:
                soup = BeautifulSoup(best_val, "html.parser")
                content = _soup_to_structured_text(soup)
            else:
                content = best_val

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


def _gemini_text(system: str, user: str, max_tokens: int = 200, temperature: float = 0.8) -> str | None:
    """Thin Gemini wrapper. Returns stripped text or None on any error."""
    if not GOOGLE_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types as gt
        client = genai.Client(api_key=GOOGLE_API_KEY)
        resp = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=user,
            config=gt.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
                temperature=temperature,
            )
        )
        return (resp.text or "").strip()
    except Exception:
        return None


def generate_prompts_free(title: str, content: str, count: int) -> list:
    """Template-based fallback — no external API needed.
    Uses each scene's own environment description (20 distinct scenes) so the
    set is varied even without Gemini. _pick_required_scenes returns (name, desc) tuples."""
    scenes = _pick_required_scenes(count)
    topic = (title or "the topic")[:55]
    prompts = []
    for name, desc in scenes:
        prompts.append(f"[{name}] An IT professional — {desc} — focused on a specific task related to {topic}.")
    # Pad with fresh random scenes (not a fixed one) if we somehow came up short
    used = {s[0] for s in scenes}
    while len(prompts) < count:
        pool = [s for s in SCENE_TYPES if s[0] not in used] or SCENE_TYPES
        name, desc = random.choice(pool)
        used.add(name)
        prompts.append(f"[{name}] An IT professional — {desc} — working on a task related to {topic}.")
    return prompts[:count]


def generate_prompts_live(title: str, content: str, count: int) -> list:
    try:
        from google import genai
        from google.genai import types as gt
        client = genai.Client(api_key=GOOGLE_API_KEY)
        required_envs = _format_required_envs(_pick_required_scenes(count))
        for _attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model="gemini-2.0-flash-lite",
                    contents=f"Blog Title:\n{title}\n\nWhole Blog:\n{content}",
                    config=gt.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT_TEMPLATE.format(count=count, required_envs=required_envs),
                        max_output_tokens=3000,
                        temperature=1.5,
                    )
                )
                break
            except Exception as _e:
                if "429" in str(_e) and _attempt < 2:
                    time.sleep(8)
                else:
                    raise
        prompts = parse_bullet_list(resp.text.strip(), count)
        # ── Debug: show what Gemini generated + the actual final prompt sent to image API ──
        with st.expander("🔍 Debug — Scene descriptions sent to image generator", expanded=False):
            for idx, p in enumerate(prompts, 1):
                st.markdown(f"**Scene {idx}:** {p}")
                final = _build_image_prompt(p, KIE_QUALITY_SUFFIX)
                st.caption(f"Final prompt ({len(final)} chars): {final[:300]}{'…' if len(final)>300 else ''}")
        return prompts
    except Exception as e:
        err = str(e)
        if any(k in err for k in ("429", "RESOURCE_EXHAUSTED", "quota", "rate limit", "exceeded")):
            st.info("ℹ️ Gemini quota reached — using template descriptions.")
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
MAXIMUM 2 infographic slots. Only add an infographic when the blog content clearly supports it.

ADD an infographic slot when:
  • Blog has numbered phases, steps, or a process → "steps"
  • Blog mentions specific percentages, stats, or dollar figures → "stats"
  • Blog gives tips, action items, best practices, or a "how to" list → "checklist"
  • Blog compares options, before/after, or has measurable improvements → "bar_chart"

Add a SECOND infographic only when there are two clearly distinct structured sections that each map well to a different type.

If the blog is thin, introductory, or does not contain clear structure/data/steps → use 0 infographics (all photos).

Infographic slots skip the required-environments list.

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


def _detect_infographics_from_text(title: str, content: str, max_ig: int) -> list:
    """Quota-free heuristic infographic detector — used when Gemini is unavailable.

    Scans raw blog text for structure (numbered steps, percentages/dollar stats,
    bullet lists) and returns 0..max_ig infographic spec dicts. Content-driven:
    returns [] when the blog has no clear structure (matches Gemini behavior)."""
    if max_ig <= 0:
        return []
    text = content or ""
    ig_title = " ".join((title or "Key Insights").split()[:8]) or "Key Insights"
    found = []

    # ── steps: "Step 1", "Phase 1", or a numbered list (1. 2. 3.) ──
    step_items = []
    for m in re.finditer(r"(?im)\b(?:step|phase)\s+(\d+)\s*[:.\)\-]?\s*(.{3,70})", text):
        label = re.split(r"[\n.|•]", m.group(2).strip())[0].strip()
        if label:
            step_items.append({"number": int(m.group(1)), "title": label[:60], "points": []})
    if len(step_items) < 3:  # fall back to a plain numbered list
        nums = []
        for m in re.finditer(r"(?m)^\s*(\d+)[.\)]\s+(.{3,80})", text):
            label = re.split(r"[\n.|•]", m.group(2).strip())[0].strip()
            if label:
                nums.append({"number": int(m.group(1)), "title": label[:60], "points": []})
        if len(nums) >= 3:
            step_items = nums
    # dedup by number, keep order, cap at 7
    if len(step_items) >= 3:
        seen, uniq = set(), []
        for it in step_items:
            if it["number"] not in seen:
                seen.add(it["number"]); uniq.append(it)
        found.append({"infographic_type": "steps", "title": ig_title, "items": uniq[:7]})

    # ── stats: percentages and dollar figures with a short label ──
    stats = []
    for m in re.finditer(r"([\$]?\d[\d,]*(?:\.\d+)?\s*(?:%|percent|million|billion|k\b|M\b|B\b)?)", text):
        val = m.group(1).strip()
        if not re.search(r"%|percent|\$|million|billion", val, re.I):
            continue  # only keep meaningful figures, not bare numbers
        start = max(0, m.start() - 0)
        tail = text[m.end():m.end() + 70]
        label = re.split(r"[.\n|•]", tail)[0].strip(" ,:-")
        label = re.sub(r"\s+", " ", label)[:60]
        if label and len(label) > 8:
            disp = val.replace("percent", "%").replace(" ", "")
            stats.append({"value": disp, "label": label})
        if len(stats) >= 4:
            break
    if len(stats) >= 2:
        found.append({"infographic_type": "stats", "title": ig_title, "stats": stats[:4]})

    # ── checklist: bullet list or tips/best-practices keywords ──
    bullets = []
    for m in re.finditer(r"(?m)^\s*[-•*▪‣]\s+(.{3,70})", text):
        item = re.split(r"[\n|]", m.group(1).strip())[0].strip()
        if item:
            bullets.append(item[:60])
    if len(bullets) >= 4:
        found.append({"infographic_type": "checklist", "title": ig_title, "items": bullets[:8]})

    return found[:max_ig]


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
        user_msg = f"Blog Title:\n{title}\n\nBlog Content:\n{content[:5000]}"
        for _attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=user_msg,
                    config=gt.GenerateContentConfig(
                        system_instruction=system_instr,
                        max_output_tokens=4000,
                        temperature=0.9,
                    )
                )
                break
            except Exception as _e:
                if "429" in str(_e) and _attempt < 2:
                    time.sleep(8)
                else:
                    raise
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
        label = (f"🔍 Slot plan: {photo_count} photo{'s' if photo_count != 1 else ''}"
                 + (f", {n_ig} infographic{'s' if n_ig != 1 else ''}" if n_ig else ", 0 infographics"))
        with st.expander(label, expanded=True):
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
        err = str(e)
        is_quota = any(k in err for k in ("429", "RESOURCE_EXHAUSTED", "quota", "rate limit", "exceeded"))
        descs = []
        if is_quota:
            st.info("ℹ️ Gemini quota reached — using backup generator. Images will still be generated.")
            try:
                descs = generate_prompts_free(title, content, count)
            except Exception:
                pass
        else:
            with st.expander("⚠️ Slot planning failed — click to debug", expanded=True):
                st.error(f"**Error:** `{e}`")
                st.code(repr(_debug_raw[:400]) if _debug_raw else "No response captured (error before API call)")
            try:
                descs = generate_prompts_live(title, content, count)
            except Exception:
                try:
                    descs = generate_prompts_free(title, content, count)
                except Exception:
                    pass
        fallback = f"IT professional working at a desk on tasks related to {title[:40]}."
        while len(descs) < count:
            descs.append(fallback)
        descs = descs[:count]
        slots = [{"slot": i + 1, "type": "photo", "description": d}
                 for i, d in enumerate(descs)]

        # Quota-free infographic detection — keep ≥1 photo, max 2 infographics
        igs = _detect_infographics_from_text(title, content, min(2, max(0, count - 1)))
        if igs:
            for off, spec in enumerate(igs):
                slots[count - 1 - off] = {"type": "infographic", **spec}
            for i, s in enumerate(slots):   # renumber after replacement
                s["slot"] = i + 1
            kinds = ", ".join(s.get("infographic_type", "") for s in slots
                              if s.get("type") == "infographic")
            st.info(f"📊 Quota-free mode: detected {len(igs)} infographic(s) from blog structure ({kinds}).")
        return slots


def generate_alt_text_for(prompt: str, title: str, index: int = 0) -> str:
    system = (
        "You write unique, specific alt text for individual blog images. "
        "Each alt text must describe what is visually happening in THAT specific image. "
        "Rules: 60-80 characters maximum, no quotes, no 'image of', no full sentences — "
        "just a descriptive phrase based on the actual scene, using 1-2 topic keywords. "
        "Example: 'IT technician reviewing network dashboard at standing desk' (59 chars). "
        "Never exceed 80 characters. Never repeat the same phrase for different images."
    )
    user = (
        f"Blog topic: {title}\n"
        f"Image {index} specific scene: {prompt[:200]}\n\n"
        "Write alt text that describes THIS specific image (not a generic description):"
    )
    result = _gemini_text(system, user, max_tokens=50, temperature=0.9)
    if result:
        result = result.strip('"\'')
        # Strip special/non-ASCII characters — keep only letters, numbers, spaces, hyphens, commas
        result = re.sub(r'[^a-zA-Z0-9 ,\-]', '', result)
        result = re.sub(r' {2,}', ' ', result).strip()
        if len(result) > 80:
            result = result[:80].rsplit(' ', 1)[0]
        return result
    return re.sub(r'[^a-zA-Z0-9 ,\-]', '', prompt[:80]).strip()


# ── Image generation ───────────────────────────────────────────────────────────

def generate_image_free(prompt: str, index: int, width: int, height: int,
                        seed: int = None, model: str = "flux") -> bytes:
    """Generate via Pollinations.ai. model: 'flux' (default) or 'flux-realism' (photorealism LoRA)."""
    enhanced = _build_image_prompt(prompt, _QUALITY_BLOCK)
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
    """Write a DIFFERENT image scene for the same blog topic.
    Always changes the environment vs the original — even when Gemini is unavailable."""
    # Detect the original scene's environment (from its [ENV] prefix, if any) and avoid it
    _cur = None
    _m = re.match(r"\s*\[([A-Z0-9 /\-]+)\]", original_prompt or "")
    if _m:
        _cur = _m.group(1).strip()
    _choices = [s for s in SCENE_TYPES if s[0] != _cur] or SCENE_TYPES
    env_name, env_desc = random.choice(_choices)
    system_var = (
        "Write a plain scene description for a documentary office photo. "
        "Your output fills: 'Documentary-style candid workplace photography of [YOUR OUTPUT]'\n"
        "⚠️ Do NOT write 'Documentary-style', 'candid', or any photography term — added automatically.\n\n"
        f"REQUIRED ENVIRONMENT: [{env_name}] — {env_desc}\n"
        f"Your description MUST start with [{env_name}] exactly. "
        "This label is how the image generator knows which physical space to render.\n\n"
        "ACTIVITY RULE: Show a SPECIFIC task directly related to the blog topic. "
        "Not 'working at a computer' — describe the exact thing the person is doing.\n\n"
        "PEOPLE: White American or British Caucasian only, age 30–50, average build. "
        "Eyes on screen/desk/colleague — NEVER at the camera. "
        "Plain business casual — NO logos or company names on clothing.\n\n"
        "ENVIRONMENT: Desk has keyboard, mouse, papers, phone face-down. "
        "NO food, NO drinks, NO coffee cups, NO water bottles.\n\n"
        "Write something COMPLETELY DIFFERENT from the original scene — different environment, different number of people, different activity.\n\n"
        f"FORMAT: Start with [{env_name}], then 1–2 plain sentences. No photography words, no dramatic adjectives."
    )
    user_var = (
        f"Blog topic: {title}\n"
        f"Original scene (write something completely different): {original_prompt}\n\n"
        "Write the new scene:"
    )
    result = _gemini_text(system_var, user_var, max_tokens=150, temperature=1.2)
    if result and len(result) > 30:
        return result.lstrip("•-* ")
    # Quota-free fallback: still a DIFFERENT environment than the original (never a copy)
    return f"[{env_name}] {env_desc}, focused on a task related to {(title or 'the topic')[:60]}"


def _generate_cover_scene(title: str) -> str:
    """Generate a cover scene description directly tied to the blog title."""
    env_name, env_desc = random.choice(SCENE_TYPES)
    system = (
        "Write a plain scene description for a documentary office photo. "
        "Your output fills: 'Documentary-style candid workplace photography of [YOUR OUTPUT]'\n"
        "⚠️ Do NOT write 'Documentary-style', 'candid', or any photography term — added automatically.\n\n"
        f"REQUIRED ENVIRONMENT: [{env_name}] — {env_desc}\n"
        f"Your description MUST start with [{env_name}].\n\n"
        "ACTIVITY RULE: The scene must DIRECTLY show the core activity from the blog title. "
        "Describe the exact task — not 'working at a computer.'\n\n"
        "PEOPLE: White American or British Caucasian only, age 30–50, average build. "
        "Eyes on screen/desk/colleague — NEVER at the camera. "
        "Plain business casual — NO logos or company names on clothing.\n\n"
        "ENVIRONMENT: Desk has keyboard, mouse, papers, phone face-down. "
        "NO food, NO drinks, NO coffee cups, NO water bottles.\n\n"
        f"FORMAT: Start with [{env_name}], then 1–2 plain sentences. No photography words, no dramatic adjectives."
    )
    result = _gemini_text(system, f"Blog title: {title}\n\nWrite the cover image scene:", max_tokens=150, temperature=1.0)
    if result and len(result) > 30:
        return result.lstrip("•-* ")
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
    full_prompt = _build_image_prompt(prompt, KIE_QUALITY_SUFFIX)

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

    def _request(self, method, path, *, params=None, json_body=None, timeout=30):
        """Single request with automatic retry on 429 (rate limit) and transient 5xx.
        Webflow v2 caps at ~60 req/min; a batch of blogs bursts past that, so without
        this retry the 3rd/4th blog would 429 and fail. Honors the Retry-After header."""
        url = f"{self.BASE}{path}"
        for attempt in range(5):
            r = requests.request(method, url, headers=self.headers,
                                  params=params, json=json_body, timeout=timeout)
            if r.ok:
                return r.json()
            # Retry on rate limit (429) and transient server errors (502/503/504)
            if r.status_code in (429, 502, 503, 504) and attempt < 4:
                try:
                    wait = float(r.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    wait = 0.0
                if wait <= 0:
                    wait = min(2 ** attempt * 3, 30)   # 3, 6, 12, 24s backoff
                time.sleep(wait)
                continue
            self._raise(r)
        # Exhausted retries — raise the last response's error
        self._raise(r)

    def _get(self, path, params=None):
        return self._request("GET", path, params=params, timeout=30)

    def _post(self, path, body=None):
        return self._request("POST", path, json_body=body, timeout=60)

    def _patch(self, path, body):
        return self._request("PATCH", path, json_body=body, timeout=30)

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

    # Shared across instances/blogs in one process so a batch of posts in the same
    # collection fetches the item list ONCE instead of re-walking it per blog
    # (the main driver of Webflow rate-limiting during batch runs). Short TTL.
    _ITEMS_CACHE: dict = {}
    _ITEMS_TTL = 120  # seconds

    def _all_collection_items(self, collection_id: str) -> list:
        cached = WebflowClient._ITEMS_CACHE.get(collection_id)
        if cached and (time.time() - cached[0]) < WebflowClient._ITEMS_TTL:
            return cached[1]
        all_items, offset = [], 0
        while True:
            data = self._get(f"/collections/{collection_id}/items",
                             params={"limit": 100, "offset": offset})
            items = data.get("items", [])
            all_items.extend(items)
            if len(items) < 100:
                break
            offset += 100
        WebflowClient._ITEMS_CACHE[collection_id] = (time.time(), all_items)
        return all_items

    def find_item_by_slug(self, collection_id: str, slug: str):
        all_items = self._all_collection_items(collection_id)
        # Pass 1: exact match
        for item in all_items:
            if item.get("fieldData", {}).get("slug") == slug:
                return item
        # Pass 2: case-insensitive exact match
        slug_lower = slug.lower()
        for item in all_items:
            if (item.get("fieldData", {}).get("slug") or "").lower() == slug_lower:
                return item
        # Pass 3: fuzzy — slug is a prefix/suffix of CMS slug or vice versa
        for item in all_items:
            cms_slug = (item.get("fieldData", {}).get("slug") or "").lower()
            if cms_slug.startswith(slug_lower) or slug_lower.startswith(cms_slug):
                return item
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
    # SAFETY NET: never write Webflow API keys into figma_node_cache.json — that file is
    # committed to a PUBLIC repo. Keys live only in CLIENT_KEYS_FILE (gitignored, local).
    try:
        clean = {
            k: ({kk: vv for kk, vv in v.items() if kk != "webflow_api_key"}
                if isinstance(v, dict) else v)
            for k, v in cache.items()
        }
        FIGMA_NODE_CACHE.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── Webflow API keys: stored LOCALLY ONLY (gitignored) so they never reach the public repo.
#    On the live (Streamlit Cloud) app this file is absent → keys are entered manually per session.
CLIENT_KEYS_FILE = Path("client_keys.json")


def _load_client_keys() -> dict:
    try:
        return json.loads(CLIENT_KEYS_FILE.read_text(encoding="utf-8")) if CLIENT_KEYS_FILE.exists() else {}
    except Exception:
        return {}


def _save_client_key(slug: str, key: str):
    if not slug:
        return
    keys = _load_client_keys()
    keys[slug] = key
    try:
        CLIENT_KEYS_FILE.write_text(json.dumps(keys, indent=2), encoding="utf-8")
    except Exception:
        pass


def _get_client_key(slug: str) -> str:
    """Resolve a client's Webflow key: local keys file → legacy cache entry → global env/secret."""
    if slug:
        k = _load_client_keys().get(slug, "")
        if k:
            return k
        legacy = _load_node_cache().get(slug, {}).get("webflow_api_key", "")
        if legacy:
            return legacy
    return os.getenv("WEBFLOW_API_KEY", "")


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
    "ink":     (26,  34,  48),
    "muted":   (91, 107, 126),
    "hdr_sub": (190, 205, 225),
    "track":   (228, 236, 244),
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


# ── Flat icons (Font Awesome 6 Free Solid) for icon-driven infographics ──
_FA_FONT_PATH = ASSETS_DIR / "fa-solid-900.ttf"
try:
    _FA_OK = _FA_FONT_PATH.exists()
except Exception:
    _FA_OK = False


def _ig_fa(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FA_FONT_PATH), size)


_IG_ICONS = {k: chr(v) for k, v in {
    "lock": 0xf023, "shield": 0xf3ed, "envelope": 0xf0e0, "gear": 0xf013,
    "cloud": 0xf0c2, "server": 0xf233, "user": 0xf007, "users": 0xf0c0,
    "key": 0xf084, "warning": 0xf071, "check": 0xf058, "database": 0xf1c0,
    "wifi": 0xf1eb, "bug": 0xf188, "file": 0xf15b, "laptop": 0xf109,
    "phone": 0xf3cd, "eye": 0xf06e, "fingerprint": 0xf577, "network": 0xf6ff,
    "ban": 0xf05e,
}.items()}

# Ordered keyword → icon rules (most specific first).
_ICON_RULES = [
    (("phish", "email", "mail", "spam", "inbox"), "envelope"),
    (("password", "credential", "login"), "lock"),
    (("mfa", "multi-factor", "two-factor", "authenticat", "biometric", "fingerprint"), "fingerprint"),
    (("unauthor", "access control", "permission", "privilege", "restricted"), "ban"),
    (("lost", "stolen", "device", "laptop", "endpoint", "byod"), "laptop"),
    (("mobile", "smartphone", "phone"), "phone"),
    (("insider", "employee", "staff", "personnel", "human error", "people", "team"), "users"),
    (("backup", "data breach", "database", "data loss", "records"), "database"),
    (("network", "wifi", "wi-fi", "firewall", "router", "connection"), "network"),
    (("malware", "virus", "antivirus", "ransomware", "trojan", "spyware", "exploit", "bug"), "bug"),
    (("cloud", "saas"), "cloud"),
    (("monitor", "detect", "visibility", "logging", "audit"), "eye"),
    (("server", "infrastructure", "data center", "datacenter"), "server"),
    (("encrypt", "certificate", " key"), "key"),
    (("patch", "update", "software", "unpatch", "outdated", "upgrade", "system"), "gear"),
    (("breach", "alert", "incident", "risk", "threat", "attack", "vulnerab", "danger", "warning"), "warning"),
]


def _pick_icon(text: str) -> str:
    t = (text or "").lower()
    for keys, name in _ICON_RULES:
        if any(k in t for k in keys):
            return _IG_ICONS[name]
    return _IG_ICONS["shield"]


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


# ── Per-client brand color → infographic palette ──
def _hex_to_rgb(hx: str) -> tuple:
    hx = (hx or "").strip().lstrip("#")
    if len(hx) == 3:
        hx = "".join(c * 2 for c in hx)
    try:
        return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (26, 58, 92)


def _lum(c: tuple) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _darken(c: tuple, f: float) -> tuple:
    return tuple(max(0, min(255, int(v * f))) for v in c)


def _light(c: tuple, f: float = 0.6) -> tuple:
    """Lighten a color toward white (for text on a filled brand card)."""
    return tuple(min(255, int(v + (255 - v) * f)) for v in c)


def _ig_palette(brand_hex: str) -> tuple:
    """Derive an editorial palette ENTIRELY from the client brand color — fully on-brand.
    Returns (primary, accent, bg), all sharing the SAME brand hue (monochromatic):
      primary = brand hue, deepened so white text reads on filled cards (title/cards/footer)
      accent  = the brand color itself, vivid & balanced (pill/numbers/checks/arrows)
      bg      = a very faint tint of the brand (page background)
    No warm<->cool flip — the accent always matches the client's actual brand color so it
    never produces an off/clashing hue (e.g. neon cyan on an orange brand)."""
    import colorsys
    base = _hex_to_rgb(brand_hex)
    r, g, b = (c / 255 for c in base)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    s = max(s, 0.45)
    # PRIMARY — deep brand shade so white text reads on a filled card
    pr, pg, pb = colorsys.hls_to_rgb(h, min(l, 0.34), max(s, 0.50))
    primary = (int(pr * 255), int(pg * 255), int(pb * 255))
    # ACCENT — the brand hue itself, vivid but balanced (white text still reads on the pill)
    acc_l = min(max(l, 0.46), 0.56)
    ar, ag, ab = colorsys.hls_to_rgb(h, acc_l, min(max(s, 0.55), 0.95))
    accent = (int(ar * 255), int(ag * 255), int(ab * 255))
    # BACKGROUND — faint tint of the brand color
    bg = tuple(int(c * 0.07 + 255 * 0.93) for c in base)
    return primary, accent, bg


def _brand_footer(client_slug: str, url: str) -> str:
    """Footer label like 'Client Name · clientsite.com' from the client + blog URL."""
    name = ""
    try:
        name = _client_display_name(client_slug) if client_slug else ""
    except Exception:
        name = ""
    domain = ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url or "").netloc.lower().removeprefix("www.")
    except Exception:
        domain = ""
    if name and domain:
        return f"{name} · {domain}"
    return name or domain or ""


def _ig_frame(d, w: int, h: int, bg: tuple):
    """Fill the page with the brand-tinted background + a faint rounded frame border."""
    d.rectangle([0, 0, w, h], fill=bg)
    m = int(min(w, h) * 0.022)
    d.rounded_rectangle([m, m, w - m, h - m], radius=int(h * 0.03),
                        outline=_darken(bg, 0.90), width=2)


def _ig_header(d, w: int, h: int, primary: tuple, accent: tuple,
               spec: dict, default_kicker: str, pad: int) -> int:
    """Editorial header: centered accent kicker pill + big brand-color title + subtitle.
    Returns the y where body content should start."""
    cx = w // 2
    y = int(h * 0.06)
    kick = (spec.get("kicker") or default_kicker or "").strip().upper()[:48]
    if kick:
        kf = _ig_font(int(h * 0.023), bold=True)
        bb = kf.getbbox(kick)
        pw = (bb[2] - bb[0]) + int(w * 0.035)
        ph = int(h * 0.05)
        d.rounded_rectangle([cx - pw // 2, y, cx + pw // 2, y + ph], radius=ph // 2, fill=accent)
        pill_txt = (255, 255, 255) if _lum(accent) < 150 else _darken(accent, 0.45)
        d.text((cx, y + ph // 2), kick, font=kf, fill=pill_txt, anchor="mm")
        y += ph + int(h * 0.028)
    tf = _ig_font(int(h * 0.064), bold=True)
    for ln in _ig_wrap(spec.get("title") or "Infographic", tf, w - 2 * pad)[:2]:
        d.text((cx, y), ln, font=tf, fill=primary, anchor="ma")
        y += int(h * 0.075)
    sub = (spec.get("subtitle") or "")[:120]
    if sub:
        sf = _ig_font(int(h * 0.026))
        d.text((cx, y + 2), sub, font=sf, fill=_IG["muted"], anchor="ma")
        y += int(h * 0.042)
    return y + int(h * 0.025)


def _ig_footer(d, w: int, h: int, primary: tuple, footer: str):
    # Footer text disabled for ALL clients (2026-06-12): the centered "Name · domain"
    # line truncated client names (e.g. "Unified Technician" -> "Unified") and looked
    # messy. Branding now comes solely from the corner logo (_apply_corner_logo).
    return


def _ig_variant(seed: str, n: int) -> int:
    """Deterministic 0..n-1 layout pick. Seeded by client brand + blog title so
    different clients (and different blogs) get visibly different infographic styles,
    while the same blog re-renders identically on redo."""
    return int(hashlib.md5((seed or "x").encode("utf-8")).hexdigest(), 16) % max(1, n)


def _ig_setup(spec, w, h, bg, primary, accent, default_kicker, pad):
    img = PILImage.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    _ig_frame(d, w, h, bg)
    body_top = _ig_header(d, w, h, primary, accent, spec, default_kicker, pad)
    return img, d, body_top


def _step_desc(item: dict) -> str:
    pts = item.get("points")
    return " ".join(pts) if isinstance(pts, list) else (pts or item.get("desc") or "")


# ── STEPS variant 1: vertical timeline ──
def _steps_timeline(spec, items, w, h, primary, accent, bg):
    pad = int(w * 0.06)
    img, d, body_top = _ig_setup(spec, w, h, bg, primary, accent, "Key Steps", pad)
    n = len(items)
    top, bot = body_top, h - int(h * 0.06)
    row_h = (bot - top) // n
    cxn = pad + int(w * 0.045)
    r = max(16, min(int(row_h * 0.30), int(h * 0.055)))
    d.line([(cxn, top + row_h // 2), (cxn, top + (n - 1) * row_h + row_h // 2)],
           fill=_light(accent, 0.45), width=max(3, int(w * 0.004)))
    tf = _ig_font(int(h * 0.034), bold=True)
    pf = _ig_font(int(h * 0.022))
    for i, item in enumerate(items):
        ry = top + i * row_h
        cyc = ry + row_h // 2
        d.ellipse([cxn - r, cyc - r, cxn + r, cyc + r], fill=accent)
        d.text((cxn, cyc), str(item.get("number", i + 1)),
               font=_ig_font(int(r * 1.05), bold=True), fill=(255, 255, 255), anchor="mm")
        tx = cxn + r + int(w * 0.03)
        ty = ry + int(row_h * 0.14)
        for ln in _ig_wrap(item.get("title") or f"Step {i + 1}", tf, w - tx - pad)[:1]:
            d.text((tx, ty), ln, font=tf, fill=primary)
            ty += int(h * 0.045)
        for ln in _ig_wrap(_step_desc(item)[:120], pf, w - tx - pad)[:2]:
            d.text((tx, ty), ln, font=pf, fill=_IG["muted"])
            ty += int(h * 0.030)
    return _ig_bytes(img)


# ── STEPS variant 2: numbered full-width rows ──
def _steps_rows(spec, items, w, h, primary, accent, bg):
    pad = int(w * 0.05)
    img, d, body_top = _ig_setup(spec, w, h, bg, primary, accent, "Key Steps", pad)
    n = len(items)
    top, bot = body_top, h - int(h * 0.06)
    gap = int(h * 0.022)
    row_h = (bot - top - (n - 1) * gap) // n
    shadow = _darken(bg, 0.91)
    badge_w = int(row_h * 0.95)
    tf = _ig_font(int(h * 0.032), bold=True)
    pf = _ig_font(int(h * 0.022))
    for i, item in enumerate(items):
        ry = top + i * (row_h + gap)
        d.rounded_rectangle([pad + 2, ry + 3, w - pad + 2, ry + row_h + 3], radius=12, fill=shadow)
        d.rounded_rectangle([pad, ry, w - pad, ry + row_h], radius=12, fill=(255, 255, 255))
        d.rounded_rectangle([pad, ry, pad + badge_w, ry + row_h], radius=12, fill=accent)
        d.rectangle([pad + badge_w - 14, ry, pad + badge_w, ry + row_h], fill=accent)
        d.text((pad + badge_w // 2, ry + row_h // 2), str(item.get("number", i + 1)),
               font=_ig_font(int(row_h * 0.5), bold=True), fill=(255, 255, 255), anchor="mm")
        tx = pad + badge_w + int(w * 0.025)
        ty = ry + int(row_h * 0.15)
        for ln in _ig_wrap(item.get("title") or f"Step {i + 1}", tf, w - tx - pad)[:1]:
            d.text((tx, ty), ln, font=tf, fill=primary)
            ty += int(h * 0.042)
        for ln in _ig_wrap(_step_desc(item)[:110], pf, w - tx - pad)[:2]:
            d.text((tx, ty), ln, font=pf, fill=_IG["muted"])
            ty += int(h * 0.029)
    return _ig_bytes(img)


def _ig_check(d, chx, cyc, ck, accent):
    d.ellipse([chx, cyc - ck // 2, chx + ck, cyc + ck // 2], fill=accent)
    d.line([(int(chx + ck * 0.27), int(cyc)),
            (int(chx + ck * 0.43), int(cyc + ck * 0.22)),
            (int(chx + ck * 0.75), int(cyc - ck * 0.25))],
           fill=(255, 255, 255), width=max(2, ck // 8))


# ── CHECKLIST variant 1: alternating flat rows ──
def _checklist_rows(spec, items, w, h, primary, accent, bg):
    pad = int(w * 0.055)
    img, d, body_top = _ig_setup(spec, w, h, bg, primary, accent, "Checklist", pad)
    n = len(items)
    top, bot = body_top, h - int(h * 0.06)
    row_h = (bot - top) // n
    cf = _ig_font(int(h * 0.030))
    ck = max(18, min(int(row_h * 0.42), 40))
    tint = _light(accent, 0.85)
    for i, item in enumerate(items):
        ry = top + i * row_h
        if i % 2 == 0:
            d.rounded_rectangle([pad, ry + 2, w - pad, ry + row_h - 2], radius=8, fill=tint)
        cyc = ry + row_h // 2
        chx = pad + int(w * 0.02)
        _ig_check(d, chx, cyc, ck, accent)
        tx = chx + ck + int(w * 0.025)
        lines = _ig_wrap((item or "")[:100], cf, w - tx - pad)[:2]
        ty = cyc - (len(lines) * int(h * 0.034)) // 2
        for ln in lines:
            d.text((tx, ty), ln, font=cf, fill=primary)
            ty += int(h * 0.034)
    return _ig_bytes(img)


# ── CHECKLIST variant 2: rounded accent pills ──
def _checklist_pills(spec, items, w, h, primary, accent, bg):
    pad = int(w * 0.055)
    img, d, body_top = _ig_setup(spec, w, h, bg, primary, accent, "Checklist", pad)
    n = len(items)
    top, bot = body_top, h - int(h * 0.06)
    gap = int(h * 0.02)
    row_h = (bot - top - (n - 1) * gap) // n
    cf = _ig_font(int(h * 0.029), bold=True)
    ck = max(16, min(int(row_h * 0.42), 36))
    tint = _light(accent, 0.82)
    for i, item in enumerate(items):
        ry = top + i * (row_h + gap)
        d.rounded_rectangle([pad, ry, w - pad, ry + row_h], radius=row_h // 2, fill=tint)
        cyc = ry + row_h // 2
        chx = pad + int(row_h * 0.28)
        _ig_check(d, chx, cyc, ck, accent)
        tx = chx + ck + int(w * 0.022)
        for ln in _ig_wrap((item or "")[:90], cf, w - tx - pad - int(row_h * 0.3))[:1]:
            d.text((tx, cyc), ln, font=cf, fill=primary, anchor="lm")
    return _ig_bytes(img)


# ── STATS variant 1: big numbers in a row with dividers ──
def _stats_bigrow(spec, stats, w, h, primary, accent, bg):
    pad = int(w * 0.05)
    img, d, body_top = _ig_setup(spec, w, h, bg, primary, accent, "By the Numbers", pad)
    n = len(stats)
    top, bot = body_top, h - int(h * 0.07)
    cell_w = (w - 2 * pad) // n
    for idx, stat in enumerate(stats):
        cx = pad + idx * cell_w + cell_w // 2
        if idx > 0:
            lx = pad + idx * cell_w
            d.line([(lx, top + int((bot - top) * 0.2)), (lx, bot - int((bot - top) * 0.2))],
                   fill=_darken(bg, 0.88), width=2)
        val = str(stat.get("value") or "—")[:10]
        vsize = int(h * 0.16)
        vf = _ig_font(vsize, bold=True)
        while vf.getbbox(val)[2] > cell_w - int(cell_w * 0.12) and vsize > 24:
            vsize -= 3
            vf = _ig_font(vsize, bold=True)
        d.text((cx, top + int((bot - top) * 0.32)), val, font=vf, fill=accent, anchor="mm")
        lf = _ig_font(int(h * 0.024))
        ly = top + int((bot - top) * 0.52)
        for ln in _ig_wrap((stat.get("label") or "")[:80], lf, cell_w - int(cell_w * 0.12))[:3]:
            d.text((cx, ly), ln, font=lf, fill=_IG["muted"], anchor="ma")
            ly += int(h * 0.030)
    return _ig_bytes(img)


# ── STATS variant 2: vertical list, big number + label ──
def _stats_rows(spec, stats, w, h, primary, accent, bg):
    pad = int(w * 0.05)
    img, d, body_top = _ig_setup(spec, w, h, bg, primary, accent, "By the Numbers", pad)
    n = len(stats)
    top, bot = body_top, h - int(h * 0.07)
    gap = int(h * 0.025)
    row_h = (bot - top - (n - 1) * gap) // n
    shadow = _darken(bg, 0.90)
    numbox_w = int(w * 0.30)
    for idx, stat in enumerate(stats):
        ry = top + idx * (row_h + gap)
        d.rounded_rectangle([pad + 2, ry + 3, w - pad + 2, ry + row_h + 3], radius=14, fill=shadow)
        d.rounded_rectangle([pad, ry, w - pad, ry + row_h], radius=14, fill=(255, 255, 255))
        val = str(stat.get("value") or "—")[:10]
        vsize = int(row_h * 0.5)
        vf = _ig_font(vsize, bold=True)
        while vf.getbbox(val)[2] > numbox_w - 20 and vsize > 20:
            vsize -= 2
            vf = _ig_font(vsize, bold=True)
        d.text((pad + numbox_w // 2, ry + row_h // 2), val, font=vf, fill=accent, anchor="mm")
        d.line([(pad + numbox_w, ry + int(row_h * 0.2)), (pad + numbox_w, ry + int(row_h * 0.8))],
               fill=_darken(bg, 0.9), width=2)
        lf = _ig_font(int(h * 0.026))
        tx = pad + numbox_w + int(w * 0.025)
        lines = _ig_wrap((stat.get("label") or "")[:100], lf, w - tx - pad)[:3]
        ty = ry + row_h // 2 - (len(lines) * int(h * 0.032)) // 2
        for ln in lines:
            d.text((tx, ty), ln, font=lf, fill=primary)
            ty += int(h * 0.032)
    return _ig_bytes(img)


# ── BAR_CHART variant 1: vertical columns ──
def _bar_columns(spec, bars, w, h, primary, accent, bg):
    pad = int(w * 0.055)
    img, d, body_top = _ig_setup(spec, w, h, bg, primary, accent, "Comparison", pad)
    n = len(bars)

    def _fval(b):
        try:
            return float(b.get("value", 0))
        except Exception:
            return 0.0
    max_v = max((_fval(b) for b in bars), default=1) or 1
    top = body_top + int(h * 0.04)
    base = h - int(h * 0.16)
    chart_h = base - top
    gap = int(w * 0.03)
    col_w = (w - 2 * pad - (n - 1) * gap) // n
    vf = _ig_font(int(h * 0.030), bold=True)
    lf = _ig_font(int(h * 0.022))
    for i, bar in enumerate(bars):
        cx = pad + i * (col_w + gap)
        val = _fval(bar)
        bh = max(4, int(chart_h * val / max_v))
        by = base - bh
        fill = accent if i == 0 else primary
        unit = str(bar.get("unit", ""))
        vstr = f"{int(val) if val == int(val) else val}{unit}"
        d.text((cx + col_w // 2, by - int(h * 0.045)), vstr, font=vf, fill=primary, anchor="ma")
        d.rounded_rectangle([cx, by, cx + col_w, base], radius=8, fill=fill)
        for j, ln in enumerate(_ig_wrap(str(bar.get("label", ""))[:30], lf, col_w + gap // 2)[:2]):
            d.text((cx + col_w // 2, base + int(h * 0.02) + j * int(h * 0.026)), ln,
                   font=lf, fill=_IG["muted"], anchor="ma")
    return _ig_bytes(img)


def _render_icongrid(spec, pairs, w, h, primary, accent, bg, kicker):
    """Icon-driven 2-column card grid. Each card: a brand-accent icon disc (icon auto-
    matched from the item text) + bold title + optional description. 100% accurate text."""
    pad = int(w * 0.045)
    img, d, body_top = _ig_setup(spec, w, h, bg, primary, accent, kicker, pad)
    n = len(pairs)
    if not n:
        return _ig_bytes(img)
    cols = 2 if n > 1 else 1
    per = (n + cols - 1) // cols
    top, bot = body_top, h - int(h * 0.05)
    cgap, rgap = int(w * 0.028), int(h * 0.028)
    cw = (w - 2 * pad - (cols - 1) * cgap) // cols
    rh = (bot - top - (per - 1) * rgap) // per
    shadow = _darken(bg, 0.90)
    has_desc = any(dsc for _, dsc in pairs)
    itf = _ig_font(int(h * 0.033), bold=True)
    idf = _ig_font(int(h * 0.021))
    for i, (title_t, desc) in enumerate(pairs):
        col, row = divmod(i, per)
        x = pad + col * (cw + cgap)
        ry = top + row * (rh + rgap)
        d.rounded_rectangle([x + 3, ry + 4, x + cw + 3, ry + rh + 4], radius=16, fill=shadow)
        d.rounded_rectangle([x, ry, x + cw, ry + rh], radius=16, fill=(255, 255, 255))
        disc = int(rh * 0.60)
        dcx, dcy = x + int(rh * 0.5), ry + rh // 2
        d.ellipse([dcx - disc // 2, dcy - disc // 2, dcx + disc // 2, dcy + disc // 2],
                  fill=_light(accent, 0.84))
        try:
            d.text((dcx, dcy), _pick_icon(title_t + " " + desc),
                   font=_ig_fa(int(disc * 0.5)), fill=accent, anchor="mm")
        except Exception:
            pass
        tx = dcx + disc // 2 + int(w * 0.018)
        tw = x + cw - tx - int(w * 0.015)
        tlines = _ig_wrap(title_t, itf, tw)[:1 if has_desc else 2]
        dlines = _ig_wrap(desc, idf, tw)[:2] if desc else []
        blk = len(tlines) * int(h * 0.040) + len(dlines) * int(h * 0.028)
        ty = dcy - blk // 2
        for ln in tlines:
            d.text((tx, ty), ln, font=itf, fill=primary)
            ty += int(h * 0.040)
        for ln in dlines:
            d.text((tx, ty), ln, font=idf, fill=_IG["muted"])
            ty += int(h * 0.028)
    return _ig_bytes(img)


def _render_steps_infographic(spec: dict, w: int, h: int,
                              primary: tuple, accent: tuple, bg: tuple, footer: str,
                              seed: str = "") -> bytes:
    _items = (spec.get("items") or [])[:6]
    if _items:
        _nv = 4 if _FA_OK else 3
        _v = _ig_variant(seed + "steps", _nv)
        if _v == 3 and len(_items) <= 6:
            _pairs = [(it.get("title") or f"Step {i + 1}", _step_desc(it))
                      for i, it in enumerate(_items)]
            return _render_icongrid(spec, _pairs, w, h, primary, accent, bg, "Key Steps")
        if _v == 1: return _steps_timeline(spec, _items[:5], w, h, primary, accent, bg)
        if _v == 2: return _steps_rows(spec, _items[:5], w, h, primary, accent, bg)
        _items = _items[:5]
    img = PILImage.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    _ig_frame(d, w, h, bg)
    pad = int(w * 0.05)
    body_top = _ig_header(d, w, h, primary, accent, spec, "Key Steps", pad)

    items = (spec.get("items") or [])[:5]
    n = len(items)
    foot_h = int(h * 0.085)
    if not n:
        _ig_footer(d, w, h, primary, footer)
        return _ig_bytes(img)

    gap = int(w * 0.022)
    card_w = (w - 2 * pad - (n - 1) * gap) // n
    avail = (h - foot_h) - body_top
    card_h = min(avail, int(h * 0.50))
    cy0 = body_top + (avail - card_h) // 2
    white = (255, 255, 255)
    shadow = _darken(bg, 0.90)

    for i, item in enumerate(items):
        cx = pad + i * (card_w + gap)
        ctr = cx + card_w // 2
        is_last = (i == n - 1)
        fill = primary if is_last else white
        d.rounded_rectangle([cx + 3, cy0 + 4, cx + card_w + 3, cy0 + card_h + 4],
                            radius=14, fill=shadow)
        d.rounded_rectangle([cx, cy0, cx + card_w, cy0 + card_h], radius=14, fill=fill)

        ty = cy0 + int(card_h * 0.12)
        num = str(item.get("number", i + 1))
        d.text((ctr, ty), num, font=_ig_font(int(card_h * 0.17), bold=True),
               fill=(_light(accent, 0.5) if is_last else accent), anchor="ma")
        ty += int(card_h * 0.22)

        tf = _ig_font(int(h * 0.030), bold=True)
        for ln in _ig_wrap(item.get("title") or f"Step {i + 1}", tf, card_w - int(card_w * 0.16))[:2]:
            d.text((ctr, ty), ln, font=tf, fill=(white if is_last else primary), anchor="ma")
            ty += int(h * 0.036)
        ty += int(h * 0.006)

        pts = item.get("points")
        desc = " ".join(pts) if isinstance(pts, list) else (pts or item.get("desc") or "")
        pf = _ig_font(int(h * 0.020))
        for ln in _ig_wrap(desc[:110], pf, card_w - int(card_w * 0.16))[:3]:
            if ty > cy0 + card_h - int(card_h * 0.08):
                break
            d.text((ctr, ty), ln, font=pf,
                   fill=(_light(primary, 0.7) if is_last else _IG["muted"]), anchor="ma")
            ty += int(h * 0.026)

        if not is_last:
            d.text((cx + card_w + gap // 2, cy0 + card_h // 2), "›",
                   font=_ig_font(int(card_h * 0.24), bold=True), fill=accent, anchor="mm")

    _ig_footer(d, w, h, primary, footer)
    return _ig_bytes(img)


def _render_stats_infographic(spec: dict, w: int, h: int,
                              primary: tuple, accent: tuple, bg: tuple, footer: str,
                              seed: str = "") -> bytes:
    _stats = (spec.get("stats") or [])[:4]
    if _stats:
        _v = _ig_variant(seed + "stats", 3)
        if _v == 1: return _stats_bigrow(spec, _stats, w, h, primary, accent, bg)
        if _v == 2: return _stats_rows(spec, _stats, w, h, primary, accent, bg)
    img = PILImage.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    _ig_frame(d, w, h, bg)
    pad = int(w * 0.05)
    body_top = _ig_header(d, w, h, primary, accent, spec, "By the Numbers", pad)

    stats = (spec.get("stats") or [])[:4]
    n = len(stats)
    foot_h = int(h * 0.085)
    if not n:
        _ig_footer(d, w, h, primary, footer)
        return _ig_bytes(img)

    cols = 2 if n == 4 else min(n, 3)
    rows = (n + cols - 1) // cols
    gap = int(w * 0.022)
    top = body_top
    bot = h - foot_h
    cell_w = (w - 2 * pad - (cols - 1) * gap) // cols
    cell_h = (bot - top - (rows - 1) * gap) // rows
    shadow = _darken(bg, 0.90)

    for idx, stat in enumerate(stats):
        row, col = divmod(idx, cols)
        cx = pad + col * (cell_w + gap)
        cy = top + row * (cell_h + gap)
        d.rounded_rectangle([cx + 3, cy + 4, cx + cell_w + 3, cy + cell_h + 4], radius=16, fill=shadow)
        d.rounded_rectangle([cx, cy, cx + cell_w, cy + cell_h], radius=16, fill=(255, 255, 255))
        d.rounded_rectangle([cx, cy, cx + cell_w, cy + 7], radius=16, fill=accent)

        val = str(stat.get("value") or "—")[:12]
        vsize = int(cell_h * 0.40)
        vf = _ig_font(vsize, bold=True)
        while vf.getbbox(val)[2] > cell_w - int(cell_w * 0.12) and vsize > 22:
            vsize -= 3
            vf = _ig_font(vsize, bold=True)

        label = (stat.get("label") or "")[:90]
        lf = _ig_font(int(h * 0.024))
        lh = int(h * 0.030)
        llines = _ig_wrap(label, lf, cell_w - int(cell_w * 0.14))[:3]
        block_h = vsize + int(h * 0.02) + len(llines) * lh
        vy = cy + 8 + max(int(h * 0.02), (cell_h - 8 - block_h) // 2)
        d.text((cx + cell_w // 2, vy + vsize // 2), val, font=vf, fill=primary, anchor="mm")
        ly = vy + vsize + int(h * 0.02)
        for ll in llines:
            d.text((cx + cell_w // 2, ly), ll, font=lf, fill=_IG["muted"], anchor="ma")
            ly += lh

    _ig_footer(d, w, h, primary, footer)
    return _ig_bytes(img)


def _render_checklist_infographic(spec: dict, w: int, h: int,
                                  primary: tuple, accent: tuple, bg: tuple, footer: str,
                                  seed: str = "") -> bytes:
    _items = (spec.get("items") or [])[:8]
    if _items:
        _nv = 4 if _FA_OK else 3
        _v = _ig_variant(seed + "checklist", _nv)
        if _v == 3 and len(_items) <= 6:
            _pairs = [(str(s), "") for s in _items]
            return _render_icongrid(spec, _pairs, w, h, primary, accent, bg, "Checklist")
        if _v == 1: return _checklist_rows(spec, _items, w, h, primary, accent, bg)
        if _v == 2 and len(_items) <= 6: return _checklist_pills(spec, _items, w, h, primary, accent, bg)
    img = PILImage.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    _ig_frame(d, w, h, bg)
    pad = int(w * 0.055)
    body_top = _ig_header(d, w, h, primary, accent, spec, "Checklist", pad)

    items = (spec.get("items") or [])[:8]
    n = len(items)
    foot_h = int(h * 0.085)
    if not n:
        _ig_footer(d, w, h, primary, footer)
        return _ig_bytes(img)

    cols = 2 if n > 4 else 1
    per_col = (n + cols - 1) // cols
    col_gap = int(w * 0.03)
    top = body_top
    bot = h - foot_h
    col_w = (w - 2 * pad - (cols - 1) * col_gap) // cols
    row_gap = int(h * 0.022)
    row_h = (bot - top - (per_col - 1) * row_gap) // per_col
    cf = _ig_font(int(h * 0.028))
    ck = max(18, min(int(row_h * 0.40), 38))
    shadow = _darken(bg, 0.91)

    for idx, item in enumerate(items):
        col, row = divmod(idx, per_col)
        cx = pad + col * (col_w + col_gap)
        ry = top + row * (row_h + row_gap)
        d.rounded_rectangle([cx + 2, ry + 3, cx + col_w + 2, ry + row_h + 3], radius=12, fill=shadow)
        d.rounded_rectangle([cx, ry, cx + col_w, ry + row_h], radius=12, fill=(255, 255, 255))
        cyc = ry + row_h // 2
        chx = cx + int(col_w * 0.04)
        d.ellipse([chx, cyc - ck // 2, chx + ck, cyc + ck // 2], fill=accent)
        d.line([(int(chx + ck * 0.27), int(cyc)),
                (int(chx + ck * 0.43), int(cyc + ck * 0.22)),
                (int(chx + ck * 0.75), int(cyc - ck * 0.25))],
               fill=(255, 255, 255), width=max(2, ck // 8))
        tx = chx + ck + int(col_w * 0.045)
        lines = _ig_wrap((item or "")[:90], cf, cx + col_w - tx - int(col_w * 0.04))[:2]
        ty = cyc - (len(lines) * int(h * 0.033)) // 2
        for ln in lines:
            d.text((tx, ty), ln, font=cf, fill=primary)
            ty += int(h * 0.033)

    _ig_footer(d, w, h, primary, footer)
    return _ig_bytes(img)


def _render_bar_chart_infographic(spec: dict, w: int, h: int,
                                  primary: tuple, accent: tuple, bg: tuple, footer: str,
                                  seed: str = "") -> bytes:
    _bars = (spec.get("bars") or [])[:6]
    if _bars and _ig_variant(seed + "bar", 2) == 1:
        return _bar_columns(spec, _bars, w, h, primary, accent, bg)
    img = PILImage.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    _ig_frame(d, w, h, bg)
    pad = int(w * 0.055)
    body_top = _ig_header(d, w, h, primary, accent, spec, "Comparison", pad)

    bars = (spec.get("bars") or [])[:6]
    n = len(bars)
    foot_h = int(h * 0.085)
    if not n:
        _ig_footer(d, w, h, primary, footer)
        return _ig_bytes(img)

    def _fval(b):
        try:
            return float(b.get("value", 0))
        except Exception:
            return 0.0
    max_v = max((_fval(b) for b in bars), default=1) or 1

    lf = _ig_font(int(h * 0.026), bold=True)
    vf = _ig_font(int(h * 0.026), bold=True)
    label_w = min(int(w * 0.24),
                  max((lf.getbbox(str(b.get("label", "")))[2] for b in bars), default=0) + 18)
    bar_x = pad + label_w + int(w * 0.015)
    bar_max_w = w - bar_x - pad
    top = body_top + int(h * 0.02)
    bot = h - foot_h
    bar_h = min(int(h * 0.10), (bot - top - (n - 1) * int(h * 0.03)) // max(n, 1))
    gap = (bot - top - n * bar_h) // (n + 1)
    track = _darken(bg, 0.92)

    for i, bar in enumerate(bars):
        by = top + gap + i * (bar_h + gap)
        val = _fval(bar)
        bw = max(0, int(bar_max_w * val / max_v))
        fill = accent if i == 0 else primary

        d.text((pad + label_w, by + bar_h // 2), str(bar.get("label", ""))[:42],
               font=lf, fill=primary, anchor="rm")
        d.rounded_rectangle([bar_x, by, bar_x + bar_max_w, by + bar_h], radius=8, fill=track)
        if bw > 6:
            d.rounded_rectangle([bar_x, by, bar_x + bw, by + bar_h], radius=8, fill=fill)

        unit = str(bar.get("unit", ""))
        val_str = f"{int(val) if val == int(val) else val}{unit}"
        if bw > w * 0.12:
            d.text((bar_x + bw - 12, by + bar_h // 2), val_str, font=vf,
                   fill=(255, 255, 255), anchor="rm")
        else:
            d.text((bar_x + bw + 12, by + bar_h // 2), val_str, font=vf,
                   fill=_IG["muted"], anchor="lm")

    _ig_footer(d, w, h, primary, footer)
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


def _render_infographic(spec: dict, w: int, h: int,
                        brand_color: str = "#1A3A5C", footer: str = "", seed: str = "") -> bytes:
    """Dispatch to the correct renderer. The whole palette (primary/accent/bg) is
    derived from the client brand color. Never raises — always returns valid bytes.
    `seed` (brand + blog title) selects a layout VARIANT so different clients/blogs get
    visibly different infographic styles instead of one fixed look per type."""
    primary, accent, bg = _ig_palette(brand_color)
    if not seed:
        seed = f"{brand_color}|{spec.get('title', '')}"
    try:
        t = spec.get("infographic_type", "steps")
        if t == "stats":     return _render_stats_infographic(spec, w, h, primary, accent, bg, footer, seed)
        if t == "checklist": return _render_checklist_infographic(spec, w, h, primary, accent, bg, footer, seed)
        if t == "bar_chart": return _render_bar_chart_infographic(spec, w, h, primary, accent, bg, footer, seed)
        return _render_steps_infographic(spec, w, h, primary, accent, bg, footer, seed)
    except Exception as e:
        img = PILImage.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)
        draw.text((40, 40), (spec.get("title") or "Infographic"),
                  font=_ig_font(22, bold=True), fill=primary)
        draw.text((40, 82), f"Render error: {str(e)[:100]}",
                  font=_ig_font(15), fill=_IG["muted"])
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
                    raise
            else:
                s.update(label="Failed to fetch blog", state="error")
                st.error(str(e))
                raise
        except Exception as e:
            s.update(label="Failed to fetch blog", state="error")
            st.error(str(e))
            raise

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
            raise

    # Step 3b: Alt texts
    with st.status("Generating alt texts...", expanded=True) as _st:
        alt_texts = []
        for i, sl in enumerate(slots, 1):
            if sl.get("type") == "infographic":
                alt = sl.get("title") or f"Infographic {i}"
            else:
                alt = generate_alt_text_for(sl.get("description", ""), title, index=i)
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
    _ig_foot = _brand_footer(client_slug, url)

    for i, (sl, alt) in enumerate(zip(slots, alt_texts), 1):
        gen_prog.progress(i / len(slots), text=f"Generating image {i} of {len(slots)}...")

        if sl.get("type") == "infographic":
            ig_label = sl.get("infographic_type", "").upper()
            st.write(f"📊 Rendering infographic [{ig_label}]: **{sl.get('title', '')}** (brand: `{_brand_color}`)")
            final_bytes, final_ext = None, "jpg"
            try:
                raw = _render_infographic(sl, target_w, target_h,
                                          brand_color=_brand_color, footer=_ig_foot)
                raw = _apply_corner_logo(raw, _brand_logo)
                opt_bytes, ext = optimize_image(raw, max_kb=200)
                final_bytes, final_ext = opt_bytes, ext
            except Exception as e:
                st.error(f"⛔ Infographic render failed: {e}")
            if final_bytes:
                path = output_dir / f"image_{i:02d}.{final_ext}"
                path.write_bytes(final_bytes)
            results.append({"index": i, "bytes": final_bytes, "ext": final_ext,
                             "size_kb": round(len(final_bytes) / 1024, 1) if final_bytes else 0,
                             "alt": alt, "prompt": sl.get("title", ""), "type": "infographic",
                             "spec": sl, "brand_color": _brand_color, "brand_logo": _brand_logo,
                             "footer": _ig_foot,
                             "status": "ok" if final_bytes else "failed: render error",
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
    for row in range(0, len(results), 4):
        cols = st.columns(4)
        for ci, result in enumerate(results[row:row + 4]):
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
                   page_icon="assets/logo.png", layout="wide")

# ── Branding: load logo as base64 ─────────────────────────────────────────────
_logo_path = Path("assets/logo.png")
_logo_b64 = ""
if _logo_path.exists():
    _logo_b64 = base64.b64encode(_logo_path.read_bytes()).decode()

st.markdown(f"""
<style>
/* ── Global ── */
html, body, [data-testid="stApp"] {{ background-color: #0D0D0D; }}
[data-testid="stMainBlockContainer"] {{ padding-top: 2.2rem; max-width: 1400px; }}

/* ── Sidebar ── */
[data-testid="stSidebar"] {{ background: #141414; border-right: 1px solid #2a2a2a; }}
[data-testid="stSidebar"] .block-container {{ padding-top: 1.4rem; }}
.msp-brand {{ display:flex; align-items:center; gap:10px; padding-bottom:16px;
    border-bottom:1px solid #2a2a2a; margin-bottom:14px; }}
.msp-brand img {{ height:26px; }}
.msp-brand .mark {{ width:26px; height:26px; border-radius:7px;
    background:linear-gradient(135deg,#35EDED,#0aa); }}
.msp-brand b {{ font-size:15px; color:#fff; letter-spacing:-0.01em; }}
[data-testid="stSidebar"] h5 {{ font-size:11px !important; text-transform:uppercase;
    letter-spacing:.09em; color:#8a8a8a !important; margin:18px 0 4px !important; font-weight:700; }}

/* ── Main header ── */
.msp-head {{ display:flex; align-items:center; justify-content:space-between;
    padding:2px 0 6px 0; }}
.msp-head h1 {{ font-size:24px; font-weight:800; color:#fff; letter-spacing:-0.02em; margin:0; }}
.msp-head .badges {{ display:flex; gap:8px; }}
.msp-head .badge {{ font-size:11px; padding:4px 11px; border-radius:20px;
    background:#1a1a1a; border:1px solid #2a2a2a; color:#8a8a8a; }}
.msp-head .badge.live {{ color:#3df03d; border-color:rgba(60,240,60,.3); }}
.msp-head .badge.cy {{ color:#35EDED; border-color:rgba(53,237,237,.3); }}
.msp-sub {{ color:#8a8a8a; font-size:13px; margin:0 0 18px 0;
    border-bottom:1px solid #2a2a2a; padding-bottom:16px; }}

/* ── Primary buttons → cyan ── */
.stButton > button[kind="primary"] {{ background:#35EDED !important; color:#000 !important;
    border:none !important; font-weight:700 !important; border-radius:8px !important; }}
.stButton > button[kind="primary"]:hover {{ background:#20d0d0 !important; }}
.stButton > button[kind="secondary"] {{ border-color:#35EDED !important; color:#35EDED !important;
    border-radius:8px !important; }}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {{ border-bottom:2px solid #1A1A1A; gap:4px; }}
.stTabs [aria-selected="true"] {{ color:#35EDED !important; border-bottom-color:#35EDED !important; }}

/* ── Image cards ── */
[data-testid="stImage"] img {{ border-radius:10px; border:1px solid #2a2a2a; }}

/* ── Alerts / dividers ── */
.stAlert[data-baseweb="notification"] {{ border-left-color:#35EDED; }}
hr {{ border-color:#2a2a2a; }}
[data-baseweb="base-input"] button {{ display:none !important; }}
</style>
""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════════════════
# SIDEBAR — all configuration (templates, Webflow key, image model)
# ════════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown(
        ("<div class='msp-brand'><img src='data:image/png;base64," + _logo_b64
         + "'><b>Image Generator</b></div>") if _logo_b64
        else "<div class='msp-brand'><span class='mark'></span><b>Image Generator</b></div>",
        unsafe_allow_html=True,
    )

    # ── Figma templates (FIRST so the Webflow key auto-fills per client) ──
    _pre_clients = get_figma_clients()
    st.markdown("##### 🎨 Figma Templates")
    _selected_slug = ""
    if _pre_clients:
        _tpl_labels = [_client_display_name(c) for c in _pre_clients]
        _selected_tpl = st.selectbox(
            "tpl_select", _tpl_labels, label_visibility="collapsed",
            key="figma_tpl_dropdown",
        )
        _selected_slug = _pre_clients[_tpl_labels.index(_selected_tpl)]
        if st.button("🗑️ Delete template", key="del_tpl_btn", use_container_width=True):
            _cache = _load_node_cache()
            if _selected_slug in _cache:
                del _cache[_selected_slug]
                _save_node_cache(_cache)
                st.success(f"Deleted **{_selected_tpl}** ✓")
                st.rerun()
    else:
        st.caption("No templates saved yet — add one below.")

    # ── Webflow API key (auto-fills from LOCAL keys file when dropdown changes) ──
    _default_key = _get_client_key(_selected_slug)
    _prev_slug = st.session_state.get("_prev_client_slug", "")
    if _selected_slug != _prev_slug:
        st.session_state["_prev_client_slug"] = _selected_slug
        st.session_state["a_key"] = _default_key
    elif not st.session_state.get("a_key") and _default_key:
        st.session_state["a_key"] = _default_key

    st.markdown("##### 🔑 Webflow API Key")
    a_api_key = st.text_input(
        "Webflow API Key", type="password", label_visibility="collapsed",
        placeholder="Paste key → 💾 Save", key="a_key",
    )
    if st.button("💾 Save key for this client", use_container_width=True, key="save_key_btn"):
        if a_api_key.strip() and _selected_slug:
            # Saved to the local-only keys file (never committed). On the live app this
            # persists only for the running container — re-enter after a redeploy.
            _save_client_key(_selected_slug, a_api_key.strip())
            st.success(f"Saved locally for **{_client_display_name(_selected_slug)}** ✓")

    # ── Add a new client template ──
    _new_tpl_url = st.text_input(
        "add_tpl", label_visibility="collapsed",
        placeholder="Add client: paste Figma frame link", key="add_tpl_url",
    )
    if st.button("➕ Add template", key="add_tpl_btn", use_container_width=True):
        if _new_tpl_url.strip():
            with st.spinner("Fetching from Figma..."):
                _ok, _msg, _ = _add_client_from_figma_url(_new_tpl_url.strip())
            if _ok:
                st.success(_msg)
                st.rerun()
            else:
                st.error(_msg)

    # ── Image model ──
    st.markdown("##### 🤖 Image Model")
    _model_opts = {
        "🤖 Auto (GPT Image 2 → Grok)": "auto",
        "🎨 GPT Image 2 (best quality)": "gpt2",
        "⚡ Grok Imagine (fallback)":     "grok",
    }
    _model_label = st.radio(
        "model", list(_model_opts.keys()), label_visibility="collapsed",
        key="model_test_radio",
    )
    st.session_state["_model_choice"] = _model_opts[_model_label]

# ════════════════════════════════════════════════════════════════════════════════
# MAIN — header + tabs
# ════════════════════════════════════════════════════════════════════════════════
_live_badges = (
    "<span class='badge live'>● Live</span>"
    "<span class='badge cy'>Kie API</span>"
    "<span class='badge'>Gemini 2.0</span>"
) if KIE_API_KEY else "<span class='badge' style='color:#ff5b5b;border-color:rgba(255,91,91,.4)'>● KIE key missing</span>"
st.markdown(
    f"<div class='msp-head'><h1>Blog Image Generator</h1><div class='badges'>{_live_badges}</div></div>"
    "<div class='msp-sub'>Turn any Webflow blog post into on-brand, MSP-themed visuals — automatically.</div>",
    unsafe_allow_html=True,
)
if not KIE_API_KEY:
    st.error("🔴 **KIE_API_KEY missing** — add it to .env to enable image generation.")

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
                    raw = _render_infographic(
                        redo_slot, DEFAULT_WIDTH, DEFAULT_HEIGHT,
                        brand_color=st.session_state.get("m_brand_color", "#1A3A5C"),
                        footer=st.session_state.get("m_footer", ""))
                    new_desc = redo_slot.get("title", "")
                else:
                    # Chain off the CURRENT prompt so each redo moves to a new scene
                    cur_result = st.session_state["m_results"][redo_i - 1]
                    original_desc = cur_result.get("prompt") or redo_slot.get("description", "")
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
                    alt = generate_alt_text_for(sl.get("description", ""), title, index=i)
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
        st.session_state["m_brand_color"] = _m_brand_color
        _m_foot = _brand_footer("", url)
        st.session_state["m_footer"] = _m_foot

        for i, (sl, alt) in enumerate(zip(slots, alt_texts), 1):
            gen_prog.progress(i / len(slots), text=f"Generating image {i} of {len(slots)}...")

            if sl.get("type") == "infographic":
                ig_label = sl.get("infographic_type", "").upper()
                st.write(f"📊 Rendering infographic [{ig_label}]: **{sl.get('title', '')}** (brand: `{_m_brand_color}`)")
                final_bytes, final_ext = None, "jpg"
                try:
                    raw = _render_infographic(sl, DEFAULT_WIDTH, DEFAULT_HEIGHT,
                                              brand_color=_m_brand_color, footer=_m_foot)
                    raw = _apply_corner_logo(raw, _m_brand_logo)
                    opt_bytes, ext = optimize_image(raw, max_kb=200)
                    final_bytes, final_ext = opt_bytes, ext
                except Exception as e:
                    st.error(f"⛔ Infographic render failed: {e}")
                results.append({
                    "index": i, "bytes": final_bytes, "ext": final_ext,
                    "size_kb": round(len(final_bytes) / 1024, 1) if final_bytes else 0,
                    "alt": alt, "prompt": sl.get("title", ""), "type": "infographic",
                    "spec": sl, "brand_color": _m_brand_color, "brand_logo": _m_brand_logo,
                    "footer": _m_foot,
                    "status": "ok" if final_bytes else "failed: render error",
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
        for row in range(0, len(results), 4):
            cols = st.columns(4)
            for ci, result in enumerate(results[row:row + 4]):
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
                        main_alt = _gemini_text(
                            "Write alt text for a blog featured image. Max 80 characters. "
                            "No quotes. Format: '<topic> blog featured image' or similar short phrase.",
                            f"Blog title: {blog_title}",
                            max_tokens=30, temperature=0.5,
                        ) or f"{blog_title[:70]} — featured image"
                        if len(main_alt) > 80:
                            main_alt = main_alt[:80].rsplit(' ', 1)[0]
                        img_update[mkey] = {"url": murl, "alt": main_alt}
                        st.write(f"✓ Main image uploaded → `{mkey}` | alt: `{main_alt}`")
                    else:
                        st.warning(f"⚠️ No image field found for main image. Fields: {list(img_fields.keys())}")

                if thumb_bytes:
                    if tkey:
                        jbytes, ext = _to_jpeg(thumb_bytes)
                        turl = wf.upload_asset(site_id, jbytes, f"thumbnail.{ext}")
                        thumb_alt = _gemini_text(
                            "Write alt text for a blog thumbnail image. Max 80 characters. "
                            "No quotes. Format: '<topic> blog thumbnail' or similar short phrase.",
                            f"Blog title: {blog_title}",
                            max_tokens=30, temperature=0.5,
                        ) or f"{blog_title[:70]} — thumbnail"
                        if len(thumb_alt) > 80:
                            thumb_alt = thumb_alt[:80].rsplit(' ', 1)[0]
                        img_update[tkey] = {"url": turl, "alt": thumb_alt}
                        st.write(f"✓ Thumbnail uploaded → `{tkey}` | alt: `{thumb_alt}`")
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

    # Infographics stay infographics — re-render from the stored spec (never a photo)
    if result.get("type") == "infographic":
        with st.status(f"Re-rendering infographic {img_idx} — {blog_state['slug']}...",
                       expanded=True) as s:
            spec = result.get("spec")
            # Guard: an infographic generated by an older run has no saved data —
            # rendering it would produce a near-blank card. Keep the current image instead.
            if not spec or not any(spec.get(k) for k in ("items", "stats", "bars")):
                s.update(label="Can't re-render — no infographic data saved", state="error")
                st.warning("This infographic was made by an older version, so its data "
                           "wasn't saved. **Re-generate the whole blog** to refresh it — "
                           "then redo will work and stay a proper infographic.")
                return
            try:
                raw = _render_infographic(spec, DEFAULT_WIDTH, DEFAULT_HEIGHT,
                                          brand_color=result.get("brand_color", "#1A3A5C"),
                                          footer=result.get("footer", ""))
                raw = _apply_corner_logo(raw, result.get("brand_logo"))
                opt_bytes, ext = optimize_image(raw, max_kb=200)
                (output_dir / f"image_{img_idx:02d}.{ext}").write_bytes(opt_bytes)
                result.update({"bytes": opt_bytes, "ext": ext,
                               "size_kb": round(len(opt_bytes) / 1024, 1),
                               "status": "ok", "defect_reason": ""})
                s.update(label=f"Infographic {img_idx} re-rendered ✓ (kept as infographic)",
                         state="complete")
            except Exception as e:
                s.update(label="Infographic re-render failed", state="error")
                st.error(f"Re-render failed: {e}")
        return

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

    for row_start in range(0, len(results), 4):
        cols = st.columns(4)
        for ci, result in enumerate(results[row_start:row_start + 4]):
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
    if _selected_slug:
        st.caption(f"Template: **{_client_display_name(_selected_slug)}**  ·  "
                   "manage templates & API key in the left sidebar ←")
    else:
        st.caption("Add a Figma template in the left sidebar ← to get started.")

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
        for _bi, raw_url in enumerate(urls):
          url = ("https://" + raw_url) if not raw_url.startswith("http") else raw_url
          try:

            # Resolve the real slug via GET (follows redirects) + canonical tag fallback.
            # HEAD is unreliable on many Webflow sites; GET + r.url is the ground truth.
            def _resolve_slug(start_url: str) -> tuple[str, str]:
                """Return (final_url, slug) using GET redirect chain + <link rel=canonical>.
                Strips query strings / fragments so the slug is a valid folder name."""
                def _slug_of(u: str) -> str:
                    u = re.sub(r"[?#].*$", "", u or "").rstrip("/")
                    return u.split("/")[-1]
                _orig = re.sub(r"[?#].*$", "", start_url).rstrip("/")
                try:
                    _r = requests.get(
                        start_url, allow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                        timeout=15,
                    )
                    # If the public page 404s (draft/unpublished/deleted), Webflow's
                    # 404 page sets canonical to ".../404" — do NOT trust it. Keep the
                    # original slug so the draft/CMS fallback can still find the post.
                    if not _r.ok:
                        return _orig, _slug_of(_orig)
                    # 1. Check <link rel="canonical"> — most reliable on Webflow
                    _soup = BeautifulSoup(_r.content, "html.parser")
                    _canon = _soup.find("link", rel="canonical")
                    if _canon and _canon.get("href"):
                        _cu = re.sub(r"[?#].*$", "", _canon["href"]).rstrip("/")
                        _cslug = _slug_of(_cu)
                        if _cslug and _cslug.lower() != "404":   # ignore canonical → /404
                            return _cu, _cslug
                    # 2. Fall back to the final URL after redirects
                    _fu = re.sub(r"[?#].*$", "", _r.url).rstrip("/")
                    _fslug = _slug_of(_fu)
                    if _fslug and _fslug.lower() != "404":
                        return _fu, _fslug
                    # 3. Last resort: the original URL's own slug
                    return _orig, _slug_of(_orig)
                except Exception:
                    return _orig, _slug_of(_orig)

            final_url, slug = _resolve_slug(url)
            # Sanitize for Windows folder name — invalid chars (? : * | < > ") would crash mkdir
            safe_slug = re.sub(r"[^A-Za-z0-9_\-]", "-", slug).strip("-") or "blog"
            output_dir = Path("generated_images") / safe_slug
            output_dir.mkdir(parents=True, exist_ok=True)

            st.markdown(f"---\n### 📝 Blog {_bi + 1} of {len(urls)}: `{slug}`")
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

            try:
                title, image_urls, results, _ = run_workflow(
                    url, output_dir,
                    wf_fallback=wf,
                    collection_id_fallback=collection_id,
                    item_id_fallback=item_id,
                    client_slug=matched_client,
                )
            except Exception as _wf_err:
                st.error(f"⛔ Generation failed for `{slug}`: {_wf_err}")
                batch.append({"url": url, "slug": slug, "title": slug,
                              "results": [], "image_urls": [], "wf_info": {},
                              "uploaded": False, "error": str(_wf_err)})
                st.session_state["batch"] = batch
                continue

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
          except Exception as _loop_err:
            # Catch-all: one bad URL must never halt the rest of the batch
            st.error(f"⛔ Unexpected error on `{url}` — skipped, continuing batch: {_loop_err}")
            batch.append({"url": url, "slug": url.rstrip('/').split('/')[-1],
                          "title": url, "results": [], "image_urls": [],
                          "wf_info": {}, "uploaded": False, "error": str(_loop_err)})
            st.session_state["batch"] = batch
            continue

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
