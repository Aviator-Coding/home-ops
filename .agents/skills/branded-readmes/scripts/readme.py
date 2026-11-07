#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6", "fonttools>=4.50", "pillow>=10"]
# ///
"""Branded READMEs for home-ops apps, in the skrumbach design system.

Subcommands (all take an app name, ns/app, or a directory):
  list                 every Flux app and the state of its README (missing, legacy, stale, fresh)
  facts  TARGET        JSON facts read from the overlay and base manifests
  init   TARGET        create assets/readme.json (banner and concept-art settings) from the facts
  banner TARGET        render assets/banner.svg and assets/explainer.svg (infographic) from assets/readme.json
  art    TARGET [infographic|concept]  AI image through the image-generation skill:
                       assets/infographic.jpg (from explainer steps) or assets/concept.jpg (art.subject)
  sync   TARGET|--all  re-render the banner, the "At a glance" block and the freshness stamp
  index  NAMESPACE     write the namespace index README (kubernetes/apps/base/<ns>/README.md)

Text in the banner is converted to glyph outlines: GitHub serves README images with a CSP
that blocks web fonts inside SVGs. Fonts (@fontsource 5.3.0) and icons (lucide-static
0.460.0) are downloaded once into ${XDG_CACHE_HOME:-~/.cache}/branded-readmes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / "kubernetes" / "apps" / "base"
MAIN = ROOT / "kubernetes" / "apps" / "main"
SKILLS = ROOT / ".agents" / "skills"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "branded-readmes"
STAMP = "branded-readme"
GLANCE_START, GLANCE_END = "<!-- glance:start -->", "<!-- glance:end -->"
STAMP_RE = re.compile(r"<!-- branded-readme v(\d+) sources=([0-9a-f]{12}) -->")

# skrumbach design system tokens (claude.ai/design "Sascha Krumbach Design System", tokens/colors.css)
C = {
    "night950": "#03050D", "night900": "#060818", "night850": "#090C22", "night800": "#0D102C",
    "cyan": "#22D8F0", "cyan300": "#7CEFFF", "blue": "#2F9BFF", "blue2": "#3A6BFF",
    "indigo": "#6C5CFF", "violet": "#8B55F5", "violet400": "#A57BFF", "magenta": "#D946E6",
    "green": "#2EE08A", "white": "#FFFFFF", "ink100": "#E9ECF7", "ink200": "#CDD2E6",
    "ink300": "#A9AFCB", "ink400": "#7C83A5", "ink500": "#555C80",
}
FONTS = {  # family key -> (fontsource package, weight)
    "disp": ("saira", 800),
    "body": ("barlow", 400),
    "label": ("barlow-semi-condensed", 600),
    "mono": ("jetbrains-mono", 500),
}
FONT_URL = "https://cdn.jsdelivr.net/npm/@fontsource/{pkg}@5.3.0/files/{pkg}-latin-{w}-normal.woff"
ICON_URL = "https://cdn.jsdelivr.net/npm/lucide-static@0.460.0/icons/{name}.svg"

ART_STYLE = (
    "Dark, neon-lit digital illustration on a deep navy-black background (#060818). "
    "Light comes only from neon cyan (#22D8F0) and violet (#8B55F5), with magenta (#D946E6) as a "
    "sparing single accent. Motifs: fine particles, thin glowing circuit traces, hairline geometry, "
    "soft bloom. Cool palette only, no warm tones. Wide cinematic composition, subject right of "
    "center, generous dark negative space on the left third. Crisp, high contrast, clean finish. "
    "No text, no letters, no numbers, no logos, no people, no photography, no full-frame gradient wash."
)
GEN_IMAGE = SKILLS / "image-generation" / "scripts" / "generate_image.py"


def die(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def rel(p: Path) -> str:
    return str(p.resolve().relative_to(ROOT))


# ---------------------------------------------------------------- manifests


def load_docs(path: Path) -> list[dict]:
    try:
        return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    except (yaml.YAMLError, UnicodeDecodeError):
        return []


def overlay_kustomizations() -> list[tuple[Path, dict]]:
    out = []
    for f in sorted(MAIN.rglob("*.yaml")):
        for d in load_docs(f):
            if d.get("kind") == "Kustomization" and str(d.get("apiVersion", "")).startswith("kustomize.toolkit"):
                out.append((f, d))
    return out


def app_dir_of(spec_path: str) -> Path | None:
    parts = Path(spec_path.lstrip("./")).parts
    if parts[:3] != ("kubernetes", "apps", "base") or len(parts) < 5:
        return None
    ns, app = parts[3], parts[4]
    return BASE / ns if app == "app" else BASE / ns / app


def all_apps() -> dict[Path, list[tuple[Path, dict]]]:
    apps: dict[Path, list[tuple[Path, dict]]] = {}
    for f, d in overlay_kustomizations():
        a = app_dir_of(str(d.get("spec", {}).get("path", "")))
        if a and a.is_dir():
            apps.setdefault(a, []).append((f, d))
    return apps


def resolve(target: str) -> Path:
    p = (Path.cwd() / target).resolve()
    if p.is_dir():
        return p
    apps = all_apps()
    hits = [a for a in apps if a.name == target or f"{a.parent.name}/{a.name}" == target]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        die(f"no app or directory named {target!r} (try `list`)")
    die(f"{target!r} is ambiguous: " + ", ".join(rel(h) for h in hits) + "; use ns/app")
    raise AssertionError


def readme_path(d: Path) -> Path:
    """An existing README stays where it is (app/README.md included); new ones go in the app dir."""
    for cand in (d / "README.md", d / "Readme.md", d / "app" / "README.md"):
        if cand.exists():
            return cand
    return d / "README.md"


def walk(o, fn):
    if isinstance(o, dict):
        fn(o)
        for v in o.values():
            walk(v, fn)
    elif isinstance(o, list):
        for v in o:
            walk(v, fn)


VERSION_KEYS = {"tag", "version", "digest", "ref", "semver"}


def strip_versions(o):
    if isinstance(o, dict):
        return {k: strip_versions(v) for k, v in o.items() if k not in VERSION_KEYS}
    if isinstance(o, list):
        return [strip_versions(v) for v in o]
    if isinstance(o, str) and re.search(r"[@:](sha256:|v?\d+\.\d+)", o):
        return re.sub(r"([@:])(sha256:[0-9a-f]+|v?\d[\w.+-]*)", "", o)
    return o


def facts(d: Path) -> dict:
    apps = all_apps()
    overlays = apps.get(d, [])
    f: dict = {"dir": rel(d), "name": d.name, "namespace": None, "kustomizations": [], "inventory": {},
               "helm": [], "images": [], "routes": [], "secrets": [], "pushsecrets": [], "pvcs": [],
               "alerts": [], "needed_by": [], "values": {}}
    names = set()
    for of, k in overlays:
        md, sp = k.get("metadata", {}), k.get("spec", {})
        ns = sp.get("targetNamespace") or md.get("namespace")
        f["namespace"] = f["namespace"] or ns
        names.add((md.get("name"), md.get("namespace")))
        comps = []
        kz = of.parent / "kustomization.yaml"
        while not kz.exists() and kz.parent.parent != kz.parent:  # grouped overlays: main/<ns>/<group>/<app>.yaml
            kz = kz.parent.parent / "kustomization.yaml"
        for doc in load_docs(kz):
            comps += [Path(c).name for c in doc.get("components", [])]
        f["kustomizations"].append({
            "name": md.get("name"), "file": rel(of), "path": sp.get("path"),
            "dependsOn": [f"{x.get('namespace', md.get('namespace'))}/{x['name']}" for x in sp.get("dependsOn", [])],
            "healthChecks": [f"{h.get('kind')}/{h.get('name')}" for h in sp.get("healthChecks", [])],
            "components": sorted(set(comps + [Path(c).name for c in sp.get("components", [])])),
            "wait": sp.get("wait"), "substitute": sorted((sp.get("postBuild", {}).get("substitute") or {}).keys()),
        })
    for of, k in overlay_kustomizations():
        for x in k.get("spec", {}).get("dependsOn", []):
            key = (x.get("name"), x.get("namespace", k["metadata"].get("namespace")))
            if key in names:
                f["needed_by"].append(f"{k['metadata'].get('namespace')}/{k['metadata']['name']}")
    f["needed_by"] = sorted(set(f["needed_by"]))

    ocirepos = {}
    docs = []
    for y in sorted(d.rglob("*.yaml")):
        if "assets" in y.parts:
            continue
        for doc in load_docs(y):
            docs.append((y, doc))
            if doc.get("kind") == "OCIRepository":
                ocirepos[doc["metadata"]["name"]] = doc["spec"].get("url")
    for y, doc in docs:
        kind, md, sp = doc.get("kind"), doc.get("metadata") or {}, doc.get("spec") or {}
        if not kind or str(doc.get("apiVersion", "")).startswith("kustomize.config"):
            continue
        f["inventory"].setdefault(kind, []).append(md.get("name"))
        if kind == "HelmRelease":
            cr, ch = sp.get("chartRef") or {}, (sp.get("chart") or {}).get("spec") or {}
            src = ocirepos.get(cr.get("name")) if cr else (ch.get("sourceRef") or {}).get("name")
            f["helm"].append({"name": md.get("name"), "file": rel(y), "chart": ch.get("chart") or cr.get("name"),
                              "source": src})
            f["values"][md.get("name")] = strip_versions(sp.get("values") or {})

            def img(o):
                r = o.get("repository")
                if isinstance(r, str) and ("/" in r or "." in r) and not r.startswith("http"):
                    f["images"].append(r)
            walk(sp.get("values") or {}, img)
        if kind in ("Deployment", "StatefulSet", "DaemonSet", "CronJob", "Job", "Pod"):
            walk(sp, lambda o: f["images"].append(strip_versions(o["image"])) if isinstance(o.get("image"), str) else None)
        if kind == "HTTPRoute":
            ann = md.get("annotations") or {}
            f["routes"].append({"name": md.get("name"), "hostnames": sp.get("hostnames", []),
                                "gateways": [p.get("name") for p in sp.get("parentRefs", [])],
                                "gatus": "gatus.home-operations.com/endpoint" in ann})
        if kind == "ExternalSecret":
            keys = [x["extract"]["key"] for x in sp.get("dataFrom", []) if "extract" in x]
            keys += [x["remoteRef"]["key"] for x in sp.get("data", []) if "remoteRef" in x]
            sec = {"name": md.get("name"), "target": (sp.get("target") or {}).get("name", md.get("name")),
                   "store": (sp.get("secretStoreRef") or {}).get("name"), "items": sorted(set(keys))}
            gens = [f"{g.get('kind')}/{g.get('name')}" for x in sp.get("dataFrom", [])
                    if (g := (x.get("sourceRef") or {}).get("generatorRef"))]
            if gens:  # only set when present, so other apps' source hashes stay unchanged
                sec["generators"] = gens
            f["secrets"].append(sec)
        if kind == "PushSecret":
            f["pushsecrets"].append({"name": md.get("name"), "items": sorted({x["match"]["remoteRef"]["remoteKey"]
                                     for x in sp.get("data", []) if "match" in x})})
        if kind == "PersistentVolumeClaim":
            f["pvcs"].append({"name": md.get("name"), "storageClass": sp.get("storageClassName"),
                              "size": ((sp.get("resources") or {}).get("requests") or {}).get("storage")})
        if kind == "PrometheusRule":
            for g in sp.get("groups", []):
                f["alerts"] += [r["alert"] for r in g.get("rules", []) if "alert" in r]
    f["images"] = sorted(set(f["images"]))
    f["kopiur"] = any("kopiur" in c for k in f["kustomizations"] for c in k["components"])
    return f


def source_hash(f: dict) -> str:
    return hashlib.sha256(json.dumps(f, sort_keys=True, default=str).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- glance block


def link(text: str, target: str, readme: Path) -> str:
    return f"[`{text}`]({os.path.relpath(ROOT / target, readme.parent)})"


def glance(f: dict, readme: Path) -> str:
    rows = []
    if f["namespace"]:
        rows.append(("Namespace", f"`{f['namespace']}`"))
    if f["kustomizations"]:
        rows.append(("Deployed by", ", ".join(link(k["name"], k["file"], readme) for k in f["kustomizations"])
                     + " (Flux Kustomization)"))
    for h in f["helm"]:
        src = f" from `{h['source']}`" if h["source"] else ""
        chart = h["source"].rstrip("/").rsplit("/", 1)[-1] if str(h["source"]).startswith("oci://") else h["chart"]
        rows.append(("Helm chart", f"`{chart}`{src}, values in {link(Path(h['file']).name, h['file'], readme)}"))
    if f["images"]:
        rows.append(("Images", ", ".join(f"`{i}`" for i in f["images"][:6]) + (" …" if len(f["images"]) > 6 else "")))
    deps = sorted({x for k in f["kustomizations"] for x in k["dependsOn"]})
    if deps:
        rows.append(("Depends on", ", ".join(f"`{x}`" for x in deps)))
    if f["needed_by"]:
        nb, more = f["needed_by"][:12], len(f["needed_by"]) - 12
        rows.append(("Needed by", ", ".join(f"`{x}`" for x in nb) + (f" and {more} more" if more > 0 else "")))
    for r in f["routes"]:
        hosts = ", ".join(f"`{h}`" for h in r["hostnames"]) or "`(no hostname)`"
        name = f"`{r['name']}`: " if len(f["routes"]) > 1 else ""
        rows.append(("Route", f"{name}{hosts} via `{'`, `'.join(r['gateways'])}`" + (" (Gatus probed)" if r["gatus"] else "")))
    for s in f["secrets"]:
        items = ", ".join(f"`{i}`" for i in s["items"])
        gens = ", ".join(f"`{g}`" for g in s.get("generators", []))
        if s["store"] and items:
            rows.append(("Secret", f"`{s['target']}` from {s['store']} item {items}" + (f", plus generator {gens}" if gens else "")))
        elif gens:
            rows.append(("Secret", f"`{s['target']}` generated in-cluster by {gens}"))
        else:
            rows.append(("Secret", f"`{s['target']}`" + (f" from {s['store']}" if s["store"] else "")))
    for s in f["pushsecrets"]:
        rows.append(("Pushes to 1Password", ", ".join(f"`{i}`" for i in s["items"])))
    for p in f["pvcs"]:
        rows.append(("Volume", f"`{p['name']}` {p['size'] or ''} on `{p['storageClass']}`".replace("  ", " ")))
    if f["kopiur"]:
        rows.append(("Backup", "kopiur component on the overlay (skill `kopiur-backups`)"))
    checks = sorted({x for k in f["kustomizations"] for x in k["healthChecks"]})
    if checks:
        rows.append(("Health checks", ", ".join(f"`{x}`" for x in checks)))
    if f["alerts"]:
        rows.append(("Alerts", f"{len(f['alerts'])}: " + ", ".join(f"`{a}`" for a in f["alerts"])))
    body = "| | |\n|---|---|\n" + "\n".join(f"| **{k}** | {v} |" for k, v in rows)
    return f"{GLANCE_START}\n<!-- Generated by `readme.py sync`; edit the manifests, not this table. -->\n{body}\n{GLANCE_END}"


# ---------------------------------------------------------------- fonts and SVG text


def fetch(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = r.read()
        except OSError as e:
            die(f"download failed: {url}: {e}", 4)
        dest.write_bytes(data)
    return dest


_fonts: dict = {}


def font(fam: str):
    from fontTools.ttLib import TTFont

    if fam not in _fonts:
        pkg, w = FONTS[fam]
        p = fetch(FONT_URL.format(pkg=pkg, w=w), CACHE / "fonts" / f"{pkg}-{w}.woff")
        tt = TTFont(p)
        _fonts[fam] = (tt, tt.getGlyphSet(), tt.getBestCmap(), tt["head"].unitsPerEm, tt["hmtx"])
    return _fonts[fam]


def _glyph(fam: str, ch: str):
    for f in [fam] + [k for k in FONTS if k != fam]:
        tt, gs, cmap, upem, hmtx = font(f)
        if ord(ch) in cmap:
            return f, cmap[ord(ch)]
    die(f"glyph {ch!r} is in none of the brand fonts; rephrase the text")
    raise AssertionError


def measure(s: str, fam: str, size: float, ls: float = 0) -> float:
    w = 0.0
    for ch in s:
        f, g = _glyph(fam, ch)
        _, _, _, upem, hmtx = font(f)
        w += hmtx[g][0] * size / upem + ls * size
    return w - (ls * size if s else 0)


_glyph_defs: dict[tuple[str, str], str] = {}


def glyph_defs() -> str:
    """Each glyph outline once, in font units; text_path places them with <use>. Call after drawing."""
    out = "<defs>" + "".join(_glyph_defs.values()) + "</defs>"
    _glyph_defs.clear()
    return out


def _glyph_id(f: str, g: str) -> str:
    from fontTools.pens.svgPathPen import SVGPathPen

    key = (f, g)
    if key not in _glyph_defs:
        gid = f"glyph-{len(_glyph_defs):x}"
        pen = SVGPathPen(font(f)[1], ntos=lambda v: str(round(v)))
        font(f)[1][g].draw(pen)
        _glyph_defs[key] = f'<path id="{gid}" d="{pen.getCommands()}"/>' if pen.getCommands() else f'<g id="{gid}"/>'
    return re.search(r'id="([\w-]+)"', _glyph_defs[key]).group(1)


def text_path(x: float, y: float, s: str, fam: str, size: float, fill: str, ls: float = 0,
              anchor: str = "start", extra: str = "") -> str:
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen

    if anchor != "start":
        w = measure(s, fam, size, ls)
        x -= w if anchor == "end" else w / 2
    if fill.startswith("url("):  # gradients need one outline in user space, so no <use>
        ds = []
        for ch in s:
            f, g = _glyph(fam, ch)
            _, gs, _, upem, hmtx = font(f)
            k = size / upem
            pen = SVGPathPen(gs, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
            gs[g].draw(TransformPen(pen, (k, 0, 0, -k, x, y)))
            ds.append(pen.getCommands())
            x += hmtx[g][0] * k + ls * size
        return f'<path d="{"".join(ds)}" fill="{fill}"{extra}/>'
    uses = []
    for ch in s:
        f, g = _glyph(fam, ch)
        _, _, _, upem, hmtx = font(f)
        k = size / upem
        if ch.strip():
            uses.append(f'<use href="#{_glyph_id(f, g)}" transform="translate({x:.1f} {y:.1f}) scale({k:.5f} {-k:.5f})"/>')
        x += hmtx[g][0] * k + ls * size
    return f'<g fill="{fill}"{extra}>{"".join(uses)}</g>'


def fit_size(s: str, fam: str, size: float, maxw: float, ls: float = 0) -> float:
    while size > 10 and measure(s, fam, size, ls) > maxw:
        size -= 1
    return size


def wrap(s: str, fam: str, size: float, maxw: float) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        t = f"{cur} {word}".strip()
        if cur and measure(t, fam, size) > maxw:
            lines.append(cur)
            cur = word
        else:
            cur = t
    return lines + ([cur] if cur else [])


def icon_svg(name: str, x: float, y: float, size: float) -> str:
    p = fetch(ICON_URL.format(name=name), CACHE / "icons" / f"{name}.svg")
    root = ET.fromstring(re.sub(r"<!--.*?-->", "", p.read_text(), flags=re.S))
    inner = ""
    for el in root:
        tag = el.tag.split("}")[-1]
        attrs = " ".join(f'{k}="{v}"' for k, v in el.attrib.items())
        inner += f"<{tag} {attrs}/>"
    k = size / 24
    return (f'<g transform="translate({x:.1f} {y:.1f}) scale({k:.3f})" fill="none" stroke="url(#neon)" '
            f'stroke-width="{2.4 / k:.3f}" stroke-linecap="round" stroke-linejoin="round" '
            f'filter="url(#glow)">{inner}</g>')


def traces(seed: str, x0: float, x1: float, h: float) -> str:
    rnd = random.Random(seed)
    out = []
    for i in range(11):
        x, y = rnd.uniform(x0, x1), rnd.choice([0, h]) + rnd.uniform(-4, 4)
        pts = [(x, y)]
        for _ in range(rnd.randint(2, 4)):
            if len(pts) % 2:
                y = min(h - 14, max(14, y + rnd.choice([-1, 1]) * rnd.uniform(30, 110)))
            else:
                x = min(x1, max(x0, x + rnd.choice([-1, 1]) * rnd.uniform(40, 160)))
            pts.append((x, y))
        col, op = (C["cyan"], 0.30) if i % 4 == 0 else (C["violet400"], 0.16)
        pl = " ".join(f"{a:.0f},{b:.0f}" for a, b in pts)
        ex, ey = pts[-1]
        out.append(f'<polyline points="{pl}" fill="none" stroke="{col}" stroke-opacity="{op}" stroke-width="1.2"/>'
                   f'<circle cx="{ex:.0f}" cy="{ey:.0f}" r="2.6" fill="{col}" fill-opacity="{op * 2:.2f}"/>')
    return "".join(out)


def banner(cfg: dict, seed: str) -> str:
    W, X = 1200, 56
    b = cfg["banner"]
    title = b["title"].upper()
    tsize = fit_size(title, "disp", 76, 740, 0.01)
    eyebrow = b.get("eyebrow", "").upper()
    chips = [c.upper() for c in b.get("chips", [])][:4]
    tag_lines = wrap(b.get("tagline", ""), "body", 21, 700)[:2]
    status = b.get("status", "").upper()
    # Height follows the content: eyebrow, title, 1-2 tagline lines, chip row, then the status line below it.
    content_bottom = 74 + 84 + 40 + 28 * len(tag_lines) + 8 + (28 if chips else -8)
    H = max(220, round(content_bottom + 44))

    defs = f"""<defs>
