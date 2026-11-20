"""Compose the privfill demo GIF from real form shots and real command output (fake vault, fake data).

Usage: python compose.py <en|zh> <out.gif> <work root>   (normally called by make.sh; needs Pillow
and the Noto Sans CJK fonts)
"""
import glob
import os
import sys

from PIL import Image, ImageDraw, ImageFont

LANG = sys.argv[1]
OUT = sys.argv[2]
G = sys.argv[3]
WORK = os.path.join(G, "work-" + LANG)
FR = os.path.join(G, "frames-" + LANG)

W, H = 880, 540
BG = (243, 244, 246)
INK = (17, 24, 39)
MUTED = (75, 85, 99)
BLUE = (37, 99, 235)
GREEN = (22, 163, 74)
TERM = (17, 24, 39)
TERM_INK = (229, 231, 235)
TERM_DIM = (156, 163, 175)
TERM_CMD = (147, 197, 253)
TERM_OK = (134, 239, 172)
CARD = (255, 255, 255)
LINE = (229, 231, 235)

CJK = "/usr/share/fonts/noto-cjk/NotoSansCJK-%s.ttc"
sans_b = ImageFont.truetype(CJK % "Bold", 21, index=2)
sans = ImageFont.truetype(CJK % "Regular", 15, index=2)
mono = ImageFont.truetype(CJK % "Regular", 15, index=7)
mono_b = ImageFont.truetype(CJK % "Bold", 15, index=7)
pill_f = ImageFont.truetype(CJK % "Bold", 15, index=2)


def read(name):
    with open(os.path.join(WORK, name), encoding="utf8") as f:
        return f.read().rstrip("\n")


T = {
    "en": dict(
        c1="You ask for a form to be filled in",
        you="You", asst="Assistant",
        ask="Fill in the registration form. My phone number isn't stored yet.",
        tell="A form is about to appear on your screen.",
        formcmd="privfill form '{\"title\": \"Registration\", \"fields\": [ … ]}'",
        c2="You type into a local window, not into the chat",
        c3="The assistant is told item names, never values",
        c4="It writes placeholders; privfill fills them in locally",
        master="registration.md  (master, written by the assistant)",
        rendercmd="privfill render registration.md 'out/{{full name}}_registration.md'",
        c5="You open the result. The values never entered the chat.",
        opened="opened by you",
    ),
    "zh": dict(
        c1="你让助手帮忙填报名表",
        you="你", asst="助手",
        ask="帮我填报名表，手机号还没存。",
        tell="马上会在你屏幕上弹出一个表单。",
        formcmd="privfill form '{\"title\": \"报名信息\", \"fields\": [ … ]}'",
        c2="你在本机窗口里填写，不经过对话",
        c3="助手只知道条目名，不知道内容",
        c4="助手写占位符，privfill 在本机填入",
        master="registration.md（母版，由助手撰写）",
        rendercmd="privfill render registration.md 'out/{{姓名}}_报名表.md'",
        c5="你打开成品。这些内容从没进过对话。",
        opened="由你打开",
    ),
}[LANG]


def base(step, caption):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((30, 20, 62, 50), 8, fill=BLUE)
    d.text((46, 35), str(step), font=pill_f, fill=(255, 255, 255), anchor="mm")
    d.text((76, 35), caption, font=sans_b, fill=INK, anchor="lm")
    for i in range(5):
        d.ellipse((W - 30 - (5 - i) * 16, 31, W - 30 - (5 - i) * 16 + 8, 39),
                  fill=BLUE if i < step else (209, 213, 219))
    return im, d


def terminal(d, box, lines):
    """lines: list of (kind, text). kinds: you, asst, cmd, out, ok, blank."""
    x0, y0, x1, y1 = box
    content = sum(12 if k == "blank" else 26 for k, _ in lines)
    y1 = min(y1, max(y0 + 150, y0 + 44 + content + 18))
    d.rounded_rectangle((x0, y0, x1, y1), 12, fill=TERM)
    for i, c in enumerate([(248, 113, 113), (251, 191, 36), (74, 222, 128)]):
        d.ellipse((x0 + 16 + i * 18, y0 + 14, x0 + 26 + i * 18, y0 + 24), fill=c)
    y = y0 + 44
    for kind, text in lines:
        if kind == "blank":
            y += 12
            continue
        if kind in ("you", "asst"):
            label = (T["you"] if kind == "you" else T["asst"]) + " ›"
            d.text((x0 + 20, y), label, font=mono_b, fill=TERM_CMD if kind == "you" else TERM_OK)
            lx = x0 + 20 + d.textlength(label + " ", font=mono_b)
            d.text((lx, y), text, font=mono, fill=TERM_INK)
        elif kind == "cmd":
            d.text((x0 + 20, y), "$", font=mono_b, fill=TERM_DIM)
            d.text((x0 + 38, y), text, font=mono, fill=TERM_CMD)
        elif kind == "ok":
            d.text((x0 + 20, y), text, font=mono, fill=TERM_OK)
        else:
            d.text((x0 + 20, y), text, font=mono, fill=TERM_INK)
        y += 26
    return y


