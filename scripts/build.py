#!/usr/bin/env python3
"""Build the HyperFrames composition from storyboard.json.

    python3 scripts/build.py [storyboard.json] [--out DIR]

Writes index.html, compositions/<scene>.html, compositions/captions.html and cue-sheet.md.
Generated files are meant to be committed: Studio edits them, and re-running the build
overwrites them from the storyboard. Everything below encodes the rules HyperFrames wants
(initial hidden states via gsap.set, transforms only, fromTo in seeked scenes, transparent roots
for scenes that whip in over the previous one, every timed element with class="clip").
"""
import argparse, html, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TPL = os.path.join(ROOT, "templates")

# bundled sounds: where each file's peak sits, so a cue lands one frame after the picture
SFX = {
    "click":        {"file": "assets/sfx/click.mp3",         "dur": 0.37, "peak": 0.073},
    "pop":          {"file": "assets/sfx/pop.mp3",           "dur": 0.72, "peak": 0.120},
    "whoosh":       {"file": "assets/sfx/whoosh.mp3",        "dur": 0.58, "peak": 0.162},
    "notification": {"file": "assets/sfx/notification.mp3",  "dur": 2.46, "peak": 0.229},
    "impact":       {"file": "assets/sfx/impact-bass-1.mp3", "dur": 2.12, "peak": 0.073},
}
FRAME = 1 / 30


def tpl(name):
    with open(os.path.join(TPL, name)) as f:
        return f.read()


def fill(t, **kw):
    for k, v in kw.items():
        t = t.replace("{{%s}}" % k, str(v))
    return t


def esc(s):
    return html.escape(str(s), quote=True)


def rich(text):
    """'Where did your money *go?*' → words as spans, *…* highlighted."""
    out = []
    for w in text.split():
        hi = w.startswith("*") and w.endswith("*") and len(w) > 2
        out.append('<span class="w%s">%s</span>' % (" hi" if hi else "", esc(w.strip("*"))))
    return " ".join(out)


def hero_html(text):
    parts = []
    for w in text.split():
        hi = w.startswith("*") and w.endswith("*") and len(w) > 2
        parts.append('<span class="q">%s</span>' % esc(w.strip("*")) if hi else esc(w))
    return " ".join(parts)