<radialGradient id="gv" cx="86%" cy="0%" r="72%"><stop offset="0" stop-color="{C['violet']}" stop-opacity=".34"/><stop offset="1" stop-color="{C['violet']}" stop-opacity="0"/></radialGradient>
<radialGradient id="gc" cx="0%" cy="100%" r="62%"><stop offset="0" stop-color="{C['cyan']}" stop-opacity=".14"/><stop offset="1" stop-color="{C['cyan']}" stop-opacity="0"/></radialGradient>
<radialGradient id="halo" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="{C['cyan']}" stop-opacity=".16"/><stop offset=".6" stop-color="{C['violet']}" stop-opacity=".08"/><stop offset="1" stop-color="{C['violet']}" stop-opacity="0"/></radialGradient>
<linearGradient id="word" gradientUnits="userSpaceOnUse" x1="{X}" x2="{X + measure(title, 'disp', tsize, 0.01):.0f}"><stop offset="0" stop-color="{C['cyan']}"/><stop offset=".55" stop-color="{C['blue']}"/><stop offset="1" stop-color="{C['blue2']}"/></linearGradient>
<linearGradient id="neon" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{C['cyan']}"/><stop offset=".5" stop-color="{C['indigo']}"/><stop offset="1" stop-color="{C['magenta']}"/></linearGradient>
<linearGradient id="hair" x1="0" x2="1"><stop offset="0" stop-color="{C['cyan']}" stop-opacity=".7"/><stop offset=".5" stop-color="{C['violet']}" stop-opacity=".55"/><stop offset="1" stop-color="{C['magenta']}" stop-opacity=".7"/></linearGradient>
<filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<clipPath id="card"><rect width="{W}" height="{H}" rx="6"/></clipPath>
</defs>
<style>.pulse{{animation:p 1.8s ease-in-out infinite}}@keyframes p{{50%{{opacity:.25}}}}@media (prefers-reduced-motion:reduce){{.pulse{{animation:none}}}}</style>"""
    out = [f'<rect width="{W}" height="{H}" rx="6" fill="{C["night900"]}"/>',
           f'<g clip-path="url(#card)"><rect width="{W}" height="{H}" fill="url(#gv)"/><rect width="{W}" height="{H}" fill="url(#gc)"/>',
           traces(seed, 640, W - 20, H), "</g>"]
    if b.get("icon"):
        out.append(f'<circle cx="1000" cy="{H / 2:.1f}" r="{min(128, H / 2 - 12):.1f}" fill="url(#halo)"/>')
        isz = min(150, H - 120)
        out.append(icon_svg(b["icon"], 1000 - isz / 2, H / 2 - isz / 2, isz))
    y = 74
    if eyebrow:
        out.append(text_path(X, y, eyebrow, "label", 15, C["cyan"], 0.24))
    y += 84
    out.append(text_path(X - 3, y, title, "disp", tsize, "url(#word)", 0.01))
    y += 40
    for line in tag_lines:
        out.append(text_path(X, y, line, "body", 21, C["ink200"]))
        y += 28
    y += 8
    cx = X
    for i, c in enumerate(chips):
        w = measure(c, "mono", 12.5, 0.08) + 28
        out.append(f'<rect x="{cx:.1f}" y="{y:.1f}" width="{w:.1f}" height="28" rx="3" fill="{C["night950"]}" '
                   f'fill-opacity=".6" stroke="{C["violet400"]}" stroke-opacity=".38"/>')
        out.append(text_path(cx + 14, y + 18.5, c, "mono", 12.5, C["cyan"] if i == 0 else C["ink200"], 0.08))
        cx += w + 10
    if status:
        sw = measure(status, "mono", 11.5, 0.1)
        sx = W - 36 - sw
        out.append(f'<circle class="pulse" cx="{sx - 14:.1f}" cy="{H - 32:.1f}" r="4" fill="{C["green"]}" filter="url(#glow)"/>')
        out.append(text_path(sx, H - 28, status, "mono", 11.5, C["ink300"], 0.1))
    out.append(f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="6" fill="none" stroke="url(#hair)"/>')
    alt = f"{b['title']}: {b.get('tagline', '')}".replace('"', "'").replace("&", "and")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'role="img" aria-label="{alt}">\n<title>{alt}</title>\n{defs}\n' + "\n".join(out) + "\n" + glyph_defs() + "\n</svg>\n")


def explainer(cfg: dict) -> str:
    """Infographic: numbered steps (icon, title, explanation) joined by arrows, then a row of key numbers."""
    e = cfg["explainer"]
    steps, stats = e["steps"], e.get("facts", [])
    W, M, GAP = 1200, 36, 26
    n = len(steps)
    cw = (W - 2 * M - GAP * (n - 1)) / n
    body = {i: wrap(s["text"], "body", 15.5, cw - 36) for i, s in enumerate(steps)}
    titles = {i: wrap(s["title"].upper(), "label", 15, cw - 36) for i, s in enumerate(steps)}
    top = 128
    # Card height = last body baseline + descender + bottom padding (matches the y walk below).
    ch = 110 + 20 * max(len(t) for t in titles.values()) + 22 * max(len(b) for b in body.values()) + 22
    stat_lines = max((len(wrap(st["label"].upper(), "label", 12.5,
                               (W - 2 * M - 16 * (len(stats) - 1)) / len(stats) - 40)[:2]) for st in stats), default=0)
    th = 68 + 15 * (stat_lines - 1) + 20 if stats else 0
    sy = top + ch + 24
    H = (sy + th if stats else top + ch) + 32

    defs = f"""<defs>
