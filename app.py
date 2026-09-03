import os
import re
import time
import random
import base64
import io
import csv
import json
import math
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
Example: • [MEETING TABLE] Two IT managers seated at a conference table reviewing a printed network diagram together, hands resting on the table.

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
# ~100 scenes across 3 visual categories. Each scene fixes the ENVIRONMENT +
# framing/composition (angle, distance, people count); the ACTIVITY is filled in
# per blog by Gemini (content-driven, RULE 2). More scenes + varied framing =
# generations that don't look repetitive. SCENE_TYPES and the _SCENE_* category
# sets are derived from these lists so no label is ever left uncategorized.
# NOTE: deliberately NO server-room / server-rack scenes (past off-topic issue).

_DESK_SCENES = [   # one person seated at an individual computer/desk
    ("SOLO WORKSTATION", "one person alone at a single desk with a laptop, shot from a slight side angle"),
    ("DUAL MONITOR DESK", "one person at a corner desk with two large monitors, viewed over their shoulder"),
    ("WINDOW SEAT LAPTOP", "one person on a laptop at a desk beside a large office window with daylight coming in"),
    ("LOUNGE AREA LAPTOP", "one person working on a laptop in a casual office lounge with soft seating"),
    ("PHONE CALL AT DESK", "one person at a desk with a phone to one ear, notepad open in front of them"),
    ("FOCUSED READING", "one person at a desk reading a printed report, pen in hand, monitor off to the side"),
    ("HELP DESK COUNTER", "one person at a help-desk counter wearing a headset, two monitors in front"),
    ("LAPTOP HANDS CLOSE-UP", "tight close-up of one person's hands on a laptop keyboard, their face softly blurred behind"),
    ("OVER-SHOULDER MONITOR", "over-the-shoulder framing of one person studying a dark dashboard on a monitor"),
    ("STICKY-NOTE DESK", "one person at a desk edged with sticky notes, adding a note to one of them"),
    ("HEADSET SUPPORT DESK", "one person in a headset at a support desk mid-conversation, gesturing with one hand"),
    ("EVENING DESK LAMP", "one person at a desk lit mostly by a warm desk lamp in a dim after-hours office"),
    ("NOTEBOOK AND SCREEN", "one person splitting attention between a paper notebook and a monitor"),
    ("CORNER CUBICLE WIDE", "wide framing of one person seated in a corner cubicle with the office visible beyond"),
    ("SIDE-PROFILE TYPING", "strict side profile of one person typing, monitor glow lighting their face"),
    ("BACK-TO-CAMERA DESK", "one person shot from behind at their desk, facing a wall-mounted monitor"),
    ("TABLET AT DESK", "one person at a desk reviewing something on a tablet held in both hands"),
    ("BAR-HEIGHT STOOL DESK", "one person perched on a stool at a bar-height bench desk with a laptop"),
    ("TWO-SCREEN TERMINAL", "one person between two monitors showing dark terminal windows"),
    ("RECLINED THINKING", "one person leaning back in an office chair, hands laced behind head, considering a screen"),
    ("LEANING-IN FOCUS", "one person leaning close toward a single monitor, elbows on the desk"),
    ("DESK PHONE AND KEYBOARD", "one person cradling a desk phone on their shoulder while typing"),
    ("HOT-DESK OPEN BENCH", "one person alone at a long shared hot-desk bench, other seats empty"),
    ("QUIET POD SEAT", "one person working inside an enclosed single-person acoustic office pod"),
    ("DESK NEAR PLANTS", "one person at a desk framed by tall office plants in the foreground"),
    ("DOWN-ANGLE DESK", "high downward angle looking onto one person at a tidy desk from above"),
    ("LOW-ANGLE DESK", "low angle looking slightly up at one person seated at their monitor"),
    ("MORNING LIGHT DESK", "one person at a desk with long soft morning light raking across the surface"),
    ("HANDWRITING AT DESK", "one person writing notes by hand on a legal pad beside a closed laptop"),
    ("EXTERNAL MONITOR LAPTOP", "one person with a laptop connected to a larger external monitor on a riser"),
    ("SWIVELLED CHAIR", "one person turned sideways in a swivel chair, glancing back at their screen"),
    ("DESK BY BOOKSHELF", "one person at a desk set against a low bookshelf of binders and manuals"),
    ("STANDING-MAT DESK", "one person standing on an anti-fatigue mat at a raised desk, weight on one leg"),
    ("PRINTOUT AND SCREEN", "one person holding a printout up beside their monitor to compare the two"),
    ("EARBUDS FOCUS DESK", "one person with earbuds in, focused on a monitor, fingers resting on the keyboard"),
    ("WIDE SOLO OPEN FLOOR", "very wide shot of one person at a lone desk on a large open office floor"),
    ("DESK EDGE PERCH", "one person perched on the edge of their own desk reading from a tablet"),
    ("DUSK WINDOW DESK", "one person at a window-side desk at dusk, city lights softly out of focus behind"),
    ("HEADPHONES DUAL SCREEN", "one person in over-ear headphones scanning across two side-by-side monitors"),
    ("CLOSE DESK DETAIL", "close framing on one person's face and hands at a desk, background thrown out of focus"),
]

_GROUP_SCENES = [  # several people together at a table / shared screen
    ("MEETING TABLE", "two to four people seated around a meeting-room table in discussion"),
    ("SMALL CONFERENCE ROOM", "three people seated around a small table inside a glass-walled conference room"),
    ("SIDE-BY-SIDE PAIR", "two people seated side by side reviewing something on one shared screen"),
    ("BOARDROOM WIDE", "wide shot of five people spaced around a long boardroom table, one leaning in"),
    ("LAPTOP PAIR REVIEW", "two people leaning together over a single open laptop at a table"),
    ("THREE AT ONE MONITOR", "three people clustered around one desktop monitor, leaning in to discuss what's on screen"),
    ("CROSS-TABLE DISCUSSION", "two people facing each other across a small table, papers between them"),
    ("PAPER REVIEW PAIR", "two people at a table comparing two printed documents side by side"),
    ("HUDDLE ROOM BENCH", "three people on a bench seat along a huddle-room wall facing a small screen"),
    ("ROUND TABLE FOUR", "four people around a round café-style table in an office breakout space"),
    ("MENTOR OVER SHOULDER", "one person seated at a desk while a colleague leans over their shoulder to look"),
    ("STANDUP AT DESK CLUSTER", "three colleagues gathered standing around one seated person's desk"),
    ("GLASS ROOM FROM OUTSIDE", "a meeting of three people seen through the glass wall of a conference room"),
    ("TABLE WITH LAPTOPS", "four people at a table each with a laptop, mid-collaboration, one talking"),
    ("CORNER SOFA MEETING", "three people on a corner sofa arrangement with a low table, talking casually"),
    ("PAIR AT STANDING TABLE", "two people standing at a high poseur table with a laptop between them"),
    ("NOTE-TAKER AND SPEAKER", "two people at a table, one speaking while the other takes notes on a laptop"),
    ("SEMICIRCLE BRIEFING", "four people seated in a loose semicircle facing one colleague briefing them"),
    ("SHARED SCREEN LOOK", "two seated people looking at a monitor together, discussing what's on screen"),
    ("CLIENT-STYLE MEETING", "two people on one side of a table facing a third across it, handshake-neutral"),
    ("BREAKOUT TABLE FIVE", "five people around a breakout table mid-discussion, papers and a laptop out"),
    ("PAIR REVIEWING TABLET", "two people seated close, both looking down at one tablet held between them"),
    ("TABLE FROM ABOVE", "high angle looking down onto four people working around a table"),
    ("QUIET TWO-PERSON DESK", "two colleagues at adjacent desks turned toward each other in quiet discussion"),
    ("WORKSHOP LONG TABLE", "wide shot of six people along a long workshop table with laptops and notes"),
]

_ACTIVE_SCENES = [ # standing / walking / at a wall — clearly NOT seated at a desk
    ("STANDING DESK", "one person standing at a height-adjustable standing desk looking at their screen"),
    ("WALKING CORRIDOR", "one or two people walking mid-stride through a bright office corridor"),
    ("WHITEBOARD SESSION", "one or two people at a whiteboard with markers, no readable text on the board"),
    ("PRESENTATION SCREEN", "one person standing beside a wall-mounted TV or screen, presenting with a relaxed open-hand gesture"),
    ("OUTDOOR TERRACE", "one or two people working at a table on a sunny office terrace or rooftop"),
    ("STICKY NOTE WALL", "two people at a wall covered in colorful sticky notes, organizing them"),
    ("INFORMAL HUDDLE", "two people standing and talking near a kitchen counter or hallway"),
    ("COFFEE BREAK CHAT", "two people in a casual standing chat near a coffee machine in a break room"),
    ("OPEN PLAN WIDE", "wide shot of three or four people at separate desks across an open-plan floor"),
    ("RECEPTION AREA", "one person standing at a front reception desk in a modern office lobby"),
    ("KITCHEN COUNTER LEAN", "one person leaning on an office kitchen counter reading from a phone"),
    ("ELEVATOR LOBBY WAIT", "one or two people standing in a bright elevator lobby, one holding a laptop"),
    ("STAIRWELL PASSING", "one person descending an open office staircase, hand on the rail"),
    ("DOORWAY CONVERSATION", "two people pausing to talk in a glass office doorway"),
    ("AT WALL DISPLAY", "one person standing near a large wall display, talking through it with an open hand"),
    ("CARRYING LAPTOP WALK", "one person walking through the office carrying an open laptop on one arm"),
    ("WINDOW LEAN PHONE", "one person leaning against a floor-to-ceiling window taking a call on a phone"),
    ("WHITEBOARD EXPLAIN", "one person mid-gesture explaining to a colleague at a whiteboard"),
    ("STANDING NOTES TABLET", "one person standing in an open area taking notes on a tablet"),
    ("HALLWAY QUICK SYNC", "two people stopped in a hallway for a quick standing sync, one gesturing"),
    ("BREAKROOM STANDING CHAT", "two people standing in a breakroom talking, one holding a notebook"),
    ("WALK-AND-TALK PAIR", "two people walking side by side through the office deep in conversation"),
    ("STANDING AT PRINTER", "one person standing at an office multifunction printer collecting pages"),
    ("LEANING ON DESK PARTITION", "one person standing and leaning on a low desk partition talking to a seated colleague"),
    ("ATRIUM WALKWAY", "one person crossing a bright multi-storey office atrium walkway"),
    ("PLANT-LINED CORRIDOR", "one person walking a corridor lined with tall office plants"),
    ("STANDING TABLET REVIEW", "one person standing near a window reviewing something on a tablet"),
    ("OPEN-HAND PRESENTER", "one person standing at the head of a room mid-gesture with an open hand while presenting"),
    ("PINBOARD PLANNING", "two people standing at a pinboard rearranging index cards"),
    ("COAT-BY-DOOR ARRIVAL", "one person arriving, laptop bag on shoulder, near a coat area by the entrance"),
    ("BALCONY LAPTOP STAND", "one person standing at a rail on an office balcony with a laptop on a ledge"),
    ("OPEN KITCHEN HUDDLE", "three people standing loosely around an office kitchen island talking"),
    ("WIDE LOBBY FIGURE", "very wide shot of one small figure crossing a spacious modern office lobby"),
    ("STANDING BEHIND SEATED", "one person standing behind a seated colleague, both looking at the seated screen"),
    ("SIDE-LIT WALKWAY", "one person walking a side-lit glass walkway, soft reflections around them"),
]

SCENE_TYPES = _DESK_SCENES + _GROUP_SCENES + _ACTIVE_SCENES

# Category sets derived from the lists above (guarantees every label is categorized)
_SCENE_DESK   = {s[0] for s in _DESK_SCENES}
_SCENE_GROUP  = {s[0] for s in _GROUP_SCENES}
_SCENE_ACTIVE = {s[0] for s in _ACTIVE_SCENES}

# Explicit cues added to env description so Gemini knows NOT to write a desk scene
_ACTIVE_CUE = " ← PERSON IS STANDING OR WALKING — do NOT write a seated-at-desk scene"
_GROUP_CUE  = " ← multiple people at a shared table, NOT individual desks"