class Build:
    def __init__(self, sb, out):
        self.sb, self.out = sb, out
        self.b = sb["brand"]
        self.c = self.b["colors"]
        self.v = sb["video"]
        self.W, self.H = self.v["width"], self.v["height"]
        self.p = dict(left=160, top=330, width=760, height=1528, radius=64, bezel=14, crop_top=66)
        self.p.update(self.b.get("phone", {}))
        self.p["img_width"] = self.p["width"] - 2 * self.p["bezel"]
        self.p["screen_h"] = self.p["height"] - 2 * self.p["bezel"]
        self.sfx = []      # (time, name, volume, note)
        self.cues = []     # for cue-sheet.md
        font = self.b.get("font", "Montserrat")
        self.font_fallback = '"%s", sans-serif' % font
        faces = []
        for weight, path in (self.b.get("font_files") or {}).items():
            faces.append('    @font-face { font-family: "%s"; font-weight: %s; src: url("%s"); }' % (font, weight, path))
        self.font_faces = "\n".join(faces)

    # ---------- shared CSS ----------
    def phone_css(self, sid):
        p = self.p
        return (
            "    .phone { position: absolute; left: %dpx; top: %dpx; width: %dpx; height: %dpx; border-radius: %dpx; background: %s; padding: %dpx; box-shadow: 0 30px 80px rgba(0,0,0,.55); border: 1px solid %s; }\n"
            "    .phone .screen { width: %dpx; height: %dpx; border-radius: %dpx; overflow: hidden; background: %s; position: relative; }\n"
            "    .phone .screen img { position: absolute; left: 0; top: -%dpx; width: %dpx; height: auto; }\n"
            "    #%s-cam { position: absolute; inset: 0; }\n"
            "    #%s-zoom { position: absolute; inset: 0; }\n"
        ) % (p["left"], p["top"], p["width"], p["height"], p["radius"], self.c.get("bezel", "#1c1c22"), p["bezel"],
             self.c.get("bezel_edge", "#2a2a33"), p["img_width"], p["screen_h"], p["radius"] - 12, self.c["ground"],
             p["crop_top"], p["img_width"], sid, sid)

    def card_css(self):
        c = self.c
        return (
            "    .card { position: relative; width: 900px; background: %s; border: 1.5px solid %s33; border-radius: 16px; box-shadow: 0 4px 10px rgba(0,0,0,.05); padding: 26px 32px; display: flex; align-items: center; gap: 24px; color: %s; }\n"
            "    .card .icon { flex: none; width: 88px; height: 88px; border-radius: 44px; background: %s29; display: flex; align-items: center; justify-content: center; }\n"
            "    .card .icon img { width: 56px; height: 56px; }\n"
            "    .card .text { min-width: 0; }\n"
            "    .card .title { font-weight: 600; font-size: 36px; line-height: 1.3; }\n"
            "    .card .meta { color: %s; font-weight: 700; font-size: 28px; letter-spacing: .04em; margin-top: 8px; }\n"
        ) % (c["surface"], c["accent"], c["ink"], c["accent"], c["highlight"])

    def card_html(self, card, cid):
        icon = '<div class="icon"><img src="%s" alt="" /></div>' % esc(card["icon"]) if card.get("icon") else ""
        meta = '<div class="meta">%s</div>' % esc(card["meta"]) if card.get("meta") else ""
        return '<div class="card" id="%s">%s<div class="text"><div class="title">%s</div>%s</div></div>' % (cid, icon, esc(card["title"]), meta)

    def screens_html(self, sid, screens):
        return "\n".join('            <img id="%s-s%d" src="%s" alt="%s" />' % (sid, i, esc(s if "/" in s else "assets/screens/" + s), esc(os.path.splitext(os.path.basename(s))[0]))
                         for i, s in enumerate(screens))

    def phone_html(self, sid, screens, extra=""):
        return ('      <div id="%s-cam">\n       <div id="%s-zoom">\n        <div class="phone" id="%s-phone"><div class="screen">\n%s\n%s        </div></div>\n       </div>\n      </div>'
                % (sid, sid, sid, self.screens_html(sid, screens), extra))

    def cue(self, t, name, vol, note):
        self.sfx.append((round(t, 3), name, vol, note))

    # ---------- scene types ----------
    def scene_hook(self, s):
        sid = s["id"]; c = self.c
        pre = s.get("preloaded", 2)
        cards = s["cards"]; pops = s.get("pops", [])
        style = self.card_css() + (
            "    #%s-pile { position: absolute; left: 90px; top: 250px; width: 900px; display: flex; flex-direction: column; gap: 24px; }\n"
            "    #%s-line { position: absolute; left: 70px; right: 70px; top: 560px; text-align: center; color: %s; font-weight: 900; font-size: 128px; line-height: 1.02; letter-spacing: -.03em; }\n"
            "    #%s-line .q { color: %s; }\n"
            "    #%s-flood { position: absolute; left: %dpx; top: %dpx; width: 200px; height: 200px; margin-left: -100px; margin-top: -100px; border-radius: 50%%; background: %s; }\n"
        ) % (sid, sid, c["ink"], sid, c["highlight"], sid, self.W // 2, int(self.H * 0.78), c["flood"])
        markup = '      <div id="%s-pile" data-layout-allow-overlap>\n%s\n      </div>\n' % (sid, "\n".join("        " + self.card_html(cd, "%s-c%d" % (sid, i)) for i, cd in enumerate(cards)))
        tlj = []
        for i in range(len(cards)):
            if i < pre:
                tlj.append('    tl.fromTo("#%s-c%d", { y: 0, opacity: 1 }, { y: 0, opacity: 1, duration: 0.01 }, 0);' % (sid, i))
            else:
                t = pops[i - pre] if i - pre < len(pops) else 0.4 * (i - pre + 1)
                tlj.append('    tl.fromTo("#%s-c%d", { y: 140, opacity: 0, scale: 0.92, transformOrigin: "50%% 100%%" }, { y: 0, opacity: 1, scale: 1, duration: 0.35, ease: "back.out(1.4)" }, %s);' % (sid, i, t))
                self.cue(s["start"] + t, "pop" if (i - pre) % 2 == 0 else "click", 1.0 if (i - pre) % 2 == 0 else 0.9, "card lands")
        hero = s.get("hero")
        if hero:
            markup += '      <div id="%s-line">%s</div>\n' % (sid, hero_html(hero["text"]).replace(" / ", "<br />"))
            tlj.append('    tl.to("#%s-pile", { filter: "blur(7px)", opacity: 0.35, duration: 0.3, ease: "power2.inOut" }, %s);' % (sid, hero["at"] - 0.05))
            tlj.append('    tl.fromTo("#%s-line", { opacity: 0, scale: 1.35, filter: "blur(10px)" }, { opacity: 1, scale: 1, filter: "blur(0px)", duration: 0.32, ease: "expo.out" }, %s);' % (sid, hero["at"]))
            self.cue(s["start"] + hero["at"], "whoosh", 0.7, "hero line")
            self.cues.append((s["start"] + hero["at"], "hero line: " + hero["text"]))
        if s.get("flood_at") is not None:
            markup += '      <div id="%s-flood"></div>\n' % sid
            tlj.append('    tl.fromTo("#%s-flood", { scale: 0 }, { scale: 14, duration: 0.42, ease: "power3.in" }, %s);' % (sid, s["flood_at"]))
            self.cue(s["start"] + s["flood_at"], "whoosh", 0.4, "flood")
        self.cues.append((s["start"], "hook: %d cards pile in" % len(cards)))
        return style, markup, "\n".join(tlj), c["ground"]

    def scene_reveal(self, s):
        sid = s["id"]; c = self.c
        screens = s.get("screens", [])
        style = self.phone_css(sid) + (
            "    #%s-drain { position: absolute; inset: 0; background: %s; }\n"
            "    #%s-brand { position: absolute; left: 0; right: 0; top: 150px; height: 140px; display: flex; align-items: center; justify-content: center; gap: 28px; }\n"
            "    #%s-tile { width: 120px; height: 120px; border-radius: 30px; background: #fff; display: flex; align-items: center; justify-content: center; }\n"
            "    #%s-tile img { width: 104px; height: 104px; }\n"
            "    #%s-word { color: %s; font-weight: 900; font-size: 84px; letter-spacing: -.03em; }\n"
        ) % (sid, c["flood"], sid, sid, sid, sid, c["ink"])
        markup = self.phone_html(sid, screens) if screens else ""
        markup += '\n      <div id="%s-brand"><div id="%s-tile"><img src="%s" alt="%s mark" /></div><div id="%s-word">%s</div></div>\n' % (sid, sid, esc(self.b["logo"]), esc(self.b["name"]), sid, esc(self.b["name"]))
        tlj = []
        if s.get("drain", True):
            markup += '      <div id="%s-drain"></div>\n' % sid
            tlj.append('    tl.fromTo("#%s-drain", { opacity: 1 }, { opacity: 0, duration: 0.3, ease: "power2.inOut" }, 0);' % sid)
        for i in range(1, len(screens)):
            tlj.append('    gsap.set("#%s-s%d", { opacity: 0, x: %d });' % (sid, i, self.p["img_width"]))
        if screens:
            tlj.append('    tl.fromTo("#%s-phone", { y: 1000, scale: 0.9, transformOrigin: "50%% 100%%" }, { y: 0, scale: 1, duration: 0.8, ease: "expo.out" }, 0.05);' % sid)
        ba = s.get("brand_at", 0.15)
        tlj.append('    tl.fromTo("#%s-tile", { scale: 0, rotate: -12 }, { scale: 1, rotate: 0, duration: 0.5, ease: "back.out(1.6)" }, %s);' % (sid, ba))
        tlj.append('    tl.fromTo("#%s-word", { opacity: 0, filter: "blur(8px)", x: -20 }, { opacity: 1, filter: "blur(0px)", x: 0, duration: 0.3, ease: "power4.out" }, %s);' % (sid, ba + 0.05))
        self.cue(s["start"] + ba, "pop", 1.0, "brand mark lands")
        for i, t in enumerate(s.get("swipes", [])):
            if i + 1 >= len(screens):
                break
            tlj.append('    tl.to("#%s-s%d", { x: -%d, duration: 0.45, ease: "power2.inOut" }, %s);' % (sid, i, self.p["img_width"], t))
            tlj.append('    tl.to("#%s-s%d", { x: 0, opacity: 1, duration: 0.45, ease: "power2.inOut" }, %s);' % (sid, i + 1, t))
            self.cue(s["start"] + t, "whoosh", 0.4, "screen swipe")
        self.cues.append((s["start"], "reveal: %s mark + name" % self.b["name"]))
        return style, markup, "\n".join(tlj), c["ground"]

    def scene_feature(self, s):
        sid = s["id"]; c = self.c; p = self.p
        screens = s["screens"]
        style = self.phone_css(sid) + self.card_css() + (
            "    #%s-card { position: absolute; left: 90px; top: 120px; width: 900px; }\n"
            "    #%s-bar { position: absolute; left: 36px; height: 8px; width: 0; background: %s; border-radius: 4px; }\n"
            "    #%s-ov { position: absolute; left: 0; top: -%dpx; width: %dpx; height: auto; }\n"
        ) % (sid, sid, c["accent"], sid, p["crop_top"], p["img_width"])
        extra = ""
        overlays = [m for m in s["moves"] if m["kind"] == "overlay"]
        if overlays:
            src = overlays[0]["src"]
            extra = '            <img id="%s-ov" src="%s" alt="overlay" />\n' % (sid, esc(src if "/" in src else "assets/screens/" + src))
        bars = [m for m in s["moves"] if m["kind"] == "progress"]
        if bars:
            extra += '            <div id="%s-bar"></div>\n' % sid
        markup = self.phone_html(sid, screens, extra)
        drops = [m for m in s["moves"] if m["kind"] == "card-drop"]
        if drops:
            d = drops[0]
            markup += '\n      <div id="%s-card">%s</div>\n' % (sid, self.card_html(d, sid + "-cardel"))
        tlj = []
        for i in range(1, len(screens)):
            tlj.append('    gsap.set("#%s-s%d", { opacity: 0, y: 260 });' % (sid, i))
        if overlays:
            tlj.append('    gsap.set("#%s-ov", { opacity: 0, y: -200 });' % sid)
        tr = s.get("transition", "cut")
        if tr == "whip-over":
            frm = s.get("from", "bottom")
            start = {"bottom": "y: 900", "top": "y: -900", "right": "x: 1100", "left": "x: -1100"}[frm]
            end = "y: 0" if frm in ("bottom", "top") else "x: 0"
            tlj.append('    tl.fromTo("#%s-cam", { %s, filter: "blur(10px)" }, { %s, filter: "blur(0px)", duration: 0.4, ease: "expo.out" }, 0);' % (sid, start, end))
            self.cue(s["start"], "whoosh", 0.5, "%s whips in" % sid)
        elif tr == "settle":
            tlj.append('    tl.fromTo("#%s-cam", { scale: 1.18, x: 0, y: 0, filter: "blur(6px)", transformOrigin: "50%% 50%%" }, { scale: 1, filter: "blur(0px)", duration: 0.45, ease: "expo.out" }, 0);' % sid)
        current = 0
        for m in s["moves"]:
            t = m["t"]; k = m["kind"]; at = s["start"] + t
            if k == "card-drop":
                tlj.append('    tl.fromTo("#%s-card", { y: -420, opacity: 0 }, { y: 0, opacity: 1, duration: 0.45, ease: "expo.out" }, %s);' % (sid, t))
                tlj.append('    tl.to("#%s-card", { y: 760, scale: 0.12, opacity: 0, transformOrigin: "50%% 50%%", duration: 0.45, ease: "power3.in" }, %s);' % (sid, m["drop_at"]))
                self.cue(at, "whoosh", 0.4, "card arrives"); self.cue(s["start"] + m["drop_at"], "whoosh", 0.5, "card drops into phone")
                self.cues.append((at, "%s: card '%s' arrives, drops at %.1fs" % (sid, m["title"], s["start"] + m["drop_at"])))
            elif k == "show":
                n = m["screen"]
                if m.get("rise"):
                    tlj.append('    gsap.set("#%s-s%d", { y: 120 });' % (sid, n))
                    tlj.append('    tl.to("#%s-s%d", { opacity: 0, duration: 0.1 }, %s);' % (sid, current, t))
                    tlj.append('    tl.to("#%s-s%d", { opacity: 1, y: 0, duration: 0.3, ease: "expo.out" }, %s);' % (sid, n, t))
                elif m.get("settle"):
                    tlj.append('    gsap.set("#%s-s%d", { y: 0, scale: 1.06, transformOrigin: "50%% 30%%" });' % (sid, n))
                    tlj.append('    tl.to("#%s-s%d", { opacity: 0, duration: 0.1 }, %s);' % (sid, current, t))
                    tlj.append('    tl.to("#%s-s%d", { opacity: 1, scale: 1, duration: 0.25, ease: "expo.out" }, %s);' % (sid, n, t))
                    self.cue(at, "pop", 1.0, "screen lands")
                else:
                    tlj.append('    gsap.set("#%s-s%d", { y: 0 });' % (sid, n))
                    tlj.append('    tl.to("#%s-s%d", { opacity: 0, duration: 0.1 }, %s);' % (sid, current, t))
                    tlj.append('    tl.to("#%s-s%d", { opacity: 1, duration: 0.18, ease: "power2.out" }, %s);' % (sid, n, t))
                self.cues.append((at, "%s: show %s" % (sid, screens[n])))
                current = n
            elif k == "scroll":
                n = m["screen"]
                tlj.append('    tl.to("#%s-s%d", { y: -260, opacity: 0, duration: 0.45, ease: "power2.inOut" }, %s);' % (sid, current, t))
                tlj.append('    tl.to("#%s-s%d", { y: 0, opacity: 1, duration: 0.45, ease: "power2.inOut" }, %s);' % (sid, n, t))
                self.cue(at, "whoosh", 0.4, "scroll"); self.cues.append((at, "%s: scroll to %s" % (sid, screens[n])))
                current = n
            elif k == "punch":
                tlj.append('    tl.to("#%s-cam", { scale: %s, x: %s, y: %s, duration: %s, ease: "power2.inOut", transformOrigin: "50%% 50%%" }, %s);' % (sid, m.get("scale", 1.5), m.get("x", 0), m.get("y", 0), m.get("duration", 0.6), t))
                self.cue(at, "click", 0.9, "punch-in"); self.cues.append((at, "%s: punch-in ×%s" % (sid, m.get("scale", 1.5))))
            elif k == "reset":
                tlj.append('    tl.to("#%s-cam", { scale: 1, x: 0, y: 0, duration: %s, ease: "power2.inOut" }, %s);' % (sid, m.get("duration", 0.5), t))
            elif k == "overlay":
                tlj.append('    tl.to("#%s-ov", { opacity: 1, y: 0, duration: 0.4, ease: "expo.out" }, %s);' % (sid, t))
                self.cue(at, "notification", 0.6, "overlay drops in"); self.cues.append((at, "%s: overlay %s" % (sid, m["src"])))
            elif k == "overlay-out":
                tlj.append('    tl.to("#%s-ov", { opacity: 0, y: -200, duration: 0.35, ease: "power2.in" }, %s);' % (sid, t))
            elif k == "progress":
                tlj.append('    gsap.set("#%s-bar", { top: %d });' % (sid, m.get("top", 1140)))
                tlj.append('    tl.fromTo("#%s-bar", { width: 0, opacity: 1 }, { width: %d, duration: %s, ease: "power1.inOut" }, %s);' % (sid, m.get("width", 660), m.get("duration", 1.5), t))
                tlj.append('    tl.to("#%s-bar", { opacity: 0, duration: 0.05 }, %s);' % (sid, t + m.get("duration", 1.5) + 0.2))
            elif k == "zoom-through":
                tlj.append('    tl.fromTo("#%s-zoom", { scale: 1, transformOrigin: "%s" }, { scale: 3.4, duration: 0.5, ease: "power3.in" }, %s);' % (sid, m.get("origin", "50% 90%"), t))
                self.cue(at, "click", 1.4, "tap (zoom-through)"); self.cues.append((at, "%s: zoom through %s" % (sid, m.get("origin"))))
            elif k == "hide":
                tlj.append('    tl.set("#%s-s%d", { opacity: 0 }, %s);' % (sid, m["screen"], t))
            else:
                sys.exit("unknown move kind %r in scene %s" % (k, sid))
        bg = "transparent" if tr == "whip-over" else c["ground"]
        self.cues.append((s["start"], "%s: %s" % (sid, screens[0])))
        return style, markup, "\n".join(tlj), bg

    def scene_endcard(self, s):
        sid = s["id"]; c = self.c; b = self.b
        style = (
            "    #%s-flood { position: absolute; inset: 0; background: %s; }\n"
            "    #%s-tile { position: absolute; left: %dpx; top: 460px; width: 300px; height: 300px; border-radius: 72px; background: #fff; display: flex; align-items: center; justify-content: center; box-shadow: 0 30px 70px rgba(0,0,0,.25); }\n"
            "    #%s-tile img { width: 268px; height: 268px; }\n"
            "    #%s-name { position: absolute; left: 0; right: 0; top: 810px; text-align: center; color: %s; font-weight: 900; font-size: 104px; letter-spacing: -.03em; }\n"
            "    #%s-claim { position: absolute; left: 50px; right: 50px; top: 960px; text-align: center; color: %s; font-weight: 600; font-size: 54px; line-height: 1.25; white-space: nowrap; }\n"
            "    .badge { position: absolute; left: %dpx; width: 500px; }\n    .badge img { width: 500px; height: auto; display: block; }\n"
        ) % (sid, c["flood"], sid, self.W // 2 - 150, sid, sid, c.get("ink_on_flood", "#ffffff"), sid, c.get("ink_on_flood", "#ffffff"), self.W // 2 - 250)
        markup = '      <div id="%s-flood"></div>\n      <div id="%s-tile"><img src="%s" alt="%s" /></div>\n      <div id="%s-name">%s</div>\n      <div id="%s-claim" data-var-text="claim">%s</div>\n' % (
            sid, sid, esc(b["logo"]), esc(b["name"]), sid, esc(b["name"]), sid, esc(b["claim"]))
        tlj = ['    tl.fromTo("#%s-flood", { clipPath: "inset(100%% 0 0 0)" }, { clipPath: "inset(0%% 0 0 0)", duration: 0.45, ease: "power2.inOut" }, 0);' % sid]
        self.cue(s["start"], "whoosh", 0.5, "flood")
        ta, na, ca = s.get("tile_at", 0.4), s.get("name_at", 0.75), s.get("claim_at", 1.75)
        tlj.append('    tl.fromTo("#%s-tile", { scale: 0, rotate: -10 }, { scale: 1, rotate: 0, duration: 0.55, ease: "back.out(1.5)" }, %s);' % (sid, ta))
        tlj.append('    tl.fromTo("#%s-name", { opacity: 0, filter: "blur(8px)" }, { opacity: 1, filter: "blur(0px)", duration: 0.25, ease: "power4.out" }, %s);' % (sid, na))
        tlj.append('    tl.fromTo("#%s-claim", { opacity: 0, filter: "blur(8px)" }, { opacity: 1, filter: "blur(0px)", duration: 0.25, ease: "power4.out" }, %s);' % (sid, ca))
        self.cue(s["start"] + ta, "impact", 0.45, "mark lands")
        for i, bd in enumerate(s.get("badges", b.get("badges", []))):
            top = 1110 + i * 190
            markup += '      <div class="badge" id="%s-badge%d" style="top: %dpx"><img src="%s" alt="store badge" /></div>\n' % (sid, i, top, esc(bd["src"]))
            tlj.append('    tl.fromTo("#%s-badge%d", { y: 200, opacity: 0 }, { y: 0, opacity: 1, duration: 0.5, ease: "expo.out" }, %s);' % (sid, i, bd["at"]))
            self.cue(s["start"] + bd["at"], "pop", 1.0, "badge")
        self.cues.append((s["start"], "end card: mark, name, claim, badges"))
        return style, markup, "\n".join(tlj), "transparent" if s.get("transition", "flood") == "flood" else c["ground"]

    # ---------- assembly ----------
    def run(self):
        sb, v = self.sb, self.v
        os.makedirs(os.path.join(self.out, "compositions"), exist_ok=True)
        hosts, z = [], 1
        for s in sb["scenes"]:
            fn = getattr(self, "scene_" + s["type"].replace("-", "_").replace("hook_cards", "hook"))
            style, markup, timeline, bg = fn(s)
            page = fill(tpl("scene.html"), font_faces=self.font_faces, root_background=bg, font_fallback=self.font_fallback,
                        id=s["id"], width=self.W, height=self.H, duration=s["duration"], style=style, markup=markup, timeline=timeline)
            with open(os.path.join(self.out, "compositions", s["id"] + ".html"), "w") as f:
                f.write(page)
            lane = 1 if s.get("transition", "cut") in ("cut", "settle") else 2
            hosts.append('      <div id="%s" data-composition-id="%s" data-composition-src="compositions/%s.html" data-track-kind="graphics" data-start="%s" data-duration="%s" data-track-index="%d" data-width="%d" data-height="%d" style="z-index: %d"></div>'
                         % (s["id"], s["id"], s["id"], s["start"], s["duration"], lane, self.W, self.H, z))
            z += 1
        # captions
        caps = sb.get("captions", [])
        cap_host = ""
        if caps:
            cs = v.get("captions", {})
            divs, groups = [], []
            for i, cp in enumerate(caps, 1):
                divs.append('    <div id="cap%d" class="clip cap" data-start="%s" data-duration="%s" data-layout-allow-caption-zone><div class="pill">%s</div></div>'
                            % (i, cp["start"], round(cp["end"] - cp["start"], 3), rich(cp["text"])))
                groups.append(["#cap%d" % i, cp["start"], cp["end"]])
            page = fill(tpl("captions.html"), font_faces=self.font_faces, font_fallback=self.font_fallback, top=cs.get("top", 1620), size=cs.get("size", 56),
                        pill=cs.get("pill", "rgba(15,15,19,.78)"), ink=self.c["ink"], highlight=self.c["highlight"], width=self.W, height=self.H,
                        caption_divs="\n".join(divs), groups=json.dumps(groups))
            with open(os.path.join(self.out, "compositions", "captions.html"), "w") as f:
                f.write(page)
            cap_host = '      <div id="captions" data-composition-id="captions" data-composition-src="compositions/captions.html" data-track-kind="captions" data-start="0" data-duration="%s" data-track-index="3" data-width="%d" data-height="%d" style="z-index: 9"></div>' % (v["duration"], self.W, self.H)
        # audio
        clips = []
        m = v.get("music")
        if m and m.get("src"):
            clips.append('      <audio id="music" class="clip" src="%s" data-start="0" data-duration="%s" data-volume="%s" data-fade-in="%s" data-fade-out="%s" data-track-index="4"></audio>'
                         % (m["src"], m.get("duration", v["duration"]), m.get("volume", 0.5), m.get("fade_in", 0.5), m.get("fade_out", 2)))
        for vo in sb.get("voice", []):
            if not vo.get("duration"):
                print("warning: voice %s has no duration — run scripts/tts.sh first" % vo["id"])
            clips.append('      <audio id="%s" class="clip" src="%s" data-start="%s" data-duration="%s" data-volume="%s" data-track-index="5"></audio>'
                         % (vo["id"], vo.get("src", "assets/audio/%s.wav" % vo["id"]), vo["start"], vo.get("duration", 3), vo.get("volume", 2.2)))
        sfx_cfg = sb.get("sfx", {})
        cues = self.sfx if sfx_cfg.get("auto", True) else []
        cues += [(x["t"], x["name"], x.get("volume", 1.0), x.get("note", "manual")) for x in sfx_cfg.get("extra", [])]
        cues.sort()
        if cues and not sfx_cfg.get("off"):
            clips.append("      <!-- UI sounds: bundled HyperFrames library (CC0); each placed so its peak lands one frame after the picture -->")
            for i, (t, name, vol, note) in enumerate(cues, 1):
                f = SFX[name]
                clips.append('      <audio id="sfx%02d" class="clip" src="%s" data-start="%s" data-duration="%s" data-volume="%s" data-track-index="%d"></audio>'
                             % (i, f["file"], round(t - f["peak"] + FRAME, 3), f["dur"], vol, 7 + i % 2))
        c = self.c
        css_vars = "\n".join("        --%s: %s;" % (k, val) for k, val in c.items()) + '\n        --font-display: %s;' % self.font_fallback
        page = fill(tpl("index.html"), resolution="portrait" if self.H > self.W else "landscape", claim=esc(self.b["claim"]), title=esc(sb.get("title", self.b["name"] + " promo")),
                    width=self.W, height=self.H, duration=v["duration"], font_faces=self.font_faces, css_vars=css_vars,
                    scene_hosts="\n".join(hosts), caption_host=cap_host, audio_clips="\n".join(clips))
        with open(os.path.join(self.out, "index.html"), "w") as f:
            f.write(page)
        # cue sheet
        rows = [(t, "SCENE  " + n) for t, n in self.cues]
        rows += [(vo["start"], "VOICE  " + vo["text"]) for vo in sb.get("voice", [])]
        rows += [(cp["start"], "CAPTION  " + cp["text"]) for cp in caps]
        rows += [(t, "SFX  %s (%s)" % (n, note)) for t, n, _, note in cues]
        rows.sort(key=lambda r: r[0])
        with open(os.path.join(self.out, "cue-sheet.md"), "w") as f:
            f.write("# Cue sheet — %s (%ss)\n\n| t | what |\n|---|---|\n" % (sb.get("title", self.b["name"]), v["duration"]))
            for t, what in rows:
                f.write("| %5.2f | %s |\n" % (t, what.replace("|", "/")))
        print("built %d scenes, %d captions, %d voice lines, %d sfx cues → %s" % (len(sb["scenes"]), len(caps), len(sb.get("voice", [])), len(cues), self.out))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("storyboard", nargs="?", default=os.path.join(ROOT, "storyboard.json"))
    ap.add_argument("--out", default=ROOT)
    a = ap.parse_args()
    with open(a.storyboard) as f:
        Build(json.load(f), a.out).run()