def card(d, box, title, body, highlight=()):
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, 12, fill=CARD, outline=LINE)
    d.text((x0 + 18, y0 + 14), title, font=sans, fill=MUTED)
    d.line((x0, y0 + 44, x1, y0 + 44), fill=LINE)
    y = y0 + 58
    for line in body.split("\n"):
        x = x0 + 18
        rest = line
        # draw highlighted spans in green
        while rest:
            hits = [(rest.find(h), h) for h in highlight if h and rest.find(h) >= 0]
            if not hits:
                d.text((x, y), rest, font=mono, fill=INK)
                break
            i, h = min(hits)
            d.text((x, y), rest[:i], font=mono, fill=INK)
            x += d.textlength(rest[:i], font=mono)
            w = d.textlength(h, font=mono)
            d.rounded_rectangle((x - 3, y - 1, x + w + 3, y + 21), 4, fill=(220, 252, 231))
            d.text((x, y), h, font=mono_b, fill=(21, 128, 61))
            x += w
            rest = rest[i + len(h):]
        y += 26


frames = []  # (image, ms)


def add(im, ms):
    frames.append((im.copy(), ms))


def typed(step, caption, before, cmd, after=(), box=(30, 70, W - 30, H - 24), pre=None):
    """Type cmd character by character after `before` lines, then show `after` lines."""
    chunk = max(2, len(cmd) // 14)
    for n in list(range(0, len(cmd), chunk)) + [len(cmd)]:
        im, d = base(step, caption)
        if pre:
            pre(d)
        terminal(d, box, list(before) + [("cmd", cmd[:n] + "▌")])
        add(im, 45)
    im, d = base(step, caption)
    if pre:
        pre(d)
    terminal(d, box, list(before) + [("cmd", cmd)])
    add(im, 350)
    if after:
        im, d = base(step, caption)
        if pre:
            pre(d)
        terminal(d, box, list(before) + [("cmd", cmd)] + list(after))
        return im, d
    return im, d


# Scene 1: the request.
chat = [("you", T["ask"]), ("blank", ""), ("asst", T["tell"]), ("blank", "")]
for k in range(1, len(chat) + 1):
    im, d = base(1, T["c1"])
    terminal(d, (30, 70, W - 30, H - 24), chat[:k])
    add(im, 900 if k in (1, 3) else 150)
im, d = typed(1, T["c1"], chat, T["formcmd"])
add(im, 600)

# Scene 2: the real form, typed into.
shots = sorted(glob.glob(os.path.join(FR, "form_*.png")))
for i, p in enumerate(shots):
    im, d = base(2, T["c2"])
    f = Image.open(p).convert("RGB")
    s = min((W - 60) / f.width, (H - 84) / f.height, 1.0)
    f = f.resize((int(f.width * s), int(f.height * s)), Image.LANCZOS)
    im.paste(f, ((W - f.width) // 2, 66))
    ImageDraw.Draw(im).rounded_rectangle(((W - f.width) // 2 - 1, 65, (W + f.width) // 2, 66 + f.height), 10,
                                         outline=(209, 213, 219))
    add(im, 1100 if i == 0 else (1800 if i == len(shots) - 1 else 110))

# Scene 3: what the assistant gets back.
form_out = [("ok", l) for l in read("form.txt").split("\n")]
seen = chat + [("cmd", T["formcmd"])] + form_out + [("blank", "")]
im, d = base(3, T["c3"])
terminal(d, (30, 70, W - 30, H - 24), seen)
add(im, 1300)
im, d = typed(3, T["c3"], seen, "privfill keys", [("ok", read("keys.txt"))])
add(im, 2200)

# Scene 4: master and render.
master = read("../work-" + LANG + "/registration.md")


def draw_master(d):
    import re
    card(d, (30, 66, W - 30, 252), T["master"], master, highlight=sorted(set(re.findall(r"\{\{[^}]+\}\}", master))))


im, d = base(4, T["c4"])
draw_master(d)
add(im, 1600)
box4 = (30, 266, W - 30, H - 24)
im, d = typed(4, T["c4"], [], T["rendercmd"], [("ok", read("render.txt"))], box=box4, pre=draw_master)
add(im, 2400)

# Scene 5: the person opens the output.
outname = read("outname.txt")
with open(os.path.join(WORK, "out", outname), encoding="utf8") as f:
    filled = f.read().rstrip("\n")
values = [l.split("：", 1)[1] if "：" in l else l.split(":", 1)[1].strip() for l in filled.split("\n")[2:]]
values = [v.lstrip("*") for v in values]
im, d = base(5, T["c5"])
card(d, (30, 66, W - 30, 290), "out/" + outname + "   · " + T["opened"], filled, highlight=values)
terminal(d, (30, 304, W - 30, H - 24), [("cmd", T["rendercmd"]), ("ok", read("render.txt"))])
add(im, 4200)

# Save: one shared palette keeps the file small and the colours steady.
imgs = [f for f, _ in frames]
pal = imgs[len(imgs) // 2].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
q = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in imgs]
q[0].save(OUT, save_all=True, append_images=q[1:], duration=[ms for _, ms in frames], loop=0, optimize=True,
          disposal=1)
print(OUT, len(q), "frames", os.path.getsize(OUT) // 1024, "KB", sum(ms for _, ms in frames) / 1000, "s")