def _pick_required_scenes(count: int) -> list:
    """Pick scenes with guaranteed visual diversity.
    For count=4: 1 desk + 1 active + 1 group + 1 wildcard (any category).
    For count=3: 1 desk + 1 active + 1 group.
    For count=2: 1 desk + 1 active.
    Guarantees at least 50% of scenes are non-desk environments, and draws from
    the full ~100-scene pool so different blogs rarely repeat the same set."""
    desk   = [s for s in SCENE_TYPES if s[0] in _SCENE_DESK]
    group  = [s for s in SCENE_TYPES if s[0] in _SCENE_GROUP]
    active = [s for s in SCENE_TYPES if s[0] in _SCENE_ACTIVE]

    picks = []
    picks.append(random.choice(desk))    # always: 1 seated-desk scene
    picks.append(random.choice(active))  # always: 1 standing/walking scene

    if count >= 3:
        picks.append(random.choice(group))   # 1 meeting/group scene

    if count >= 4:
        # 4th slot: a wildcard from ANY category (the first three already keep it
        # ≥50% non-desk) — drawn from the full pool so batches vary more.
        used = {s[0] for s in picks}
        pool = [s for s in SCENE_TYPES if s[0] not in used]
        picks.append(random.choice(pool if pool else SCENE_TYPES))

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
    "natural relaxed hand positions, hands resting on the desk, keyboard, or at their sides, "
    # expression — each person has a genuine, understated smile (never forced/toothy)
    "each person has a subtle warm natural smile, relaxed friendly approachable expression, "
    "looking pleasant and content while working, "
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
    # repetitive "pointing at the computer" cliché — the #1 pose to avoid
    "pointing at screen, pointing at the monitor, pointing at computer, pointing at laptop, "
    "finger pointing at screen, index finger extended toward monitor, pointing gesture at display, "
    "hand pointing at the screen, arm outstretched toward monitor, everyone pointing at the screen, "
    "pointing at a display, jabbing finger at monitor, "
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
    # expression — genuine understated smile so people never look flat/unfriendly
    "each person has a subtle warm natural smile, relaxed friendly approachable expression, looking pleasant and content, "
    "natural relaxed varied hand positions, hands resting on the desk, keyboard, or at their sides, "
    "no food on desk, no drinks on desk, no water bottle, no coffee cup, no snacks, clean professional workspace, "
    # monitors/screens must not show readable text — the model tends to paint the blog
    # title onto displays, which then looks cut off. Keep screens active-looking but only
    # lightly (~30%) blurred so no title/words are legible, NOT blank white.
    "monitors and screens are turned on showing soft out-of-focus generic dashboard interfaces with muted colors, "
    "only slightly blurred so no text is legible, the displays clearly look active and in use, "
    "never blank white screens, no readable words or title text on any display"
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
            model = genai.GenerativeModel("gemini-2.5-flash-lite")
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


def _qa_cant_evaluate(reason: str) -> bool:
    """True if the QA model's 'defect' is really 'I couldn't see the image'.
    A dead/blind vision model returns has_defect:true with reasons like 'no image
    provided' — treating that as a real defect triggers pointless 3x regeneration.
    Treat these as a PASS so a broken QA never blocks a good image."""
    r = (reason or "").lower()
    return any(k in r for k in (
        "no image", "not provided", "wasn't provided", "was not provided",
        "cannot evaluate", "can't evaluate", "unable to evaluate", "no image supplied",
        "image supplied", "not supplied", "cannot see", "can't see", "no picture",
    ))


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
            model = genai.GenerativeModel("gemini-2.5-flash-lite")
            img_part = {"mime_type": "image/jpeg", "data": b64}
            resp = model.generate_content([_ANATOMY_CHECK_PROMPT, img_part])
            content = resp.text.strip()
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                data = json.loads(match.group())
                if data.get("has_defect"):
                    reason = data.get("reason", "defect detected")
                    if _qa_cant_evaluate(reason):
                        return True, ""   # QA couldn't see the image → don't regenerate
                    return False, reason
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
                reason = data.get("reason", "defect detected")
                if not _qa_cant_evaluate(reason):
                    return False, reason
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


# Realistic browser fingerprint — many sites (e.g. roxieit.com) return 403 Forbidden
# to bare/minimal User-Agents. A full Chrome header set bypasses most UA-based blocks.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def fetch_blog(url: str):
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
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
    # Strip a trailing site-name suffix ("Post Title | Site Name") from ANY source —
    # h1/og:title often carry it too, not just <title>. Pipe only, so hyphenated
    # titles like "Cloud Migration - A 5-Step Guide" are left intact.
    if title and "|" in title:
        title = re.split(r"\s*\|\s*", title)[0].strip() or title
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
    item = wf.get_item(collection_id, item_id)
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
            model="gemini-2.5-flash-lite",
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


def _gemini_retryable(err_str: str) -> bool:
    """True for transient Gemini errors worth retrying: rate limits (429) AND
    server overload (503 UNAVAILABLE / 'high demand' / 500). Previously only 429
    was retried, so a demand spike (503) dropped the slot planner to the weak
    fallback → blogs with real data silently got 0 infographics."""
    s = err_str.lower()
    return any(k in s for k in (
        "429", "503", "500", "unavailable", "resource_exhausted",
        "overloaded", "high demand", "try again",
    ))


def generate_prompts_live(title: str, content: str, count: int) -> list:
    try:
        from google import genai
        from google.genai import types as gt
        client = genai.Client(api_key=GOOGLE_API_KEY)
        required_envs = _format_required_envs(_pick_required_scenes(count))
        for _attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=f"Blog Title:\n{title}\n\nWhole Blog:\n{content}",
                    config=gt.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT_TEMPLATE.format(count=count, required_envs=required_envs),
                        max_output_tokens=3000,
                        temperature=1.5,
                    )
                )
                break
            except Exception as _e:
                if _gemini_retryable(str(_e)) and _attempt < 2:
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


def _plan_image_slots(title: str, content: str, count: int) -> list:
    """Plan all image slots — PHOTOS ONLY (infographics disabled per user request,
    2026-07-20). Every slot is a scene-varied photo; no infographic is ever produced.
    Uses the live Gemini scene planner for topic-relevant variety, falling back to
    the template-based generator on quota/errors so images are always produced."""

    if FREE_MODE:
        descs = generate_prompts_free(title, content, count)
    else:
        try:
            descs = generate_prompts_live(title, content, count)
        except Exception:
            try:
                descs = generate_prompts_free(title, content, count)
            except Exception:
                descs = []

    fallback = f"IT professional working at a desk on tasks related to {(title or 'IT services')[:40]}."
    while len(descs) < count:
        descs.append(fallback)
    descs = descs[:count]

    slots = [{"slot": i + 1, "type": "photo", "description": d}
             for i, d in enumerate(descs)]

    with st.expander(f"🔍 Slot plan: {count} photo{'s' if count != 1 else ''} (infographics disabled)",
                     expanded=True):
        for s in slots:
            st.markdown(f"**Slot {s['slot']} 📷 PHOTO:** {(s.get('description') or '')[:180]}")
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
    if r.status_code == 402:
        # Pollinations now requires payment — the free fallback is unavailable.
        raise RuntimeError(
            "No image generator available: Kie credits are exhausted AND Pollinations "
            "now requires payment (402). Top up Kie credits at kie.ai to resume generating."
        )
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
        # Kie credits exhausted RIGHT NOW — use the free fallback for THIS image only.
        # Kie is retried first on every image, so generation resumes on Kie automatically
        # the moment credits are topped up — there is no sticky "use Pollinations" state.
        st.warning("⚠️ Kie credits exhausted for this image — trying the free fallback. "
                   "Kie is tried first on every image, so it resumes automatically once credits return.")
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

def _detect_locale_tag(url: str) -> str:
    """Return a 2-letter locale code if the URL path starts with one (e.g.
    https://site.be/fr/blog/... → 'fr'), else None. Whether it's actually a valid
    locale is confirmed later against the site's configured locales."""
    try:
        from urllib.parse import urlparse
        parts = [p for p in urlparse(url).path.split("/") if p]
    except Exception:
        return None
    if parts and re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", parts[0].lower()):
        return parts[0].lower()
    return None