<radialGradient id="gv" cx="90%" cy="0%" r="70%"><stop offset="0" stop-color="{C['violet']}" stop-opacity=".26"/><stop offset="1" stop-color="{C['violet']}" stop-opacity="0"/></radialGradient>
<radialGradient id="gc" cx="0%" cy="100%" r="60%"><stop offset="0" stop-color="{C['cyan']}" stop-opacity=".10"/><stop offset="1" stop-color="{C['cyan']}" stop-opacity="0"/></radialGradient>
<linearGradient id="neon" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{C['cyan']}"/><stop offset=".5" stop-color="{C['indigo']}"/><stop offset="1" stop-color="{C['magenta']}"/></linearGradient>
<linearGradient id="hair" x1="0" x2="1"><stop offset="0" stop-color="{C['cyan']}" stop-opacity=".7"/><stop offset=".5" stop-color="{C['violet']}" stop-opacity=".55"/><stop offset="1" stop-color="{C['magenta']}" stop-opacity=".7"/></linearGradient>
<linearGradient id="cardb" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{C['cyan']}" stop-opacity=".55"/><stop offset=".5" stop-color="{C['violet']}" stop-opacity=".40"/><stop offset="1" stop-color="{C['magenta']}" stop-opacity=".55"/></linearGradient>
<linearGradient id="word" x1="0" x2="1"><stop offset="0" stop-color="{C['cyan']}"/><stop offset=".55" stop-color="{C['blue']}"/><stop offset="1" stop-color="{C['blue2']}"/></linearGradient>
<filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
</defs>"""
    out = [f'<rect width="{W}" height="{H}" rx="6" fill="{C["night900"]}"/>',
           f'<rect width="{W}" height="{H}" rx="6" fill="url(#gv)"/><rect width="{W}" height="{H}" rx="6" fill="url(#gc)"/>',
           text_path(M, 54, e.get("eyebrow", "HOW IT WORKS").upper(), "label", 14, C["cyan"], 0.24),
           text_path(M - 1, 94, e["title"], "disp", fit_size(e["title"], "disp", 32, W - 2 * M), C["white"], 0.01)]
    for i, s in enumerate(steps):
        x = M + i * (cw + GAP)
        out.append(f'<rect x="{x:.1f}" y="{top}" width="{cw:.1f}" height="{ch}" rx="6" fill="{C["night850"]}" stroke="url(#cardb)"/>')
        out.append(text_path(x + 18, top + 34, f"{i + 1:02d}", "mono", 14, C["magenta"], 0.1))
        out.append(f'<circle cx="{x + cw - 46:.1f}" cy="{top + 46}" r="30" fill="{C["cyan"]}" fill-opacity=".06" '
                   f'stroke="{C["violet400"]}" stroke-opacity=".25"/>')
        out.append(icon_svg(s["icon"], x + cw - 46 - 18, top + 46 - 18, 36))
        y = top + 110
        for t in titles[i]:
            out.append(text_path(x + 18, y, t, "label", 15, C["white"], 0.12))
            y += 20
        out.append(f'<rect x="{x + 18:.1f}" y="{y - 6}" width="28" height="2" fill="{C["cyan"]}"/>')
        y += 22
        for line in body[i]:
            out.append(text_path(x + 18, y, line, "body", 15.5, C["ink200"]))
            y += 22
        if i < n - 1:  # arrow into the next card
            ax, ay = x + cw + 3, top + ch / 2
            out.append(f'<path d="M{ax:.1f} {ay - 8:.1f}L{ax + GAP - 8:.1f} {ay:.1f}L{ax:.1f} {ay + 8:.1f}" fill="none" '
                       f'stroke="{C["cyan"]}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" filter="url(#glow)"/>')
    if stats:
        sw = (W - 2 * M - 16 * (len(stats) - 1)) / len(stats)
        for i, st in enumerate(stats):
            x = M + i * (sw + 16)
            out.append(f'<rect x="{x:.1f}" y="{sy}" width="{sw:.1f}" height="{th}" rx="6" fill="{C["night950"]}" '
                       f'fill-opacity=".7" stroke="{C["violet400"]}" stroke-opacity=".38"/>')
            out.append(text_path(x + 20, sy + 46, st["value"].upper(), "disp", 30, C["cyan"], 0.01))
            for j, line in enumerate(wrap(st["label"].upper(), "label", 12.5, sw - 40)[:2]):
                out.append(text_path(x + 20, sy + 68 + j * 15, line, "label", 12.5, C["ink300"], 0.14))
    out.append(f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="6" fill="none" stroke="url(#hair)"/>')
    alt = (e["title"] + ": " + " ".join(f"{i + 1}. {s['title']}: {s['text']}" for i, s in enumerate(steps)))
    alt = alt.replace('"', "'").replace("&", "and").replace("<", "").replace(">", "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'role="img" aria-label="{alt}">\n<title>{alt}</title>\n{defs}\n' + "\n".join(out) + "\n" + glyph_defs() + "\n</svg>\n")


# ---------------------------------------------------------------- config, art, readme


def cfg_path(d: Path) -> Path:
    return readme_path(d).parent / "assets" / "readme.json"


def load_cfg(d: Path) -> dict:
    p = cfg_path(d)
    if not p.exists():
        die(f"{rel(p)} is missing; run `init` first")
    return json.loads(p.read_text())


def write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n")


def cmd_init(d: Path, force: bool) -> None:
    p = cfg_path(d)
    if p.exists() and not force:
        die(f"{rel(p)} exists; edit it, or pass --force")
    f = facts(d)
    chips = []
    for h in f["helm"]:
        chips.append("HELM • OCI" if str(h["source"] or "").startswith("oci://") else "HELM")
    if f["routes"]:
        chips.append("HTTPROUTE")
    if f["secrets"]:
        chips.append("1PASSWORD")
    if f["kopiur"]:
        chips.append("KOPIUR BACKUP")
    write_json(p, {
        "banner": {
            "eyebrow": f"HOME-OPS • {f['namespace'] or d.parent.name}",
            "title": d.name,
            "tagline": "One sentence: what this app does for the cluster.",
            "chips": chips[:4] or ["FLUX"],
            "icon": "box",
            "status": f"RECONCILED BY FLUX • {f['namespace'] or d.parent.name}/{d.name}",
        },
        "art": {"subject": "", "model": "gpt", "quality": "medium"},
    })
    print(rel(p))


def cmd_banner(d: Path) -> Path:
    out = cfg_path(d).parent / "banner.svg"
    out.write_text(banner(load_cfg(d), d.name))
    return out


def cmd_explainer(d: Path) -> Path | None:
    cfg = load_cfg(d)
    if not cfg.get("explainer", {}).get("steps"):
        return None
    out = cfg_path(d).parent / "explainer.svg"
    out.write_text(explainer(cfg))
    return out


def infographic_prompt(cfg: dict) -> str:
    e = cfg["explainer"]
    missing = [s["title"] for s in e["steps"] if not s.get("scene")]
    if missing:
        die("every explainer step needs a `scene` (what its station shows) for the infographic: " + ", ".join(missing))
    n = len(e["steps"])
    stations = ", ".join(f'"{i + 1} {s["title"].upper()}" {s["scene"].rstrip(".")}' for i, s in enumerate(e["steps"]))
    title = cfg.get("art", {}).get("title") or e["title"]
    about = cfg.get("art", {}).get("about") or e["title"].rstrip(".").lower()
    return (
        f"Isometric illustrated infographic explaining {about}, {n} stations connected left "
        "to right by one glowing cyan data path. Each station is a small glowing platform with an illustration and a "
        f"bold label above it in a thin geometric uppercase sans: {stations}. Title at top left: \"{title.upper()}\". "
        "Dark navy-black background (#060818), neon cyan (#22D8F0) and violet (#8B55F5) light, magenta (#D946E6) on "
        "one station only. Thin circuit traces, soft bloom, crisp clean vector-render finish, generous spacing, every "
        "label spelled exactly as given. No other text, no logos, no people, no warm tones."
    )


def trim_dark(im, threshold: int = 60, pad: int = 36):
    """Crop empty dark bands above and below the content (models leave a lot of night sky)."""
    gray = im.convert("L").point(lambda v: 255 if v > threshold else 0)
    box = gray.getbbox()
    if not box:
        return im
    top, bottom = max(0, box[1] - pad), min(im.size[1], box[3] + pad)
    return im.crop((0, top, im.size[0], bottom))


def run_generate(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 3 and os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
        # A service-account token hides the personal vaults; retry with the signed-in op session.
        env = {k: v for k, v in os.environ.items() if k != "OP_SERVICE_ACCOUNT_TOKEN"}
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return r


def cmd_art(d: Path, kind: str, model: str | None, quality: str | None) -> None:
    from PIL import Image

    cfg = load_cfg(d)
    a = cfg.get("art", {})
    if kind == "infographic":
        if not cfg.get("explainer", {}).get("steps"):
            die("the infographic is drawn from explainer.steps; fill those first")
        prompt, out, q = infographic_prompt(cfg), "infographic.jpg", quality or "high"
    else:
        if not a.get("subject"):
            die("set art.subject in assets/readme.json first (one sentence: the concept, as a scene)")
        prompt, out, q = f"{a['subject'].rstrip('.')}. {ART_STYLE}", "concept.jpg", quality or a.get("quality", "medium")
    model = model or a.get("model", "gpt")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.png"
        cmd = [str(GEN_IMAGE), prompt, "-m", model, "-o", str(raw)]
        if model == "gpt":
            cmd += ["-s", "1536x1024", "-q", q]
        r = run_generate(cmd)
        if r.returncode:
            sys.stderr.write(r.stderr)
            sys.exit(r.returncode)
        im = Image.open(raw).convert("RGB")
        w, h = im.size
        if kind == "concept":  # 2:1 crop keeps the README scannable; infographics keep their full frame
            ch = round(w / 2)
            top = max(0, (h - ch) // 2)
            im = im.crop((0, top, w, min(h, top + ch)))
        im = trim_dark(im) if kind == "infographic" else im
        im = im.resize((1400, round(1400 * im.size[1] / im.size[0])), Image.LANCZOS)
        dest = cfg_path(d).parent / out
        im.save(dest, "JPEG", quality=84, optimize=True, progressive=True)
    meta = json.loads(r.stdout or "{}")
    print(json.dumps({"file": rel(dest), "bytes": dest.stat().st_size, "model": meta.get("model"),
                      "cost_usd": meta.get("cost_usd")}))
    print("Read the image and check every label's spelling before using it.", file=sys.stderr)


def cmd_sync(d: Path) -> str:
    readme = readme_path(d)
    if not readme.exists():
        die(f"{rel(readme)} does not exist; write it from references/readme-template.md first")
    f = facts(d)
    text = readme.read_text()
    if cfg_path(d).exists():
        cmd_banner(d)
        cmd_explainer(d)
    if GLANCE_START in text and GLANCE_END in text:
        s, e = text.index(GLANCE_START), text.index(GLANCE_END) + len(GLANCE_END)
        text = text[:s] + glance(f, readme) + text[e:]
    stamp = f"<!-- {STAMP} v1 sources={source_hash(f)} -->"
    text = STAMP_RE.sub(stamp, text) if STAMP_RE.search(text) else text.rstrip("\n") + f"\n\n{stamp}\n"
    readme.write_text(text)
    return rel(readme)


def state(d: Path) -> str:
    readme = readme_path(d)
    if not readme.exists():
        return "missing"
    m = STAMP_RE.search(readme.read_text())
    if not m:
        return "legacy"
    return "fresh" if m.group(2) == source_hash(facts(d)) else "stale"


def cmd_list(only: str | None) -> None:
    counts: dict[str, int] = {}
    for d in sorted(all_apps()):
        s = state(d)
        counts[s] = counts.get(s, 0) + 1
        if only and s != only:
            continue
        print(f"{s:8} {rel(d)}")
    print("# " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())), file=sys.stderr)


def cmd_index(ns: str) -> None:
    nsdir = BASE / ns
    if not nsdir.is_dir():
        die(f"no namespace dir {rel(nsdir)}")
    apps = sorted(a for a in all_apps() if a == nsdir or a.parent == nsdir)
    readme = nsdir / "README.md"
    if readme.exists() and STAMP_RE.search(readme.read_text()) is None and "<!-- branded-index -->" not in readme.read_text():
        die(f"{rel(readme)} is hand-written; fold the app table into it by hand")
    rows = []
    for a in apps:
        r = readme_path(a)
        tagline = "—"
        if cfg_path(a).exists():
            tagline = load_cfg(a)["banner"].get("tagline", "—")
        name = f"[{a.name}]({os.path.relpath(r, nsdir)})" if r.exists() else a.name
        rows.append(f"| {name} | {tagline} | {state(a)} |")
    body = ("<!-- branded-index -->\n"
            f"# {ns}\n\nApps in namespace `{ns}`, one Flux Kustomization each "
            f"(overlay: [`kubernetes/apps/main/{ns}/`]({os.path.relpath(MAIN / ns, nsdir)})).\n\n"
            "| App | What it does | README |\n|---|---|---|\n" + "\n".join(rows) + "\n\n"
            f"Regenerate with `.agents/skills/branded-readmes/scripts/readme.py index {ns}`.\n")
    readme.write_text(body)
    print(rel(readme))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("--only", choices=["missing", "legacy", "stale", "fresh"])
    for name in ("facts", "banner"):
        sub.add_parser(name).add_argument("target")
    s = sub.add_parser("init")
    s.add_argument("target")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("art")
    s.add_argument("target")
    s.add_argument("kind", nargs="?", choices=["infographic", "concept"], default="infographic")
    s.add_argument("-m", "--model", choices=["gpt", "gemini", "grok"])
    s.add_argument("-q", "--quality", choices=["low", "medium", "high"])
    s = sub.add_parser("sync")
    s.add_argument("target", nargs="?")
    s.add_argument("--all", action="store_true", help="every README that carries a stamp")
    sub.add_parser("index").add_argument("namespace")
    a = ap.parse_args()

    if a.cmd == "list":
        cmd_list(a.only)
    elif a.cmd == "facts":
        print(json.dumps(facts(resolve(a.target)), indent=2, default=str))
    elif a.cmd == "init":
        cmd_init(resolve(a.target), a.force)
    elif a.cmd == "banner":
        d = resolve(a.target)
        print(rel(cmd_banner(d)))
        ex = cmd_explainer(d)
        if ex:
            print(rel(ex))
    elif a.cmd == "art":
        cmd_art(resolve(a.target), a.kind, a.model, a.quality)
    elif a.cmd == "sync":
        if a.all:
            for d in sorted(all_apps()):
                if state(d) in ("stale", "fresh"):
                    print(cmd_sync(d))
        elif a.target:
            print(cmd_sync(resolve(a.target)))
        else:
            die("sync needs a target or --all")
    elif a.cmd == "index":
        cmd_index(a.namespace)


if __name__ == "__main__":
    main()