class WebflowClient:
    BASE = "https://api.webflow.com/v2"

    def __init__(self, api_key: str):
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json",
            "content-type": "application/json"
        }
        # Set by set_locale_from_url() when the blog URL points to a secondary
        # Webflow locale (e.g. /fr/). When set, all item reads/writes target that
        # locale so French blogs generate+upload to the French CMS item.
        self.cms_locale_id = None
        self.locale_tag = None

    def _locale_params(self, extra: dict = None) -> dict:
        p = dict(extra or {})
        if self.cms_locale_id:
            p["cmsLocaleId"] = self.cms_locale_id
        return p

    def set_locale_from_url(self, site: dict, blog_url: str):
        """Detect a locale code in the blog URL path (e.g. /fr/blog/...) and, if it
        matches one of the site's secondary locales, target that locale for all
        subsequent item reads/writes. Primary locale needs no cmsLocaleId.
        Returns the matched locale tag, or None."""
        self.cms_locale_id = None
        self.locale_tag = None
        tag = _detect_locale_tag(blog_url)
        if not tag:
            return None
        locales = site.get("locales", {}) or {}
        primary = locales.get("primary", {}) or {}
        if (primary.get("tag") or "").lower() == tag:
            self.locale_tag = tag           # primary locale — no cmsLocaleId needed
            return tag
        for sec in (locales.get("secondary", []) or []):
            if (sec.get("tag") or "").lower() == tag and sec.get("enabled", True):
                self.cms_locale_id = sec.get("cmsLocaleId")
                self.locale_tag = tag
                return tag
        return None

    def get_item(self, collection_id: str, item_id: str):
        """Fetch a single CMS item in the active locale (passes cmsLocaleId when set —
        a secondary-locale item 404s without it)."""
        return self._get(f"/collections/{collection_id}/items/{item_id}",
                         params=self._locale_params())

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
                   "testimon", "career", "industry", "service", "case",
                   "newsletter"]  # "Newsletters" else matches "news" in pass 2 below

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
        # Cache per (collection, locale) — FR and EN listings have different slugs
        cache_key = (collection_id, self.cms_locale_id)
        cached = WebflowClient._ITEMS_CACHE.get(cache_key)
        if cached and (time.time() - cached[0]) < WebflowClient._ITEMS_TTL:
            return cached[1]
        all_items, offset = [], 0
        while True:
            data = self._get(f"/collections/{collection_id}/items",
                             params=self._locale_params({"limit": 100, "offset": offset}))
            items = data.get("items", [])
            all_items.extend(items)
            if len(items) < 100:
                break
            offset += 100
        WebflowClient._ITEMS_CACHE[cache_key] = (time.time(), all_items)
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
        return self._request("PATCH", f"/collections/{collection_id}/items/{item_id}",
                             params=self._locale_params(), json_body={"fieldData": field_data},
                             timeout=30)

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
        # Full-canvas design ("template" covering ~the whole frame): trust the overlay's
        # own size as the canvas. The Main/thumbnail GROUP bbox is the union of ALL
        # children and can be inflated by a stray/overflowing element, which would leave
        # a bare photo strip beyond the design (the overlay can't mask past its own edge).
        ow, oh = obb.get("width", fw), obb.get("height", fh)
        if ow >= fw * 0.9 and oh >= fh * 0.9:
            entry[f"{prefix}_w"] = int(round(ow))
            entry[f"{prefix}_h"] = int(round(oh))
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
        # Figma's actual line height (px). Fall back to fontSize+6 only if absent.
        _lh = st_.get("lineHeightPx")
        if _lh:
            entry[f"{prefix}_lh"]       = int(round(_lh))
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

    # Accept a link from ANY Figma file — pull the file key out of the URL instead of
    # assuming the default template file. Falls back to FIGMA_FILE_KEY if the URL has none.
    fk_match = re.search(r'figma\.com/(?:design|file)/([A-Za-z0-9]+)', figma_url)
    file_key = fk_match.group(1) if fk_match else FIGMA_FILE_KEY

    hdrs = {"X-Figma-Token": token}
    try:
        r = requests.get(
            f"https://api.figma.com/v1/files/{file_key}/nodes?ids={node_id}&depth=3",
            headers=hdrs, timeout=20,
        )
        if r.status_code == 429:
            wait = r.headers.get("Retry-After", "unknown")
            return False, f"Rate limited (Retry-After: {wait}s). Try again later.", ""
        if not r.ok:
            return False, f"Figma API error {r.status_code}: {r.text[:120]}", ""

        # Figma returns {"nodes": {"<id>": null}} (value None, key present) when the node
        # isn't in THIS file — so use `or {}` at each hop, not .get(..., {}) defaults.
        nodes     = r.json().get("nodes") or {}
        node_wrap = nodes.get(node_id) or {}
        frame_doc = node_wrap.get("document") or {}
        if not frame_doc:
            return False, (
                f"Frame `{node_id}` not found in Figma file `{file_key}`.\n\n"
                "Double-check you right-clicked the **client frame** (not an inner "
                "layer) → **Copy link**, and pasted the full URL here."
            ), ""

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
            "file_key": file_key,        # which Figma file this client's nodes live in
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
    """Return the cache key (slug) that best matches a Webflow site display name, or '' if none.

    Compares with ALL non-alphanumerics stripped (not replaced by '-'), so a Webflow
    site name like "Palm Tech" → "palmtech" matches the cache key "palmtech", and
    "Red Team" → "redteam" matches "red-team". Earlier this normalized to "palm-tech"
    (hyphen) and silently failed to match "palmtech" → callers fell back to the 920×613
    default template with no overlay.
    """
    cache = _load_node_cache()
    if not cache:
        return ""
    def _squash(x: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", x.lower())
    s = _squash(site_name)
    if not s:
        return ""
    # 1) exact match on the squashed form (handles "Palm Tech" == "palmtech")
    for key in cache:
        if _squash(key) == s:
            return key
    # 2) containment either way (squashed), longest key first to avoid spurious short matches
    for key in sorted(cache, key=lambda k: len(_squash(k)), reverse=True):
        ks = _squash(key)
        if ks and (ks in s or s in ks):
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

    node_map = {}
    if missing_main  and overlay_main_id:  node_map[overlay_main_id]  = main_path
    if missing_thumb and overlay_thumb_id: node_map[overlay_thumb_id] = thumb_path

    # Only call Figma if we actually have node IDs to export. Firing with an empty
    # `ids=` returns a 400 — this happens when a client was registered without one of
    # the overlay nodes detected (e.g. the thumbnail overlay). Skip + warn instead.
    if node_map:
        try:
            client_file_key = entry.get("file_key") or FIGMA_FILE_KEY
            img_r = requests.get(
                f"https://api.figma.com/v1/images/{client_file_key}",
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

    if missing_main and not overlay_main_id:
        st.warning(f"⚠️ '{client_name}' has no MAIN overlay node — re-register its Figma template.")
    if missing_thumb and not overlay_thumb_id:
        st.info(f"ℹ️ '{client_name}' has no THUMBNAIL overlay node — thumbnail will render without "
                f"the design overlay. Re-register its Figma template to add one.")

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
        "lh":             entry.get("main_lh"),
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
        "lh":             entry.get("thumb_lh"),
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
        ox, oy = tpl.get("ox", 0), tpl.get("oy", 0)
        logo_path = tpl["logo"]
        logo = logo_alpha = None
        canvas = None
        if logo_path.exists() and logo_path.stat().st_size > 1000:
            logo = PILImage.open(logo_path).convert("RGBA")
            logo_alpha = logo.split()[3]
            lw, lh = logo.size
            # Full-canvas overlay = a design that frames the photo inside an inset window
            # (e.g. a rounded card). Fit the photo to that TRANSPARENT WINDOW at its
            # natural scale instead of cover-cropping to the whole canvas — the window is
            # much smaller than the canvas, so canvas-cropping would zoom the subject.
            # Panel-style overlays (a small side panel) skip this and keep full-bleed.
            if lw >= tpl["w"] * 0.9 and lh >= tpl["h"] * 0.9:
                window = logo_alpha.point(lambda v: 255 if v < 128 else 0).getbbox()
                if window:
                    wl, wt, wr, wb = window
                    photo = _cover_crop(bg_img, wr - wl, wb - wt)
                    canvas = PILImage.new("RGB", (tpl["w"], tpl["h"]), (17, 17, 17))
                    canvas.paste(photo, (wl + ox, wt + oy))
        if canvas is None:
            canvas = _cover_crop(bg_img, tpl["w"], tpl["h"])
        if logo is not None:
            canvas.paste(logo.convert("RGB"), (ox, oy), mask=logo_alpha)
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
        # Normalize typographic chars only. Accented Latin letters (é, è, ç, à, ô…)
        # are KEPT so French/Dutch/etc. titles render faithfully — the template fonts
        # cover Latin-1. Map a few >255 chars that fonts often lack to ASCII equivalents.
        _CHAR_MAP = {"®": "(R)", "™": "(TM)", "©": "(C)", "’": "'", "‘": "'",
                     "“": '"', "”": '"', "–": "-", "—": "-", "…": "...",
                     "œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE",
                     "“": '"', "„": '"', "«": '"', "»": '"'}
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
        lh = tpl.get("lh") or (tpl["fsz"] + 6)
        fc = tpl.get("font_color", (255, 255, 255))
        for i, line in enumerate(lines):
            draw.text((tpl["tx"], tpl["ty"] + i * lh), line, fill=fc, font=font)
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


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
            if wf_fallback and str(e.response.status_code) in ("404", "403"):
                _code = str(e.response.status_code)
                st.info(f"🔒 Public page returned {_code} (draft or bot-blocked) — "
                        f"fetching content from Webflow CMS instead...")
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

    # Step 3a: Plan image slots (photos only)
    with st.status(f"Planning {count} image slots...", expanded=True) as _st:
        try:
            slots = _plan_image_slots(title, content, count)
            _st.update(label=f"Slots planned ✓ — {count} photo{'s' if count != 1 else ''}",
                       state="complete")
        except Exception as e:
            _st.update(label="Slot planning failed", state="error")
            st.error(str(e))
            raise

    # Step 3b: Alt texts
    with st.status("Generating alt texts...", expanded=True) as _st:
        alt_texts = []
        for i, sl in enumerate(slots, 1):
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


    for i, (sl, alt) in enumerate(zip(slots, alt_texts), 1):
        gen_prog.progress(i / len(slots), text=f"Generating image {i} of {len(slots)}...")


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

# ── Build/deploy diagnostic marker ────────────────────────────────────────────
# Confirms which version is actually live + which templates the deployed cache
# contains. If this caption is missing or shows palmtech=NO, the running
# Streamlit instance is stale — Reboot the app from share.streamlit.io.
_dbg_cache = _load_node_cache()
st.caption(
    f"🛠️ build 2026-06-16b · {len(_dbg_cache)} templates loaded · "
    f"palmtech={'✅' if 'palmtech' in _dbg_cache else '❌ MISSING'} · "
    f"aboutit={'✅' if 'aboutit' in _dbg_cache else '❌ MISSING'}"
)

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

# ── UX design-system v5 (whole-app): stepper, section heads, KPI tiles, cards ──
st.markdown("""
<style>
:root { --cy:#35EDED; --cy2:#20d0d0; --uxline:#2a2a2a; --uxpanel:#1a1a1a;
        --uxpanel2:#202020; --uxdim:#8a8a8a; --uxfaint:#666;
        --uxgreen:#3df03d; --uxamber:#f5c451; --uxred:#ff6b6b; }
/* section header */
.ux-shead { display:flex; align-items:center; gap:10px; margin:6px 0 12px; }
.ux-shead .k { background:var(--cy); color:#000; border-radius:7px; width:24px; height:24px;
    display:inline-flex; align-items:center; justify-content:center; font-weight:800; font-size:12px; }
.ux-shead h2 { font-size:15px; margin:0; font-weight:700; color:#f2f2f2; }
.ux-shead .hint { color:var(--uxfaint); font-size:12px; margin-left:auto; }
/* stepper */
.ux-stepper { display:flex; gap:8px; margin:2px 0 18px; flex-wrap:wrap; }
.ux-step { flex:1; min-width:96px; background:var(--uxpanel); border:1px solid var(--uxline);
    border-radius:10px; padding:9px 12px; }
.ux-step .n { display:inline-flex; width:20px; height:20px; border-radius:50%; background:var(--uxpanel2);
    color:var(--uxdim); align-items:center; justify-content:center; font-size:11px; font-weight:700; margin-right:7px; }
.ux-step .lbl { font-size:12px; font-weight:600; color:var(--uxdim); }
.ux-step.active { border-color:var(--cy); background:linear-gradient(180deg,rgba(53,237,237,.10),transparent); }
.ux-step.active .n { background:var(--cy); color:#000; }
.ux-step.active .lbl { color:#f2f2f2; }
.ux-step.done .n { background:var(--uxgreen); color:#000; }
.ux-step.done .lbl { color:#f2f2f2; }
/* KPI tiles */
.ux-kpis { display:flex; gap:10px; margin:2px 0 14px; flex-wrap:wrap; }
.ux-kpi { flex:1; min-width:120px; background:var(--uxpanel); border:1px solid var(--uxline);
    border-radius:10px; padding:12px 15px; }
.ux-kpi .v { font-size:24px; font-weight:800; line-height:1; color:#f2f2f2; }
.ux-kpi .l { font-size:11px; color:var(--uxdim); margin-top:6px; }
.ux-kpi.cy .v { color:var(--cy); } .ux-kpi.green .v { color:var(--uxgreen); }
.ux-kpi.amber .v { color:var(--uxamber); } .ux-kpi.red .v { color:var(--uxred); }
/* source strip / callout */
.ux-strip { display:flex; align-items:center; gap:14px; background:var(--uxpanel); border:1px solid var(--uxline);
    border-radius:10px; padding:11px 15px; flex-wrap:wrap; margin-bottom:8px; }
.ux-strip .ok { color:var(--uxgreen); font-weight:700; font-size:13px; }
.ux-strip .meta { color:var(--uxdim); font-size:12px; }
.ux-callout { background:rgba(53,237,237,.06); border:1px solid var(--uxline); border-left:3px solid var(--cy);
    border-radius:8px; padding:10px 14px; font-size:12px; color:var(--uxdim); margin:12px 0; }
.ux-callout.amber { border-left-color:var(--uxamber); }
/* client-card look for bordered containers holding a checkbox */
[data-testid="stVerticalBlockBorderWrapper"]:has(.ux-cardmark) {
    background:var(--uxpanel); border-radius:10px; transition:border-color .15s; }
[data-testid="stVerticalBlockBorderWrapper"]:has(.ux-cardmark):hover { border-color:var(--cy); }
.ux-cname { font-weight:700; font-size:13px; color:#f2f2f2; }
.ux-cmeta { font-size:11px; color:var(--uxdim); margin-top:2px; }
.ux-bdg { display:inline-block; font-size:10px; font-weight:700; border-radius:999px; padding:2px 8px; margin-top:6px; }
.ux-bdg.ok { background:rgba(61,240,61,.14); color:var(--uxgreen); }
.ux-bdg.no { background:rgba(255,107,107,.14); color:var(--uxred); }
</style>
""", unsafe_allow_html=True)


def _ux_section(num, title, hint=""):
    """Consistent numbered section header (whole-app design system)."""
    _h = f"<span class='hint'>{hint}</span>" if hint else ""
    st.markdown(f"<div class='ux-shead'><span class='k'>{num}</span>"
                f"<h2>{title}</h2>{_h}</div>", unsafe_allow_html=True)


def _ux_stepper(steps, active_idx, done_before=True):
    """Horizontal stepper. steps=list of labels; active_idx=0-based active step."""
    _html = "<div class='ux-stepper'>"
    for _i, _lbl in enumerate(steps):
        if _i < active_idx and done_before:
            _cls, _n = "done", "✓"
        elif _i == active_idx:
            _cls, _n = "active", str(_i + 1)
        else:
            _cls, _n = "", str(_i + 1)
        _html += (f"<div class='ux-step {_cls}'><span class='n'>{_n}</span>"
                  f"<span class='lbl'>{_lbl}</span></div>")
    _html += "</div>"
    st.markdown(_html, unsafe_allow_html=True)


def _ux_kpis(tiles):
    """KPI row. tiles=list of (value, label, color) where color in ''/cy/green/amber/red."""
    _html = "<div class='ux-kpis'>"
    for _v, _l, _c in tiles:
        _html += f"<div class='ux-kpi {_c}'><div class='v'>{_v}</div><div class='l'>{_l}</div></div>"
    _html += "</div>"
    st.markdown(_html, unsafe_allow_html=True)


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
    "<span class='badge'>Gemini 2.5</span>"
) if KIE_API_KEY else "<span class='badge' style='color:#ff5b5b;border-color:rgba(255,91,91,.4)'>● KIE key missing</span>"
st.markdown(
    f"<div class='msp-head'><h1>Blog Image Generator</h1><div class='badges'>{_live_badges}</div></div>"
    "<div class='msp-sub'>Turn any Webflow blog post into on-brand, MSP-themed visuals — automatically.</div>",
    unsafe_allow_html=True,
)
if not KIE_API_KEY:
    st.error("🔴 **KIE_API_KEY missing** — add it to .env to enable image generation.")

tab_manual, tab_revise, tab_batch = st.tabs(
    ["📥  Manual Upload", "🔗  Generate from Link", "⚡  Batch Generate (Airtable)"])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — Manual Upload (generate content images only — no compositing)
# ════════════════════════════════════════════════════════════════════════════════
with tab_manual:
    _ux_section("✨", "Generate Images", "one blog · content images only")
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

        # Step 2: Plan image slots (photos only)
        with st.status(f"Planning {count} image slots...", expanded=True) as _ms:
            try:
                slots = _plan_image_slots(title, content, count)
                _ms.update(label=f"Slots planned ✓ — {count} photo{'s' if count != 1 else ''}",
                           state="complete")
            except Exception as e:
                _ms.update(label="Slot planning failed", state="error")
                st.error(str(e))
                st.stop()

        # Step 3: Alt texts
        with st.status("Generating alt texts...", expanded=True) as _ms:
            alt_texts = []
            for i, sl in enumerate(slots, 1):
                alt = generate_alt_text_for(sl.get("description", ""), title, index=i)
                time.sleep(1)
                alt_texts.append(alt)
            _ms.update(label="Alt texts ready ✓", state="complete")

        # Step 4: Generate images
        _ux_section("🖼️", "Generated Images", "download the ones you want")
        gen_prog = st.progress(0, text="Starting image generation...")
        results  = []
        img_seeds = [random.randint(10000, 999999) for _ in slots]


        for i, (sl, alt) in enumerate(zip(slots, alt_texts), 1):
            gen_prog.progress(i / len(slots), text=f"Generating image {i} of {len(slots)}...")


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
# TAB — Generate from Link
# Paste a published blog URL → generate the FULL branded deliverable (inner content
# images + branded Main + Thumbnail), for a new post or a revision. The client is
# auto-detected from the URL's domain; override it with the dropdown if needed.
# Optional 1-click upload to Webflow lives in the second tab_revise block below.
# ════════════════════════════════════════════════════════════════════════════════
def _revise_detect_client(url: str) -> str:
    """Best-effort client slug from a blog URL — matches the domain against the
    template cache (e.g. version2llc.com → 'version2'). Returns '' if no match."""
    from urllib.parse import urlparse
    host = urlparse(url if url.startswith("http") else "https://" + url).netloc.lower()
    host = host.split(":")[0].removeprefix("www.")
    for cand in (host, re.sub(r"\.[a-z]{2,}$", "", host), host.split(".")[0]):
        m = _match_client(cand)
        if m:
            return m
    return ""


def _revise_build_branded(title: str, ok_results: list, client_slug: str) -> tuple:
    """Generate a cover photo and composite branded Main + Thumbnail for a client.
    Returns (main_bytes, thumb_bytes) — either may be None if compositing fails."""
    prompts = [r["prompt"] for r in ok_results if r.get("prompt")] or [title]
    cover = _generate_cover_bg(title, prompts)
    ml, tl = ensure_figma_assets_for_client(client_slug)
    mtpl, ttpl = make_tpls(client_slug, ml, tl)
    return (composite_template(cover, title, mtpl),
            composite_template(cover, title, ttpl))


with tab_revise:
    _ux_section("🔗", "Generate from Link", "one blog · branded Main + Thumbnail + inner images")
    st.caption(
        "Paste a **published blog link** and the app detects the client, then generates the "
        "branded **Main**, **Thumbnail**, and inner images — for a brand-new post or a "
        "revision. Download them, or **upload straight to Webflow** at the bottom "
        "(no need to open Webflow).")

    rv_url = st.text_input(
        "Blog URL", placeholder="https://www.version2llc.com/blog/your-post", key="rv_url")

    # Client override — defaults to auto-detect from the URL's domain.
    _rv_cache = _load_node_cache()
    _rv_slugs = sorted(_rv_cache.keys())
    _rv_labels = ["🔍 Auto-detect from URL"] + [_client_display_name(s) for s in _rv_slugs]
    _rv_choice = st.selectbox(
        "Client (branding template)", _rv_labels, index=0, key="rv_client_choice",
        help="Auto-detect uses the link's domain. Pick a client here to override.")
    _rv_override = "" if _rv_choice == _rv_labels[0] else _rv_slugs[_rv_labels.index(_rv_choice) - 1]

    rv_btn = st.button("Generate Images", type="primary",
                       use_container_width=True, key="rv_btn")

    # ── Handle per-image redo (inner images) ──────────────────────────────────
    if st.session_state.get("rv_redo_idx") is not None and "rv_results" in st.session_state:
        redo_i    = st.session_state.pop("rv_redo_idx")
        redo_seed = st.session_state.pop("rv_redo_seed", random.randint(10000, 999999))
        with st.spinner(f"Regenerating image {redo_i}..."):
            try:
                cur = st.session_state["rv_results"][redo_i - 1]
                new_desc = generate_prompt_variation(
                    cur.get("prompt", ""), st.session_state.get("rv_title", ""))
                raw = _dispatch_image_gen(new_desc, redo_i, DEFAULT_WIDTH, DEFAULT_HEIGHT,
                                          seed=redo_seed)
                opt_bytes, ext = optimize_image(raw, max_kb=200)
                st.session_state["rv_results"][redo_i - 1] = {
                    "index": redo_i, "bytes": opt_bytes, "ext": ext,
                    "size_kb": round(len(opt_bytes) / 1024, 1),
                    "alt": cur.get("alt", ""), "prompt": new_desc,
                    "status": "ok", "defect_reason": "",
                }
            except Exception as e:
                st.error(f"Redo failed: {e}")

    # ── Handle cover regeneration (re-composite Main + Thumbnail) ──────────────
    if st.session_state.pop("rv_redo_cover", False) and "rv_results" in st.session_state:
        _rv_cl = st.session_state.get("rv_client", "")
        if _rv_cl:
            with st.spinner("Regenerating cover + Main/Thumbnail..."):
                try:
                    okr = [r for r in st.session_state["rv_results"] if r["status"] == "ok"]
                    mb, tb = _revise_build_branded(
                        st.session_state.get("rv_title", ""), okr, _rv_cl)
                    st.session_state["rv_main_bytes"]  = mb
                    st.session_state["rv_thumb_bytes"] = tb
                except Exception as e:
                    st.error(f"Cover regeneration failed: {e}")

    # ── Full generation on button click ───────────────────────────────────────
    if rv_btn:
        if not rv_url.strip():
            st.error("Please enter a blog URL.")
            st.stop()
        for k in ["rv_results", "rv_slots", "rv_alt_texts", "rv_title",
                  "rv_client", "rv_main_bytes", "rv_thumb_bytes"]:
            st.session_state.pop(k, None)

        url = ("https://" + rv_url) if not rv_url.startswith("http") else rv_url

        # Resolve client: manual override wins, else auto-detect from domain.
        client_slug = _rv_override or _revise_detect_client(url)
        if client_slug:
            st.info(f"🏢 Client: **{_client_display_name(client_slug)}** "
                    f"{'(you selected)' if _rv_override else '(auto-detected from link)'}")
        else:
            st.warning(
                "⚠️ Couldn't detect the client from this link — I'll still generate the "
                "inner images, but **Main + Thumbnail need a client**. Pick one from the "
                "dropdown above and regenerate to get the branded pair.")

        # Step 1: Fetch blog (public scrape)
        with st.status("Fetching blog page...", expanded=True) as s:
            try:
                title, content, image_urls = fetch_blog(url)
                count = len(image_urls) or 4
                st.write(f"**Title:** {title}")
                st.write(f"**Images detected on page:** {len(image_urls)} → generating **{count}**")
                s.update(label="Blog fetched ✓", state="complete")
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    s.update(label="Page not found (404)", state="error")
                    st.error(
                        "**Page not found (404).** The post may be a **draft / unpublished** "
                        "in Webflow. Publish it first, or use the **Batch Generate tab** (it can "
                        "read drafts with the client's Webflow key).")
                else:
                    s.update(label="Failed to fetch blog", state="error")
                    st.error(f"HTTP error: {e}")
                st.stop()
            except Exception as e:
                s.update(label="Failed to fetch blog", state="error")
                st.error(f"Could not load the page: {e}")
                st.stop()

        # Step 2: Plan slots + alt texts
        with st.status(f"Planning {count} image slots...", expanded=True) as _s:
            try:
                slots = _plan_image_slots(title, content, count)
                _s.update(label=f"Slots planned ✓ — {count} photo{'s' if count != 1 else ''}",
                          state="complete")
            except Exception as e:
                _s.update(label="Slot planning failed", state="error")
                st.error(str(e))
                st.stop()

        with st.status("Generating alt texts...", expanded=True) as _s:
            alt_texts = []
            for i, sl in enumerate(slots, 1):
                alt_texts.append(generate_alt_text_for(sl.get("description", ""), title, index=i))
                time.sleep(1)
            _s.update(label="Alt texts ready ✓", state="complete")

        # Step 3: Generate inner images
        _ux_section("🖼️", "Inner Images", "content images for the blog body")
        gen_prog = st.progress(0, text="Starting image generation...")
        results  = []
        img_seeds = [random.randint(10000, 999999) for _ in slots]
        for i, (sl, alt) in enumerate(zip(slots, alt_texts), 1):
            gen_prog.progress(i / len(slots), text=f"Generating image {i} of {len(slots)}...")
            prompt = sl.get("description", "")
            last_err, final_bytes, final_ext = None, None, "jpg"
            base_seed = img_seeds[i - 1]
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    raw = _dispatch_image_gen(prompt, i, DEFAULT_WIDTH, DEFAULT_HEIGHT,
                                              seed=base_seed + attempt * 1000)
                    is_ok, reason = check_anatomy(raw)
                    if not is_ok and attempt < MAX_ATTEMPTS:
                        st.warning(f"⚠️ Image {i} — defect ({reason}), regenerating...")
                        continue
                    final_bytes, final_ext = optimize_image(raw, max_kb=200)
                    if not is_ok:
                        st.warning(f"⚠️ Image {i} — kept after {MAX_ATTEMPTS} attempts ({reason})")
                    break
                except Exception as e:
                    last_err = e
                    if attempt < MAX_ATTEMPTS:
                        st.warning(f"⚠️ Image {i} failed ({e}), retrying...")
                        time.sleep(2)
            results.append({
                "index": i, "bytes": final_bytes, "ext": final_ext,
                "size_kb": round(len(final_bytes) / 1024, 1) if final_bytes else 0,
                "alt": alt, "prompt": prompt,
                "status": "ok" if final_bytes else f"failed: {last_err}",
                "defect_reason": "",
            })
        gen_prog.empty()

        # Step 4: Branded Main + Thumbnail (only if a client is known)
        main_bytes, thumb_bytes = None, None
        okr = [r for r in results if r["status"] == "ok"]
        if client_slug and okr:
            with st.status("Generating cover + branded Main/Thumbnail...", expanded=True) as _s:
                try:
                    main_bytes, thumb_bytes = _revise_build_branded(title, okr, client_slug)
                    _s.update(label="Main + Thumbnail ✓", state="complete")
                except Exception as e:
                    _s.update(label=f"Compositing failed: {e}", state="error")

        st.session_state["rv_results"]     = results
        st.session_state["rv_slots"]       = slots
        st.session_state["rv_alt_texts"]   = alt_texts
        st.session_state["rv_title"]       = title
        st.session_state["rv_client"]      = client_slug
        st.session_state["rv_main_bytes"]  = main_bytes
        st.session_state["rv_thumb_bytes"] = thumb_bytes
        st.session_state["rv_url_final"]   = url
        st.session_state["rv_slug"]        = url.rstrip("/").split("/")[-1]
        st.session_state["rv_image_urls"]  = image_urls
        st.session_state.pop("rv_uploaded", None)

    # ── Display results ────────────────────────────────────────────────────────
    if "rv_results" in st.session_state:
        _rv_title = st.session_state.get("rv_title", "")
        _rv_cl    = st.session_state.get("rv_client", "")

        # Branded Main + Thumbnail
        _mb = st.session_state.get("rv_main_bytes")
        _tb = st.session_state.get("rv_thumb_bytes")
        if _mb or _tb:
            _ux_section("🎨", "Branded Cover", f"{_client_display_name(_rv_cl)} · Main + Thumbnail")
            bc1, bc2 = st.columns(2)
            for _col, _label, _bytes, _fname in [
                (bc1, "Main", _mb, "main.png"), (bc2, "Thumbnail", _tb, "thumbnail.png")]:
                with _col:
                    if _bytes:
                        st.image(_bytes, caption=_label, use_container_width=True)
                        st.download_button(f"⬇ Download {_label}", data=_bytes,
                                           file_name=_fname, mime="image/png",
                                           key=f"rv_dl_{_label}", use_container_width=True)
                    else:
                        st.info(f"{_label} not generated.")
            if st.button("🔄 Regenerate cover (new Main + Thumbnail)", key="rv_redo_cover_btn",
                         use_container_width=True):
                st.session_state["rv_redo_cover"] = True
                st.rerun()
        elif _rv_cl:
            st.info("Branded Main/Thumbnail weren't generated — try **Regenerate Images** again.")

        # Inner images
        _ux_section("🖼️", "Inner Images", "download the ones you want")
        results = st.session_state["rv_results"]
        for rowi in range(0, len(results), 4):
            cols = st.columns(4)
            for ci, result in enumerate(results[rowi:rowi + 4]):
                with cols[ci]:
                    i = result["index"]
                    if result["bytes"]:
                        fname = f"image_{i:02d}.{result['ext']}"
                        st.image(result["bytes"],
                                 caption=f"{fname} — {result['size_kb']} KB",
                                 use_container_width=True)
                        st.text_area(f"Alt text #{i}", value=result["alt"],
                                     height=70, key=f"rv_alt_{i}")
                        dl_c, redo_c = st.columns([3, 1])
                        with dl_c:
                            st.download_button(
                                label=f"⬇ Download {fname}", data=result["bytes"],
                                file_name=fname,
                                mime=MIME_MAP.get(result["ext"], "image/jpeg"),
                                key=f"rv_dl_{i}", use_container_width=True)
                        with redo_c:
                            if st.button("🔄", key=f"rv_redo_{i}", use_container_width=True,
                                         help="Regenerate this image"):
                                st.session_state["rv_redo_idx"]  = i
                                st.session_state["rv_redo_seed"] = random.randint(10000, 999999)
                                st.rerun()
                    else:
                        st.error(f"Image {i} failed: {result['status']}")
                        if st.button("🔄 Retry", key=f"rv_retry_{i}", use_container_width=True):
                            st.session_state["rv_redo_idx"]  = i
                            st.session_state["rv_redo_seed"] = random.randint(10000, 999999)
                            st.rerun()

        ok = sum(1 for r in results if r["status"] == "ok")
        st.success(f"Done! {ok}/{len(results)} inner images ready"
                   + (" · Main + Thumbnail above." if (_mb or _tb) else "."))


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — Auto Upload to Webflow
# ════════════════════════════════════════════════════════════════════════════════
def do_webflow_connect(api_key: str, manual_site_id: str, client_name: str, slug: str,
                       blog_url: str = "", known_collection_id: str = ""):
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

        # Multi-language sites: if the URL is /fr/... (etc.), target that locale so
        # the post is found, fetched, and uploaded in the right language.
        _ltag = wf.set_locale_from_url(site, blog_url)
        if _ltag:
            _is_secondary = bool(wf.cms_locale_id)
            st.write(f"**Locale:** {_ltag.upper()} "
                     f"({'secondary — CMS targets this language' if _is_secondary else 'primary'})")

    # Prefer the explicit collectionId from Airtable/Clients info. The auto-detector
    # can mis-fire — e.g. it grabbed Zhero's "Newsletters" collection via the "news"
    # keyword, so blog slugs were searched in the wrong collection and never found.
    collection = None
    if known_collection_id:
        try:
            cols = wf.get_collections(site_id)
            collection = next((c for c in cols if c.get("id") == known_collection_id), None)
        except Exception:
            collection = None
        if not collection:
            collection = {"id": known_collection_id, "displayName": known_collection_id}
    if not collection:
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
    if getattr(wf, "cms_locale_id", None):
        st.write(f"🌍 **Upload target locale: {wf.locale_tag.upper()}** "
                 f"(secondary — CMS item `{item_id}`)")
    else:
        st.write(f"🌍 **Upload target locale: primary/default** (CMS item `{item_id}`)")
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
            full_item = wf.get_item(collection_id, item_id)
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
                fd2 = wf.get_item(collection_id, item_id).get("fieldData", {})

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


def do_webflow_upload_cover(wf, site_id, collection_id, item_id, was_published,
                            main_bytes=None, thumb_bytes=None, blog_title="",
                            client_name="", site_name=""):
    """Upload ONLY the main (featured) image + thumbnail to the CMS item, then publish.
    Used by the 'Main + Thumbnail Only' tab — no body images / infographics / rich text."""
    if getattr(wf, "cms_locale_id", None):
        st.write(f"🌍 **Upload target locale: {wf.locale_tag.upper()}** "
                 f"(secondary — CMS item `{item_id}`)")
    else:
        st.write(f"🌍 **Upload target locale: primary/default** (CMS item `{item_id}`)")
    if main_bytes or thumb_bytes:
        with st.status("Uploading main image & thumbnail...", expanded=True) as s:
            try:
                fd2 = wf.get_item(collection_id, item_id).get("fieldData", {})
                st.write(f"**All CMS fields:** {list(fd2.keys())}")
                img_fields = {k: v for k, v in fd2.items()
                              if v is None or (isinstance(v, dict) and "url" in v)}
                st.write(f"**Image-type fields found:** {list(img_fields.keys()) or 'none'}")

                def _find_field(fields, keywords):
                    hit = next((k for k in fields if k.lower() in keywords), None)
                    if hit:
                        return hit
                    return next((k for k in fields if any(kw in k.lower() for kw in keywords)), None)

                def _to_jpeg(png_bytes):
                    buf = io.BytesIO()
                    PILImage.open(io.BytesIO(png_bytes)).convert("RGB").save(buf, format="JPEG", quality=90)
                    return buf.getvalue(), "jpg"

                THUMB_KW = ["thumbnail-image", "thumbnail_image", "thumbnail", "thumb",
                            "card-image", "card-thumbnail", "card", "preview", "list-image"]
                MAIN_KW  = ["main-image", "main_image", "hero-image", "featured-image",
                            "post-image", "blog-image", "cover-image", "og-image",
                            "main", "hero", "featured", "cover", "post-main"]
                img_update = {}
                tkey = _find_field(img_fields, THUMB_KW) if thumb_bytes else None
                main_candidates = {k: v for k, v in img_fields.items() if k != tkey}
                mkey = _find_field(main_candidates, MAIN_KW) if main_bytes else None
                if main_bytes and not mkey and main_candidates:
                    mkey = next(iter(main_candidates))
                    st.write(f"ℹ️ Main image: no keyword match — using `{mkey}` as fallback")
                st.write(f"**Matched → main:** `{mkey or 'NOT FOUND'}` | **thumb:** `{tkey or 'NOT FOUND'}`")
                if main_bytes and mkey:
                    jbytes, ext = _to_jpeg(main_bytes)
                    murl = wf.upload_asset(site_id, jbytes, f"main_image.{ext}")
                    main_alt = _gemini_text(
                        "Write alt text for a blog featured image. Max 80 characters. No quotes.",
                        f"Blog title: {blog_title}", max_tokens=30, temperature=0.5,
                    ) or f"{blog_title[:70]} — featured image"
                    if len(main_alt) > 80:
                        main_alt = main_alt[:80].rsplit(' ', 1)[0]
                    img_update[mkey] = {"url": murl, "alt": main_alt}
                    st.write(f"✓ Main image → `{mkey}`")
                elif main_bytes:
                    st.warning(f"⚠️ No image field found for main image. Fields: {list(img_fields.keys())}")
                if thumb_bytes and tkey:
                    jbytes, ext = _to_jpeg(thumb_bytes)
                    turl = wf.upload_asset(site_id, jbytes, f"thumbnail.{ext}")
                    thumb_alt = _gemini_text(
                        "Write alt text for a blog thumbnail image. Max 80 characters. No quotes.",
                        f"Blog title: {blog_title}", max_tokens=30, temperature=0.5,
                    ) or f"{blog_title[:70]} — thumbnail"
                    if len(thumb_alt) > 80:
                        thumb_alt = thumb_alt[:80].rsplit(' ', 1)[0]
                    img_update[tkey] = {"url": turl, "alt": thumb_alt}
                    st.write(f"✓ Thumbnail → `{tkey}`")
                elif thumb_bytes:
                    st.warning(f"⚠️ No image field found for thumbnail. Fields: {list(img_fields.keys())}")
                if img_update:
                    wf.update_item(collection_id, item_id, img_update)
                    st.write(f"CMS updated: {list(img_update.keys())}")
                s.update(label="Main image & thumbnail uploaded ✓", state="complete")
            except Exception as e:
                s.update(label="Main/thumbnail upload failed", state="error")
                st.error(str(e))
                return

    with st.status("Checking publish status...", expanded=True) as s:
        try:
            if was_published:
                wf.publish_items(collection_id, [item_id])
                s.update(label="Re-published ✓", state="complete")
            else:
                s.update(label="Kept as draft ✓", state="complete")
        except Exception as e:
            s.update(label="Publish step failed", state="error")
            st.error(str(e))
    st.success(f"✅ Done! Main + thumbnail uploaded "
               f"({client_name or site_name} — {'published' if was_published else 'draft'}).")


def _resolve_blog_slug(start_url: str):
    """Module-level slug resolver (GET redirect chain + <link rel=canonical>), used by the
    Main+Thumbnail tab. Mirrors the auto tab's resolver; ignores Webflow /404 canonicals."""
    def _slug_of(u):
        u = re.sub(r"[?#].*$", "", u or "").rstrip("/")
        return u.split("/")[-1]
    _orig = re.sub(r"[?#].*$", "", start_url).rstrip("/")
    try:
        _r = requests.get(start_url, allow_redirects=True,
                          headers=BROWSER_HEADERS, timeout=15)
        if not _r.ok:
            return _orig, _slug_of(_orig)
        _soup = BeautifulSoup(_r.content, "html.parser")
        _canon = _soup.find("link", rel="canonical")
        if _canon and _canon.get("href"):
            _cu = re.sub(r"[?#].*$", "", _canon["href"]).rstrip("/")
            _cslug = _slug_of(_cu)
            if _cslug and _cslug.lower() != "404":
                return _cu, _cslug
        _fu = re.sub(r"[?#].*$", "", _r.url).rstrip("/")
        _fslug = _slug_of(_fu)
        if _fslug and _fslug.lower() != "404":
            return _fu, _fslug
        return _orig, _slug_of(_orig)
    except Exception:
        return _orig, _slug_of(_orig)


def _fetch_cover_title(url, slug, wf=None, collection_id=None, item_id=None):
    """Best-effort blog title for the cover tab: public page → Webflow CMS draft → slug."""
    try:
        title, _c, _i = fetch_blog(url)
        if title:
            return title
    except requests.HTTPError as he:
        if wf and str(getattr(he.response, "status_code", "")) in ("404", "403"):
            try:
                title, _c, _i = fetch_blog_from_cms(slug, wf, collection_id, item_id)
                if title:
                    return title
            except Exception:
                pass
    except Exception:
        pass
    return slug.replace("-", " ").title()


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


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — Batch Generate (Airtable → Webflow)
# Reads Blog Keyword + Clients info straight from Airtable (CSV in 1/ is a fallback),
# lets the user pick clients, generates per client, reviews, uploads to Webflow, then
# marks ONLY that row's "Image status" = Done in Airtable after a successful upload.
# ════════════════════════════════════════════════════════════════════════════════
BATCH_DATA_DIR = Path("1")


def _batch_norm(s: str) -> str:
    """Normalise a client name for matching: lowercase, alphanumeric only.
    'Capstone Works, Inc.' -> 'capstoneworksinc'."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _batch_read_csv_rows(text: str) -> list:
    import csv, io
    try:
        return list(csv.DictReader(io.StringIO(text)))
    except Exception:
        return []


def _batch_classify(fieldnames) -> str:
    """Decide whether a CSV is the Blog Keyword sheet or the Clients info sheet."""
    fs = {(f or "").strip().lower() for f in (fieldnames or [])}
    if "webflow_token" in fs or "siteid" in fs:
        return "clients"
    if "image status" in fs or "final blog url" in fs or "primary keyword" in fs:
        return "blog"
    return "unknown"


def _batch_autoload():
    """Auto-detect the two CSVs sitting in the 1/ folder. Returns (blog_rows, clients_rows)."""
    blog, clients = [], []
    if not BATCH_DATA_DIR.exists():
        return blog, clients
    for p in sorted(BATCH_DATA_DIR.glob("*.csv")):
        try:
            rows = _batch_read_csv_rows(p.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not rows:
            continue
        kind = _batch_classify(rows[0].keys())
        if kind == "clients" and not clients:
            clients = rows
        elif kind == "blog" and not blog:
            blog = rows
    return blog, clients


# ── Airtable direct read/write (replaces the CSV drop when AIRTABLE_TOKEN is set) ──
_AT_BASE = "app0baCBwviPDXArm"
_AT_TBL_BLOG = "tblJ1ZtHYnb9B5BPq"       # Blog Keyword
_AT_TBL_CLIENTS = "tblaoZfBy5Ts9R59D"    # Clients info
_AT_BLOG_FIELDS = ["Client name", "Final blog url", "Image status", "Primary keyword",
                   "Publishing Date", "Scheduled Generation Date", "Month"]


def _airtable_token() -> str:
    return (os.getenv("AIRTABLE_TOKEN") or "").strip()


def _airtable_fetch(table_id, fields=None):
    """Read ALL records from a table (paginated). Returns list of {id, fields}."""
    import urllib.request
    tok = _airtable_token()
    out, offset = [], None
    base_url = f"https://api.airtable.com/v0/{_AT_BASE}/{table_id}"
    while True:
        params = [("pageSize", "100")]
        if offset:
            params.append(("offset", offset))
        for f in (fields or []):
            params.append(("fields[]", f))
        url = base_url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
        data = json.load(urllib.request.urlopen(req, timeout=30))
        out.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return out


@st.cache_data(ttl=600, show_spinner="📡 Reading Airtable…")
def _airtable_load():
    """Load blogs + clients straight from Airtable, shaped exactly like the CSV rows so
    all downstream code works unchanged. 'Client name' in Blog Keyword is a linked-record
    field (returns record ids), so it is resolved back to the client name via Clients info.
    Returns (blog_rows, clients_rows), or (None, None) if no token / on any error."""
    if not _airtable_token():
        return None, None
    try:
        clients_rows, id_to_name = [], {}
        for r in _airtable_fetch(_AT_TBL_CLIENTS):
            f = {k: (v.strip() if isinstance(v, str) else v)
                 for k, v in r.get("fields", {}).items()}
            f["Record ID"] = r["id"]
            clients_rows.append(f)
            nm = f.get("Client name")
            if isinstance(nm, str) and nm:
                id_to_name[r["id"]] = nm

        blog_rows = []
        for r in _airtable_fetch(_AT_TBL_BLOG, _AT_BLOG_FIELDS):
            src = r.get("fields", {})
            row = {k: (v.strip() if isinstance(v, str) else v) for k, v in src.items()}
            cn = src.get("Client name")
            if isinstance(cn, list):  # linked records -> resolve to a name string
                row["Client name"] = next(
                    (id_to_name.get(x, "") for x in cn if id_to_name.get(x)), "")
            row["Record ID"] = r["id"]  # real Airtable record id (used to mark Done)
            blog_rows.append(row)
        return blog_rows, clients_rows
    except Exception:
        return None, None


def _airtable_mark_done(record_id: str):
    """STRICT WRITE: set ONLY {'Image status': 'Done'} on ONE Blog Keyword record.
    Never writes any other field, row, table, or value. Returns (ok, error_str)."""
    import urllib.request
    if not _airtable_token():
        return False, "no AIRTABLE_TOKEN"
    if not record_id or not str(record_id).startswith("rec"):
        return False, "invalid record id"
    url = f"https://api.airtable.com/v0/{_AT_BASE}/{_AT_TBL_BLOG}/{record_id}"
    body = json.dumps({"fields": {"Image status": "Done"}}).encode()
    req = urllib.request.Request(url, data=body, method="PATCH",
                                 headers={"Authorization": f"Bearer {_airtable_token()}",
                                          "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30)
        return True, ""
    except Exception as e:
        return False, str(e)


def _batch_clients_index(clients_rows) -> dict:
    """Map normalised client name -> Clients-info row."""
    idx = {}
    for r in clients_rows:
        nm = _batch_norm(r.get("Client name", ""))
        if nm:
            idx[nm] = r
    return idx


def _batch_resolve_creds(client_name: str, clients_idx: dict) -> dict:
    """Resolve a client's Webflow credentials.
    Order: Clients info CSV (token+siteId) -> local client_keys.json fallback."""
    row = clients_idx.get(_batch_norm(client_name))
    if row:
        tok = (row.get("webflow_token") or "").strip()
        site = (row.get("siteId") or "").strip()
        if tok and site:
            return {
                "source": "Clients info", "ok": True, "reason": "",
                "token": tok, "site_id": site,
                "collection_id": (row.get("collectionId") or "").strip(),
                "author_id": (row.get("CMS Item ID of the Author") or "").strip(),
                "category_id": (row.get("Blog Category Collection ID") or "").strip(),
            }
    # Fallback: local, gitignored client_keys.json (e.g. Capstone, which has no Airtable row)
    slug = _match_client(client_name) or _batch_norm(client_name)
    key = _get_client_key(slug)
    if key:
        return {
            "source": "local keys", "ok": True,
            "reason": "from local client_keys.json (no siteId in Airtable — resolved via token)",
            "token": key, "site_id": "", "collection_id": "",
            "author_id": "", "category_id": "",
        }
    return {"source": "none", "ok": False,
            "reason": "no webflow_token in Clients info and no local key",
            "token": "", "site_id": "", "collection_id": "", "author_id": "", "category_id": ""}


_BATCH_MONTHS = ["January", "February", "March", "April", "May", "June",
                 "July", "August", "September", "October", "November", "December"]


def _batch_period(row):
    """Best-effort (year, month) for a blog row, from the most reliable date available:
    Scheduled Generation Date -> Publishing Date -> free-text Month column. The Month
    column is unreliable (often no year, or blank on rows that DO have a real date), so
    the parsed date fields win. year may be None if only a bare month name is found;
    returns None if nothing parses."""
    s = (row.get("Scheduled Generation Date") or "").strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return (y, mo)
    s = (row.get("Publishing Date") or "").strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)          # CSV: M/D/YYYY
    if m:
        mo, y = int(m.group(1)), int(m.group(3))
        if 1 <= mo <= 12:
            return (y, mo)
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)          # Airtable: YYYY-MM-DD
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return (y, mo)
    s = (row.get("Month") or "").strip().lower()
    ym = re.search(r"(?:19|20)\d{2}", s)
    for i, nm in enumerate(_BATCH_MONTHS, 1):
        if nm.lower() in s:
            return (int(ym.group(0)) if ym else None, i)
    return None


def _batch_period_label(row) -> str:
    p = _batch_period(row)
    if not p:
        return "— no date —"
    y, mo = p
    return f"{_BATCH_MONTHS[mo - 1]} {y}" if y else f"{_BATCH_MONTHS[mo - 1]} (no year)"


def _batch_period_sortkey(label: str):
    for i, nm in enumerate(_BATCH_MONTHS, 1):
        if label.startswith(nm):
            ys = re.search(r"(?:19|20)\d{2}", label)
            return (int(ys.group(0)) if ys else 9999, i)
    return (99999, 99)


def _batch_slug(url: str) -> str:
    """Last path segment of a blog URL (the CMS slug)."""
    u = re.sub(r"[?#].*$", "", (url or "").strip()).rstrip("/")
    return u.split("/")[-1] if u else ""


def _batch_token_alive(token: str) -> bool:
    """Read-only liveness check for a Webflow token (401/revoked -> False)."""
    if not token:
        return False
    try:
        WebflowClient(token).get_sites()
        return True
    except Exception:
        return False


def _batch_redo_inner(entry: dict, i: int):
    """Regenerate a single inner/content image in-place (same size), re-run QA."""
    r = entry["results"][i]
    try:
        w, h = PILImage.open(io.BytesIO(r["bytes"])).size
    except Exception:
        w, h = DEFAULT_WIDTH, DEFAULT_HEIGHT
    new_prompt = generate_prompt_variation(r.get("prompt", ""), entry.get("title", "")) or r.get("prompt", "")
    raw = _dispatch_image_gen(new_prompt, i + 1, w, h)
    is_ok, reason = check_anatomy(raw)
    opt_bytes, opt_ext = optimize_image(raw, max_kb=200)
    r["bytes"] = opt_bytes
    r["ext"] = opt_ext
    r["prompt"] = new_prompt
    r["defect_reason"] = "" if is_ok else reason
    r["status"] = "ok"


def _batch_redo_cover(entry: dict):
    """Regenerate the cover photo and re-composite Main + Thumbnail (they share one cover)."""
    okr = [r for r in entry.get("results", []) if r.get("status") == "ok"]
    pp = [r["prompt"] for r in okr if r.get("type") != "infographic"]
    cover = _generate_cover_bg(entry.get("title", ""), pp or [entry.get("title", "")])
    ml, tl = ensure_figma_assets_for_client(entry.get("template", ""))
    mtpl, ttpl = make_tpls(entry.get("template", ""), ml, tl)
    entry["main_bytes"] = composite_template(cover, entry.get("title", ""), mtpl)
    entry["thumb_bytes"] = composite_template(cover, entry.get("title", ""), ttpl)


def _batch_upload_entry(entry: dict):
    """Upload one held blog to its client's Webflow (reuses the proven do_webflow_upload).
    Uploads whatever was generated 1:1 — inner images to the body, plus Main/Thumb.
    Redo the bad ones BEFORE uploading; there is no per-image approve toggle."""
    info = entry["wf_info"]
    wf = WebflowClient(entry.get("token", ""))
    okr = [r for r in entry.get("results", []) if r.get("status") == "ok"]
    do_webflow_upload(
        wf, info["site_id"], info["collection_id"], info["item_id"], info["was_published"],
        entry.get("image_urls", []), okr, info.get("site_name", ""), entry.get("client", ""),
        main_bytes=entry.get("main_bytes"),
        thumb_bytes=entry.get("thumb_bytes"),
        blog_title=entry.get("title", ""))
    entry["uploaded"] = True


# ════════════════════════════════════════════════════════════════════════════════
# Revise from Link — Upload section (SECOND tab_revise block; placed here, after
# do_webflow_connect / do_webflow_upload AND the Airtable helpers are defined, so it
# can call them. Streamlit lets you write to the same tab from multiple `with` blocks —
# this renders at the bottom of the Revise tab, under the generated images.)
# ════════════════════════════════════════════════════════════════════════════════
def _revise_resolve_creds(client_slug: str) -> dict | None:
    """Pull a client's Webflow creds (token + collectionId) straight from the Airtable
    'Clients info' table — the same source the Batch Generate tab uses — so the user never
    has to paste a key. Matches the template slug to the Airtable client name via
    _match_client. Returns the creds dict (only when a usable token was found), else None."""
    if not client_slug:
        return None
    try:
        _, clients_rows = _airtable_load()
    except Exception:
        clients_rows = None
    if not clients_rows:
        return None
    idx = _batch_clients_index(clients_rows)
    for row in clients_rows:
        nm = row.get("Client name", "")
        if isinstance(nm, str) and nm and _match_client(nm) == client_slug:
            creds = _batch_resolve_creds(nm, idx)
            if creds.get("ok") and creds.get("token"):
                creds["client_name"] = nm
                return creds
    return None


with tab_revise:
    if st.session_state.get("rv_results"):
        _rv_okr = [r for r in st.session_state["rv_results"] if r["status"] == "ok"]
        _rv_has_cover = bool(st.session_state.get("rv_main_bytes") or st.session_state.get("rv_thumb_bytes"))
        if _rv_okr or _rv_has_cover:
            st.divider()
            _ux_section("⬆️", "Upload to Webflow",
                        "push these straight to the blog — no need to open Webflow")

            _rv_up_slug     = st.session_state.get("rv_client", "")
            _rv_client_disp = _client_display_name(_rv_up_slug) if _rv_up_slug else ""

            # ── Credential resolution (priority order) ──────────────────────────
            #   1) Airtable "Clients info" (token + collectionId) — zero manual entry
            #   2) local client_keys.json / WEBFLOW_API_KEY env — offline fallback
            #   3) manual paste (only when nothing above is available)
            _rv_creds     = _revise_resolve_creds(_rv_up_slug)
            _rv_key       = ""
            _rv_coll      = ""
            _rv_typed_key = ""

            if _rv_creds:
                _rv_key  = _rv_creds["token"]
                _rv_coll = _rv_creds.get("collection_id", "")
                st.success(f"🔑 Using **{_rv_creds['client_name']}**'s Webflow key from "
                           f"**Airtable** (Clients info) — no key needed.")
            else:
                _rv_saved_key = _get_client_key(_rv_up_slug) if _rv_up_slug else os.getenv("WEBFLOW_API_KEY", "")
                if _rv_saved_key:
                    _rv_key = _rv_saved_key
                    _who = f" for {_rv_client_disp}" if _rv_client_disp else ""
                    st.caption(f"🔑 No Airtable key found — using the **saved local key**{_who}. "
                               f"Leave the box blank to use it.")
                else:
                    st.caption("No key in Airtable for this client. Paste the Webflow API key "
                               "once — it's stored locally (gitignored) and reused next time. "
                               "Tip: set `AIRTABLE_TOKEN` in .env to pull keys automatically.")
                # Never pre-fill the field with the real key (avoids the eye-toggle leak).
                _rv_typed_key = st.text_input(
                    "Webflow API key", type="password", value="",
                    placeholder="Paste key (or leave blank to use the saved one)",
                    key="rv_wf_key")
                if _rv_typed_key.strip():
                    _rv_key = _rv_typed_key.strip()

            if st.session_state.get("rv_uploaded"):
                st.success("✅ Already uploaded to Webflow. Regenerate the blog to upload again.")
            elif st.button("⬆️ Upload to Webflow now", type="primary",
                           use_container_width=True, key="rv_upload_btn"):
                if not _rv_key:
                    st.error("No Webflow key available — set `AIRTABLE_TOKEN` in .env or paste a key above.")
                else:
                    # Remember a freshly typed key locally for next time (fallback path only).
                    if (not _rv_creds) and _rv_typed_key.strip() and _rv_up_slug:
                        _save_client_key(_rv_up_slug, _rv_typed_key.strip())
                    try:
                        with st.status("Connecting to Webflow…", expanded=True) as _cs:
                            # Pass the Airtable collectionId (when known) so we don't rely on
                            # keyword auto-detection — that mis-fired for Zhero's "Newsletters".
                            wf, site_id, site_name, collection_id, item_id, was_pub = \
                                do_webflow_connect(
                                    _rv_key, None, _rv_client_disp,
                                    st.session_state.get("rv_slug", ""),
                                    blog_url=st.session_state.get("rv_url_final", ""),
                                    known_collection_id=_rv_coll)
                            _cs.update(label=f"Connected ✓ → {site_name}", state="complete")
                        do_webflow_upload(
                            wf, site_id, collection_id, item_id, was_pub,
                            st.session_state.get("rv_image_urls", []), _rv_okr,
                            site_name, _rv_client_disp or site_name,
                            main_bytes=st.session_state.get("rv_main_bytes"),
                            thumb_bytes=st.session_state.get("rv_thumb_bytes"),
                            blog_title=st.session_state.get("rv_title", ""))
                        st.session_state["rv_uploaded"] = True
                    except Exception as e:
                        st.error(f"Upload failed: {e}")


with tab_batch:
    st.markdown("#### ⚡ Batch Generate — Airtable → Webflow")

    # State-aware stepper
    _res_now = st.session_state.get("abatch_results", {})
    _has_done = any(v.get("status") == "done" for v in _res_now.values())
    _any_pick = any(str(_k).startswith("batch_pick::") and st.session_state.get(_k)
                    for _k in list(st.session_state.keys()))
    _all_up = _has_done and all(v.get("uploaded") for v in _res_now.values()
                                if v.get("status") == "done")
    _active_step = 4 if _all_up else 3 if _has_done else 2 if _any_pick else 1
    _ux_stepper(["Source", "Pick clients", "Generate", "Review", "Upload"], _active_step)

    # ── STEP 1: Data source ──
    _ux_section("1", "Data source", "🔒 read-only to Airtable")
    # Read straight from Airtable; silently fall back to CSVs in the 1/ folder if the
    # token is missing or the API is unreachable, so the tab never hard-fails.
    _at_blog, _at_clients = _airtable_load()
    if _at_blog is not None:
        _blog_rows, _clients_rows, _src_from = _at_blog, (_at_clients or []), "Airtable"
    else:
        _blog_rows, _clients_rows = _batch_autoload()
        _src_from = "1/ CSV (fallback)"

    _src_c1, _src_c2 = st.columns([5, 1])
    with _src_c1:
        if _blog_rows:
            _conn = ("✓ Connected to Airtable" if _src_from == "Airtable"
                     else "⚠️ Airtable not connected — using 1/ CSV fallback")
            st.markdown(
                f"<div class='ux-strip'><span class='ok'>{_conn}</span>"
                f"<span class='meta'>Blog Keyword: <b>{len(_blog_rows)}</b> · "
                f"Clients info: <b>{len(_clients_rows)}</b> · source: {_src_from}</span></div>",
                unsafe_allow_html=True)
    with _src_c2:
        if st.button("🔄 Refresh", use_container_width=True,
                     help="Re-read the latest rows from Airtable"):
            _airtable_load.clear()
            st.rerun()

    if not _blog_rows:
        st.error("No data. Set **AIRTABLE_TOKEN** in `.env` (or drop the CSVs in the `1/` "
                 "folder as a fallback).")
    else:
        # Rows that still need images (any month)
        _review_all = [r for r in _blog_rows
                       if (r.get("Image status") or "").strip().lower() == "review needed"]

        # ── Month / Year filter — derived from the most reliable DATE field (not the messy
        #    free-text "Month" column), so ANY month+year that has review-needed rows appears,
        #    including ones whose "Month" cell is blank (e.g. January 2026). ──
        _ALL_MONTHS = "— All months/years —"
        _period_count = {}
        for r in _review_all:
            _lbl = _batch_period_label(r)
            _period_count[_lbl] = _period_count.get(_lbl, 0) + 1
        _opt_to_label = {_ALL_MONTHS: None}
        _month_options = [_ALL_MONTHS]
        for _lbl in sorted(_period_count, key=_batch_period_sortkey):
            _disp = f"{_lbl}  ({_period_count[_lbl]})"
            _opt_to_label[_disp] = _lbl
            _month_options.append(_disp)
        # Guard: drop a stale selection (e.g. after loading a different CSV) so selectbox won't crash
        if st.session_state.get("batch_month") not in _month_options:
            st.session_state.pop("batch_month", None)
        _month_pick = st.selectbox("📅 Month / Year", _month_options, key="batch_month")
        _picked_label = _opt_to_label.get(_month_pick)
        _review = ([r for r in _review_all if _batch_period_label(r) == _picked_label]
                   if _picked_label else _review_all)

        _groups = {}
        for r in _review:
            _cn = (r.get("Client name") or "").strip()
            if _cn:
                _groups.setdefault(_cn, []).append(r)

        _clients_idx = _batch_clients_index(_clients_rows)
        _client_creds = {cn: _batch_resolve_creds(cn, _clients_idx) for cn in _groups}
        _ready_clients = [cn for cn in _groups if _client_creds[cn]["ok"]]

        if not _review:
            st.success("No _Review needed_ rows in this CSV/month — nothing to generate. 🎉")
        else:
            # ── STEP 2: Pick clients ──
            _ux_section("2", "Pick batch clients")
            _ux_kpis([
                (len(_review), "Blogs · review needed", "cy"),
                (len(_groups), "Clients", ""),
                (len(_ready_clients), "✅ Ready", "green"),
                (len(_groups) - len(_ready_clients), "⚠️ No creds", "amber"),
            ])

            # Quick actions
            _qa1, _qa2, _ = st.columns([1.5, 1, 4])
            with _qa1:
                if st.button("✓ Select all ready", disabled=not _ready_clients,
                             use_container_width=True):
                    for _cn in _ready_clients:
                        st.session_state[f"batch_pick::{_cn}"] = True
                    st.rerun()
            with _qa2:
                if st.button("✕ Clear", use_container_width=True):
                    for _cn in _groups:
                        st.session_state[f"batch_pick::{_cn}"] = False
                    st.rerun()

            # ── Client cards (checkbox grid) — merges old table + multiselect + queue ──
            _pick_keys = {}
            _card_cols = st.columns(3)
            for _idx, (_cn, _items) in enumerate(_groups.items()):
                _cr = _client_creds[_cn]
                _pk = f"batch_pick::{_cn}"
                _pick_keys[_cn] = _pk
                with _card_cols[_idx % 3]:
                    with st.container(border=True):
                        st.markdown("<span class='ux-cardmark'></span>",
                                    unsafe_allow_html=True)
                        if _cr["ok"]:
                            st.checkbox(f"**{_cn}**", key=_pk)
                            st.markdown(
                                f"<div class='ux-cmeta'>{len(_items)} blog · {_cr['source']}</div>"
                                f"<span class='ux-bdg ok'>✅ ready</span>",
                                unsafe_allow_html=True)
                        else:
                            st.checkbox(f"**{_cn}**", value=False, disabled=True,
                                        key=_pk + "::disabled")
                            st.markdown(
                                f"<div class='ux-cmeta'>{len(_items)} blog</div>"
                                f"<span class='ux-bdg no'>❌ {_cr['reason']}</span>",
                                unsafe_allow_html=True)
            _picked = [cn for cn in _ready_clients
                       if st.session_state.get(_pick_keys[cn], False)]

            # ── Queue (compact) for the picked clients ──
            if _picked:
                _total_blogs = sum(len(_groups[cn]) for cn in _picked)
                st.markdown(
                    f"<div class='ux-strip'><span class='ok'>▶ {len(_picked)} client · "
                    f"{_total_blogs} blog naka-queue</span></div>", unsafe_allow_html=True)
                with st.expander(f"View queue ({_total_blogs} blogs)"):
                    for _cn in _picked:
                        _cr = _client_creds[_cn]
                        st.markdown(f"**🏢 {_cn}** — {len(_groups[_cn])} blog · creds: `{_cr['source']}`")
                        for _r in _groups[_cn]:
                            _url = (_r.get("Final blog url") or "").strip().split()[0] if (_r.get("Final blog url") or "").strip() else ""
                            _pub = (_r.get("Publishing Date") or "").strip()
                            _kw = (_r.get("Primary keyword") or "").strip()
                            _line = f"- {_url or '⚠️ no Final blog url'}"
                            if _kw:
                                _line += f"  ·  _{_kw}_"
                            if _pub:
                                _line += f"  ·  publish: **{_pub}**"
                            st.markdown(_line)

                # ── Generate: per-client, resume-safe, NO upload yet ──
                _store = st.session_state.setdefault("abatch_results", {})

                def _rid(_r):
                    return ((_r.get("Record ID") or "").strip()
                            or (_r.get("Final blog url") or "").strip())

                _pending = [(cn, r) for cn in _picked for r in _groups[cn]
                            if _store.get(_rid(r), {}).get("status") != "done"]
                _done_already = _total_blogs - len(_pending)

                _ux_section("3", "Generate",
                            "Main + Thumbnail + inner · auto-QA · no upload")
                _bc1, _bc2 = st.columns([2, 1])
                with _bc1:
                    _go = st.button(f"⚡ Generate selected ({len(_pending)} remaining)",
                                    type="primary", disabled=not _pending,
                                    use_container_width=True)
                with _bc2:
                    if st.button("🗑️ Clear results", use_container_width=True):
                        st.session_state["abatch_results"] = {}
                        st.rerun()
                if _done_already:
                    st.caption(f"🔁 Resume-safe: {_done_already}/{_total_blogs} already done — will skip.")

                if _go:
                    # Pre-flight: read-only token liveness per picked client
                    _dead = set()
                    with st.status("Pre-flight: checking tokens…", expanded=True) as _ps:
                        for _cn in _picked:
                            if _batch_token_alive(_client_creds[_cn]["token"]):
                                st.write(f"✅ {_cn}: token OK")
                            else:
                                _dead.add(_cn)
                                st.write(f"❌ {_cn}: dead/invalid token (401) — skipping")
                        _ps.update(label="Pre-flight done ✓", state="complete")

                    _prog = st.progress(0.0, text="Preparing…")
                    for _i, (_cn, _r) in enumerate(_pending):
                        _rec = _rid(_r)
                        _url = ((_r.get("Final blog url") or "").strip().split() or [""])[0]
                        _creds = _client_creds[_cn]
                        _prog.progress(_i / max(len(_pending), 1),
                                       text=f"{_cn} — {_i + 1}/{len(_pending)}")

                        if _cn in _dead:
                            _store[_rec] = {"client": _cn, "url": _url, "status": "failed",
                                            "error": "dead/invalid Webflow token (401)"}
                            st.session_state["abatch_results"] = _store
                            continue
                        if not _url:
                            _store[_rec] = {"client": _cn, "url": "", "status": "failed",
                                            "error": "no Final blog url"}
                            st.session_state["abatch_results"] = _store
                            continue

                        _slug = _batch_slug(_url)
                        _safe = re.sub(r"[^A-Za-z0-9_\-]", "-", _slug).strip("-") or "blog"
                        _odir = Path("generated_images") / _safe
                        _odir.mkdir(parents=True, exist_ok=True)
                        st.markdown(f"---\n#### 🏢 {_cn} · `{_slug}`")

                        try:
                            with st.status("Connecting to Webflow…", expanded=False) as _cs:
                                wf, site_id, site_name, collection_id, item_id, was_pub = \
                                    do_webflow_connect(_creds["token"], None, _cn, _slug, blog_url=_url,
                                                       known_collection_id=_creds.get("collection_id") or "")
                                _matched = _match_client(site_name)
                                _cs.update(label=f"Connected ✓ → {site_name}", state="complete")

                            _title, _img_urls, _results, _ = run_workflow(
                                _url, _odir, wf_fallback=wf,
                                collection_id_fallback=collection_id,
                                item_id_fallback=item_id, client_slug=_matched)

                            _main_b, _thumb_b = None, None
                            _okr = [r for r in _results if r["status"] == "ok"]
                            if _okr:
                                with st.status("Cover + main/thumbnail…", expanded=False) as _cs2:
                                    try:
                                        _pp = [r["prompt"] for r in _okr if r.get("type") != "infographic"]
                                        _cover = _generate_cover_bg(_title, _pp or [_title])
                                        _ml, _tl = ensure_figma_assets_for_client(_matched)
                                        _mtpl, _ttpl = make_tpls(_matched, _ml, _tl)
                                        _main_b = composite_template(_cover, _title, _mtpl)
                                        _thumb_b = composite_template(_cover, _title, _ttpl)
                                        _cs2.update(label="Main + thumbnail ✓", state="complete")
                                    except Exception as _ce:
                                        _cs2.update(label=f"Compositing failed: {_ce}", state="error")

                            _store[_rec] = {
                                "client": _cn, "url": _url, "slug": _slug, "title": _title,
                                "results": _results, "image_urls": _img_urls,
                                "main_bytes": _main_b, "thumb_bytes": _thumb_b,
                                "template": _matched, "token": _creds["token"],
                                "wf_info": {"site_id": site_id, "collection_id": collection_id,
                                            "item_id": item_id, "was_published": was_pub,
                                            "site_name": site_name},
                                "record": {_k: _r.get(_k, "") for _k in
                                           ("Record ID", "Client name", "Final blog url",
                                            "Primary keyword", "Publishing Date")},
                                "status": "done", "error": "",
                            }
                        except Exception as _ge:
                            _store[_rec] = {"client": _cn, "url": _url, "slug": _slug,
                                            "status": "failed", "error": str(_ge)}
                        st.session_state["abatch_results"] = _store

                    _prog.progress(1.0, text="Done!")
                    _ok = sum(1 for v in _store.values() if v.get("status") == "done")
                    _fail = sum(1 for v in _store.values() if v.get("status") == "failed")
                    st.success(f"✅ Generation done — {_ok} done · {_fail} failed. "
                               "Ready for review below. **Nothing uploaded yet.**")
            else:
                st.info("Pick at least one client above to see the queue.")

            # ══ Review: contact-sheet (Redo) → upload ══
            _store_disp = st.session_state.get("abatch_results", {})
            if _store_disp:
                _dn = sum(1 for v in _store_disp.values() if v.get("status") == "done")
                _fl = sum(1 for v in _store_disp.values() if v.get("status") == "failed")
                _upc = sum(1 for v in _store_disp.values() if v.get("uploaded"))
                _ux_section("4", "Review",
                            f"{_dn} done · {_fl} failed · {_upc} uploaded")
                _ux_kpis([
                    (_dn, "✅ Generated", "green"),
                    (_fl, "❌ Failed", "red" if _fl else ""),
                    (_upc, "⬆️ Uploaded", "cy"),
                ])
                st.caption("Redo the bad ones BEFORE uploading. Everything shown here "
                           "(Main + Thumb + inner) uploads 1:1 to Webflow when you click "
                           "**⬆️ Upload** below. 🚩 = flagged by auto-QA.")

                _by_client = {}
                for _rid_k, _v in _store_disp.items():
                    _by_client.setdefault(_v.get("client", "?"), []).append((_rid_k, _v))

                for _cn2, _pairs in _by_client.items():
                    st.markdown(f"#### 🏢 {_cn2}")
                    for _rk, _v in _pairs:
                        if _v.get("status") != "done":
                            st.markdown(f"- ❌ `{_v.get('slug') or _v.get('url','')}` — {_v.get('error','')}")
                            continue
                        _okimgs = [(_ii, r) for _ii, r in enumerate(_v.get("results", []))
                                   if r.get("status") == "ok"]
                        _flags = sum(1 for _ii, r in _okimgs if r.get("defect_reason"))
                        _hdr = (f"**📄 `{_v.get('slug','')}`** — {(_v.get('title','') or '')[:55]}"
                                f" · Main + Thumb + {len(_okimgs)} inner")
                        if _flags:
                            _hdr += f" · <span style='color:#f5c451'>🚩 {_flags} flagged</span>"
                        if _v.get("uploaded"):
                            _hdr += " · <span style='color:#39d98a'>✅ uploaded</span>"
                        st.markdown(_hdr, unsafe_allow_html=True)

                        _tiles = []
                        if _v.get("main_bytes"):
                            _tiles.append(("MAIN", _v["main_bytes"], "inner_no", -1, False))
                        if _v.get("thumb_bytes"):
                            _tiles.append(("THUMB", _v["thumb_bytes"], "inner_no", -2, False))
                        for _ii, r in _okimgs:
                            _tiles.append((f"INNER {_ii + 1}", r.get("bytes"), "inner", _ii,
                                           bool(r.get("defect_reason"))))
                        if not _v.get("main_bytes") and not _v.get("thumb_bytes"):
                            st.caption("⚠️ No Main/Thumb — this client likely has no template.")

                        _locked = _v.get("uploaded", False)
                        _cols = st.columns(min(len(_tiles), 6)) if _tiles else []
                        for _ti, (_cap, _b, _kind, _iidx, _flagged) in enumerate(_tiles):
                            with _cols[_ti % len(_cols)]:
                                if _b:
                                    st.image(_b, caption=(f"🚩 {_cap}" if _flagged else _cap),
                                             use_container_width=True)
                                # Controls sit directly under the tile they act on.
                                if _kind == "inner" and not _locked:
                                    if st.button("🔄 Redo", key=f"redo_{_rk}_in_{_iidx}",
                                                 use_container_width=True):
                                        with st.spinner(f"Redo {_cap}…"):
                                            _batch_redo_inner(_v, _iidx)
                                        st.session_state["abatch_results"][_rk] = _v
                                        st.rerun()
                                elif _iidx == -1 and not _locked:  # MAIN tile
                                    if st.button("🔄 Redo Main+Thumb", key=f"redo_{_rk}_cover",
                                                 use_container_width=True,
                                                 disabled=not _v.get("template")):
                                        with st.spinner("Redo cover…"):
                                            _batch_redo_cover(_v)
                                        st.session_state["abatch_results"][_rk] = _v
                                        st.rerun()
                        st.divider()

                # ── Upload approved to Webflow ──
                _pending_up = [(_rk, _v) for _rk, _v in _store_disp.items()
                               if _v.get("status") == "done" and not _v.get("uploaded")]
                _ux_section("5", "Upload to Webflow",
                            "live client site · auto-marks Airtable 'Done'")
                if not _pending_up:
                    st.info("Nothing left to upload.")
                else:
                    if st.button(f"⬆️ Upload {len(_pending_up)} blogs to Webflow",
                                 type="primary", use_container_width=True):
                        _uprog = st.progress(0.0)
                        for _ui, (_rk, _v) in enumerate(_pending_up):
                            _uprog.progress(_ui / max(len(_pending_up), 1),
                                            text=f"Uploading {_v.get('slug','')}…")
                            try:
                                with st.status(f"⬆️ {_v.get('client','')} · {_v.get('slug','')}",
                                               expanded=False) as _us:
                                    _batch_upload_entry(_v)
                                    # STRICT: after a SUCCESSFUL upload, mark ONLY this row's
                                    # "Image status" = "Done" in Airtable. Nothing else is touched.
                                    _recid = (_v.get("record", {}) or {}).get("Record ID", "")
                                    _md_ok, _md_err = _airtable_mark_done(_recid)
                                    _v["airtable_done"] = _md_ok
                                    _v["airtable_done_error"] = "" if _md_ok else _md_err
                                    _done_lbl = f"Uploaded ✓ {_v.get('slug','')}"
                                    _done_lbl += " · Airtable Done ✓" if _md_ok else " · Airtable Done ✗"
                                    _us.update(label=_done_lbl, state="complete")
                                _v["upload_error"] = ""
                            except Exception as _ue:
                                _v["upload_error"] = str(_ue)
                                st.error(f"⛔ {_v.get('slug','')}: {_ue}")
                            st.session_state["abatch_results"][_rk] = _v
                        _airtable_load.clear()  # refresh so the marked-Done rows drop out
                        _uprog.progress(1.0, text="Done!")
                        _md_fail = [_v.get("slug", "") for _rk, _v in _pending_up
                                    if _v.get("airtable_done") is False and not _v.get("upload_error")]
                        if _md_fail:
                            st.warning("✅ Uploaded, but couldn't mark Done in Airtable for: "
                                       + ", ".join(_md_fail) + " — mark them manually.")
                        else:
                            st.success("✅ Upload done · marked Done in Airtable.")
                        st.rerun()
