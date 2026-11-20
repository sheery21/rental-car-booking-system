"""Generate docs/project_structure.pdf from the project's own source code.

The script reads the real files (Python `ast` for the backend, a regex for the
JavaScript classes, the folder tree from disk) and draws:

  1. Overview      – folder structure, how to run, pages, API endpoints
  2. Architecture  – layers and how a request travels through them
  3. Class diagram – domain models (Vehicle, Customer, Booking, Payment ...)
  4. Class diagram – HTTP / API layer and exceptions
  5. Class diagram – services (RentalService, AuthService) and storage
  6. Database      – SQLite tables, read from the live schema
  7. Sequence      – what happens when a customer books a car
  8. OOP concepts  – where each concept is used

Run from the project folder:   python docs/generate_diagram.py
Needs: reportlab  (pip install reportlab)
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "project_structure.pdf"
W, H = landscape(A3)
M = 36  # page margin
TOTAL_PAGES = 8

# ---------------------------------------------------------------- fonts
FONT_FILES = {
    "Sans": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
    "Sans-Bold": ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"],
    "Mono": ["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf"],
}
FALLBACK = {"Sans": "Helvetica", "Sans-Bold": "Helvetica-Bold", "Mono": "Courier"}
FONT_DIRS = [
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
]


def register_fonts() -> dict[str, str]:
    names = dict(FALLBACK)
    for logical, files in FONT_FILES.items():
        for path in (d / f for f in files for d in FONT_DIRS):
            if path.exists():
                try:
                    pdfmetrics.registerFont(TTFont(logical, str(path)))
                except Exception:  # unreadable font file – try the next one
                    continue
                names[logical] = logical
                break
    return names


FONT = register_fonts()
SANS, BOLD, MONO = FONT["Sans"], FONT["Sans-Bold"], FONT["Mono"]

# ---------------------------------------------------------------- colours
INK = HexColor("#1f2937")
MUTED = HexColor("#6b7280")
LINE = HexColor("#9ca3af")
PAPER = HexColor("#fbfbfc")


@dataclass(frozen=True)
class Theme:
    fill: object
    stroke: object
    head: object


THEMES = {
    "model": Theme(HexColor("#eff6ff"), HexColor("#1d4ed8"), HexColor("#dbeafe")),
    "service": Theme(HexColor("#fffbeb"), HexColor("#b45309"), HexColor("#fde68a")),
    "storage": Theme(HexColor("#f0fdf4"), HexColor("#15803d"), HexColor("#bbf7d0")),
    "api": Theme(HexColor("#f5f3ff"), HexColor("#6d28d9"), HexColor("#ddd6fe")),
    "error": Theme(HexColor("#fef2f2"), HexColor("#b91c1c"), HexColor("#fecaca")),
    "frontend": Theme(HexColor("#ecfeff"), HexColor("#0e7490"), HexColor("#a5f3fc")),
    "external": Theme(HexColor("#f9fafb"), HexColor("#6b7280"), HexColor("#e5e7eb")),
    "factory": Theme(HexColor("#fdf4ff"), HexColor("#a21caf"), HexColor("#f5d0fe")),
}


# ================================================================ source parsing
@dataclass
class ClassInfo:
    name: str
    module: str
    bases: list[str] = field(default_factory=list)
    doc: str = ""
    attrs: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    abstract: bool = False

    @property
    def stereotype(self) -> str:
        parts = []
        if "Enum" in self.bases:
            parts.append("«enumeration»")
        elif self.abstract and not self.attrs and all("{abstract}" in m for m in self.methods):
            parts.append("«interface»")
        elif self.abstract:
            parts.append("«abstract»")
        if "Serializable" in self.bases:
            parts.append("implements Serializable")
        return "  ·  ".join(parts)


def _short(text: str, limit: int = 18) -> str:
    text = text.replace("typing.", "").strip("'\"")
    return text if len(text) <= limit else text.split("[")[0]


def _visibility(name: str) -> str:
    if name.startswith("__") and not name.endswith("__"):
        return "-"
    return "#" if name.startswith("_") else "+"


def _format_args(fn: ast.FunctionDef) -> str:
    a = fn.args
    names = [x.arg for x in a.posonlyargs + a.args if x.arg not in ("self", "cls")]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    names += [x.arg for x in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    if len(names) > 3:
        names = names[:2] + [".."]
    return ", ".join(names)


def _decorators(fn: ast.FunctionDef) -> set[str]:
    out = set()
    for d in fn.decorator_list:
        out.add(ast.unparse(d).split("(")[0])
    return out


def parse_class(node: ast.ClassDef, module: str) -> ClassInfo:
    info = ClassInfo(node.name, module, [ast.unparse(b).split("[")[0] for b in node.bases],
                     (ast.get_docstring(node) or "").split("\n")[0])
    info.abstract = "ABC" in info.bases

    setters = {d.split(".")[0] for item in node.body if isinstance(item, ast.FunctionDef)
               for d in _decorators(item) if d.endswith(".setter")}
    properties = {item.name for item in node.body if isinstance(item, ast.FunctionDef)
                  and "property" in _decorators(item)}

    for item in node.body:
        # class level constants / attributes
        if isinstance(item, (ast.Assign, ast.AnnAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            for t in targets:
                if not isinstance(t, ast.Name):
                    continue
                if "Enum" in info.bases:
                    info.attrs.append(t.id)
                    continue
                value = ast.unparse(item.value) if item.value is not None else ""
                suffix = f" = {value}" if value and len(value) <= 8 else ""
                if isinstance(item, ast.AnnAssign) and not suffix:
                    suffix = f" : {_short(ast.unparse(item.annotation))}"
                info.attrs.append(f"{_visibility(t.id)} {t.id}{suffix}")
        if not isinstance(item, ast.FunctionDef):
            continue

        decos = _decorators(item)
        if item.name == "__init__":
            arg_types = {a.arg: ast.unparse(a.annotation) for a in item.args.args + item.args.kwonlyargs
                         if a.annotation is not None}
            for stmt in ast.walk(item):
                target = None
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    target = stmt.targets[0]
                elif isinstance(stmt, ast.AnnAssign):
                    target = stmt.target
                if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                        and target.value.id == "self"):
                    name = target.attr
                    if name in properties:  # e.g. self.daily_rate = x goes through the setter
                        continue
                    if any(a.split(" ")[1] == name for a in info.attrs if " " in a):
                        continue
                    typ = (ast.unparse(stmt.annotation) if isinstance(stmt, ast.AnnAssign)
                           else arg_types.get(name.lstrip("_"), ""))
                    info.attrs.append(f"{_visibility(name)} {name}" + (f" : {_short(typ)}" if typ else ""))
            continue
        if item.name.startswith("__") or any(d.endswith(".setter") for d in decos):
            continue

        ret = _short(ast.unparse(item.returns)) if item.returns is not None else ""
        abstract = "abstractmethod" in decos
        info.abstract |= abstract
        if "property" in decos:
            line = f"{_visibility(item.name)} {item.name}" + (f" : {ret}" if ret else "")
            if item.name in setters:
                line += " {get,set}"
        else:
            line = f"{_visibility(item.name)} {item.name}({_format_args(item)})" + (f" : {ret}" if ret else "")
            if "classmethod" in decos or "staticmethod" in decos:
                line += " {static}"
        if abstract:
            line += " {abstract}"
        info.methods.append(line)
    return info


def parse_backend() -> dict[str, ClassInfo]:
    classes: dict[str, ClassInfo] = {}
    for py in sorted((ROOT / "backend").rglob("*.py")):
        module = py.relative_to(ROOT).as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes[node.name] = parse_class(node, module)
    # library classes the project builds on
    classes["BaseHTTPRequestHandler"] = ClassInfo(
        "BaseHTTPRequestHandler", "http.server (stdlib)", methods=["+ do_GET()", "+ do_POST()",
                                                                     "+ send_response(code)"])
    classes["Exception"] = ClassInfo("Exception", "builtins")
    return classes


@dataclass
class JsClass:
    file: str
    name: str
    methods: list[str]


def parse_js_classes(folder: Path) -> list[JsClass]:
    result = []
    for path in sorted(folder.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        starts = [(m.start(), m.group(1)) for m in re.finditer(r"^(?:export )?class (\w+)", text, re.M)]
        for i, (pos, name) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
            methods = [m for m in re.findall(r"^  (?:async |static )?(\w+)\([^)]*\)\s*\{", text[pos:end], re.M)
                       if m != "constructor"]
            result.append(JsClass(path.name, name, methods))
    return result


@dataclass
class RouteInfo:
    method: str
    path: str
    handler: str
    admin: bool


def parse_routes() -> list[RouteInfo]:
    src = (ROOT / "backend" / "api" / "server.py").read_text(encoding="utf-8")
    found = re.findall(r'r\.add\("(\w+)",\s*rf?"([^"]+)",\s*self\.(\w+)(,\s*admin=True)?\)', src)
    return [RouteInfo(method, re.sub(r"\{ID\}|\(\?P<(\w+)>[^)]*\)", "{id}", path), handler, bool(admin))
            for method, path, handler, admin in found]


def parse_pages() -> dict[str, str]:
    src = (ROOT / "backend" / "api" / "server.py").read_text(encoding="utf-8")
    return dict(re.findall(r'"(/[\w]*)":\s*"([\w.]+\.html)"', src.split("PAGES =")[1].split("\n")[0]))


def parse_schema() -> tuple[list[tuple[str, list[dict], list[dict]]], list[str]]:
    """Run SqliteRepository.SCHEMA in an in-memory database and read it back."""
    import sqlite3
    tree = ast.parse((ROOT / "backend" / "storage" / "repository.py").read_text(encoding="utf-8"))
    schema = next(ast.literal_eval(n.value) for c in ast.walk(tree) if isinstance(c, ast.ClassDef)
                  and c.name == "SqliteRepository" for n in c.body
                  if isinstance(n, ast.Assign) and n.targets[0].id == "SCHEMA")
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema)
    tables = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid"):
        cols = [dict(zip(("cid", "name", "type", "notnull", "default", "pk"), r))
                for r in conn.execute(f"PRAGMA table_info({name})")]
        fks = [dict(zip(("id", "seq", "table", "from", "to"), r[:5]))
               for r in conn.execute(f"PRAGMA foreign_key_list({name})")]
        uniques = {conn.execute(f"PRAGMA index_info({idx[1]})").fetchone()[2]
                   for idx in conn.execute(f"PRAGMA index_list({name})") if idx[2] and idx[3] == "u"}
        for col in cols:
            col["unique"] = col["name"] in uniques
        tables.append((name, cols, fks))
    indexes = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name")]
    conn.close()
    return tables, indexes


def module_doc(path: Path) -> str:
    try:
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    except SyntaxError:
        return ""
    return doc.strip().split("\n")[0].rstrip(".")


# ================================================================ drawing helpers
@dataclass
class Box:
    x: float
    top: float
    w: float
    h: float

    @property
    def bottom(self) -> float:
        return self.top + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.top + self.h / 2

    @property
    def right(self) -> float:
        return self.x + self.w

    def top_at(self, f: float = .5): return (self.x + self.w * f, self.top)
    def bottom_at(self, f: float = .5): return (self.x + self.w * f, self.bottom)
    def left_at(self, f: float = .5): return (self.x, self.top + self.h * f)
    def right_at(self, f: float = .5): return (self.right, self.top + self.h * f)


class Pdf:
    """Thin wrapper around reportlab's canvas using top-left coordinates."""

    def __init__(self, path: Path):
        self.c = canvas.Canvas(str(path), pagesize=(W, H))
        self.c.setTitle("Rental Car Booking System – Project Structure")
        self.c.setAuthor("generate_diagram.py")
        self.page = 0

    # ---- primitives
    def Y(self, top: float) -> float:
        return H - top

    def text(self, x, top, s, font=SANS, size=10, color=INK, align="left"):
        c = self.c
        c.setFont(font, size)
        c.setFillColor(color)
        y = self.Y(top)
        if align == "center":
            c.drawCentredString(x, y, s)
        elif align == "right":
            c.drawRightString(x, y, s)
        else:
            c.drawString(x, y, s)

    def para(self, x, top, s, width, font=SANS, size=9.5, color=INK, leading=None) -> float:
        leading = leading or size * 1.35
        lines = simpleSplit(s, font, size, width)
        for i, line in enumerate(lines):
            self.text(x, top + size + i * leading, line, font, size, color)
        return len(lines) * leading

    def rect(self, x, top, w, h, fill=None, stroke=INK, radius=6, lw=1, dash=None):
        c = self.c
        c.setLineWidth(lw)
        c.setDash(*dash) if dash else c.setDash()
        if fill is not None:
            c.setFillColor(fill)
        if stroke is not None:
            c.setStrokeColor(stroke)
        c.roundRect(x, self.Y(top + h), w, h, radius, stroke=int(stroke is not None),
                    fill=int(fill is not None))
        c.setDash()

    def poly(self, pts, color=INK, lw=1, dash=None):
        c = self.c
        c.setStrokeColor(color)
        c.setLineWidth(lw)
        c.setDash(*dash) if dash else c.setDash()
        p = c.beginPath()
        p.moveTo(pts[0][0], self.Y(pts[0][1]))
        for x, y in pts[1:]:
            p.lineTo(x, self.Y(y))
        c.drawPath(p, stroke=1, fill=0)
        c.setDash()

    def _shape(self, pts, fill, stroke=INK, lw=1):
        c = self.c
        c.setStrokeColor(stroke)
        c.setFillColor(fill)
        c.setLineWidth(lw)
        c.setDash()
        p = c.beginPath()
        p.moveTo(pts[0][0], self.Y(pts[0][1]))
        for x, y in pts[1:]:
            p.lineTo(x, self.Y(y))
        p.close()
        c.drawPath(p, stroke=1, fill=1)

    @staticmethod
    def _dir(tip, frm):
        dx, dy = tip[0] - frm[0], tip[1] - frm[1]
        length = max((dx * dx + dy * dy) ** .5, 1e-6)
        return dx / length, dy / length

    def head(self, tip, frm, kind="open", color=INK, size=9):
        ux, uy = self._dir(tip, frm)
        px, py = -uy, ux
        back = (tip[0] - ux * size, tip[1] - uy * size)
        left = (back[0] + px * size * .55, back[1] + py * size * .55)
        right = (back[0] - px * size * .55, back[1] - py * size * .55)
        if kind == "triangle":
            self._shape([tip, left, right], white, color)
        elif kind == "filled":
            self._shape([tip, left, right], color, color)
        else:
            self.poly([left, tip, right], color)

    def diamond(self, at, toward, filled=True, color=INK, size=11):
        ux, uy = self._dir(toward, at)
        px, py = -uy, ux
        mid = (at[0] + ux * size / 2, at[1] + uy * size / 2)
        far = (at[0] + ux * size, at[1] + uy * size)
        pts = [at, (mid[0] + px * 4.5, mid[1] + py * 4.5), far, (mid[0] - px * 4.5, mid[1] - py * 4.5)]
        self._shape(pts, color if filled else white, color)

    def label(self, x, top, s, size=7.5, color=MUTED, align="center", bg=PAPER):
        w = pdfmetrics.stringWidth(s, SANS, size)
        bx = {"center": x - w / 2, "left": x, "right": x - w}[align]
        self.c.setFillColor(bg)
        self.c.rect(bx - 2, self.Y(top + 3), w + 4, size + 3, stroke=0, fill=1)
        self.text(bx, top, s, SANS, size, color)

    # ---- page frame
    def new_page(self, title: str, subtitle: str = ""):
        if self.page:
            self.c.showPage()
        self.page += 1
        self.c.setFillColor(PAPER)
        self.c.rect(0, 0, W, H, stroke=0, fill=1)
        self.text(M, 46, title, BOLD, 20)
        if subtitle:
            self.text(M, 64, subtitle, SANS, 10, MUTED)
        self.c.setStrokeColor(HexColor("#e5e7eb"))
        self.c.setLineWidth(1)
        self.c.line(M, self.Y(76), W - M, self.Y(76))
        self.text(M, H - 18, "Rental Car Booking System  ·  generated by docs/generate_diagram.py  ·  "
                  f"{date.today().isoformat()}", SANS, 8, MUTED)
        self.text(W - M, H - 18, f"Page {self.page} / {TOTAL_PAGES}", SANS, 8, MUTED, "right")

    def save(self):
        self.c.showPage()
        self.c.save()


# ---------------------------------------------------------------- UML helpers
LINE_H = 10.2
MEMBER_SIZE = 7.4


def _fit(s: str, font: str, size: float, maxw: float) -> str:
    if pdfmetrics.stringWidth(s, font, size) <= maxw:
        return s
    while s and pdfmetrics.stringWidth(s + "..", font, size) > maxw:
        s = s[:-1]
    return s + ".."


def class_height(info: ClassInfo, compact=False) -> float:
    head = 22 + (11 if info.stereotype else 0) + (10 if info.module else 0)
    if compact:
        return head
    return head + max(len(info.attrs), 1) * LINE_H + 8 + max(len(info.methods), 1) * LINE_H + 8


def class_box(pdf: Pdf, info: ClassInfo, x, top, w, theme: str, compact=False) -> Box:
    t = THEMES[theme]
    h = class_height(info, compact)
    head_h = class_height(info, compact=True)
    # shadow + body
    pdf.rect(x + 2, top + 2, w, h, HexColor("#e5e7eb"), None, 5)
    pdf.rect(x, top, w, h, t.fill, t.stroke, 5, 1.1)
    pdf.c.setFillColor(t.head)
    pdf.c.roundRect(x + .6, pdf.Y(top + head_h) + .5, w - 1.2, head_h - 1, 4.5, stroke=0, fill=1)
    if not compact:
        pdf.c.rect(x + .6, pdf.Y(top + head_h) + .5, w - 1.2, 6, stroke=0, fill=1)

    cy = top + 6
    if info.stereotype:
        pdf.text(x + w / 2, cy + 8, _fit(info.stereotype, SANS, 7, w - 8), SANS, 7, t.stroke, "center")
        cy += 11
    name_size = 10.5
    while name_size > 7 and pdfmetrics.stringWidth(info.name, BOLD, name_size) > w - 10:
        name_size -= .5
    pdf.text(x + w / 2, cy + 11, info.name, BOLD, name_size, INK, "center")
    cy += 14
    if info.module:
        pdf.text(x + w / 2, cy + 7, _fit(info.module, SANS, 6.8, w - 8), SANS, 6.8, MUTED, "center")
    if compact:
        return Box(x, top, w, h)

    pdf.c.setStrokeColor(t.stroke)
    pdf.c.setLineWidth(.8)
    y = top + head_h
    pdf.c.line(x, pdf.Y(y), x + w, pdf.Y(y))
    y += 4
    for a in info.attrs or [" "]:
        pdf.text(x + 6, y + 7.6, _fit(a, MONO, MEMBER_SIZE, w - 10), MONO, MEMBER_SIZE, INK)
        y += LINE_H
    y += 4
    pdf.c.setStrokeColor(t.stroke)
    pdf.c.line(x, pdf.Y(y), x + w, pdf.Y(y))
    y += 4
    for m in info.methods or [" "]:
        color = t.stroke if "{abstract}" in m else INK
        pdf.text(x + 6, y + 7.6, _fit(m, MONO, MEMBER_SIZE, w - 10), MONO, MEMBER_SIZE, color)
        y += LINE_H
    return Box(x, top, w, h)


def inherit(pdf: Pdf, parent: Box, children: list[Box], dashed=False, bus_y=None):
    """Draw a UML generalisation tree: children -> one hollow triangle on parent."""
    dash = (4, 3) if dashed else None
    top_child = min(c.top for c in children)
    bus = bus_y if bus_y is not None else parent.bottom + (top_child - parent.bottom) / 2
    xs = [c.cx for c in children] + [parent.cx]
    for ch in children:
        pdf.poly([(ch.cx, ch.top), (ch.cx, bus)], LINE, 1, dash)
    if len(set(round(x) for x in xs)) > 1:
        pdf.poly([(min(xs), bus), (max(xs), bus)], LINE, 1, dash)
    tip = (parent.cx, parent.bottom)
    pdf.poly([(parent.cx, bus), (parent.cx, parent.bottom + 10)], LINE, 1, dash)
    pdf.head(tip, (parent.cx, bus), "triangle", INK, 11)


def inherit_side(pdf: Pdf, child_pt, parent_pt):
    pdf.poly([child_pt, parent_pt], LINE)
    pdf.head(parent_pt, child_pt, "triangle", INK, 11)


def link(pdf: Pdf, pts, start=None, end="open", label=None, label_at=None, dashed=False,
         color=INK):
    """Connector through `pts`. start: 'diamond' | None. end: 'open' | 'filled' | None."""
    pdf.poly(pts, color, 1, (4, 3) if dashed else None)
    if start == "diamond":
        pdf.diamond(pts[0], pts[1], True, color)
    if end:
        pdf.head(pts[-1], pts[-2], end, color)
    if label:
        lx, ly = label_at or ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)
        pdf.label(lx, ly, label)


def elbow(p1, p2, mid=None):
    """Vertical-horizontal-vertical route from p1 to p2."""
    my = mid if mid is not None else (p1[1] + p2[1]) / 2
    return [p1, (p1[0], my), (p2[0], my), p2]


def legend(pdf: Pdf, x, top, width=330):
    items = [("triangle", "inherits from (is-a)", False), ("diamond", "owns / composition (has-a)", False),
             ("open", "uses / association", False), ("open", "creates / depends on", True)]
    pdf.text(x, top, "Legend", BOLD, 9)
    y = top + 14
    for kind, text, dashed in items:
        a, b = (x, y), (x + 40, y)
        if kind == "diamond":
            link(pdf, [a, b], start="diamond", end="open")
        elif kind == "triangle":
            pdf.poly([a, b], LINE)
            pdf.head(b, a, "triangle", INK, 10)
        else:
            link(pdf, [a, b], end="open", dashed=dashed)
        pdf.text(x + 50, y + 3, text, SANS, 8, INK)
        y += 15
    pdf.para(x, y - 3, "Members: + public  # protected  - private.  A name without () is a @property.  "
             "{abstract} must be overridden  ·  {static} classmethod  ·  {get,set} validated setter.",
             width, SANS, 7.5, MUTED)


# ================================================================ page 1 – overview
DESCRIPTIONS = {
    "": "project root",
    "backend": "Python backend (pure standard library)",
    "backend/api": "HTTP layer",
    "backend/models": "domain classes (OOP core)",
    "backend/services": "business logic",
    "backend/storage": "persistence",
    "frontend": "browser UI served by the Python server",
    "frontend/index.html": "landing page  ( / )",
    "frontend/book.html": "book a car + my bookings by email  ( /book )",
    "frontend/admin.html": "admin login + panel  ( /admin )",
    "frontend/404.html": "page not found",
    "frontend/favicon.svg": "site icon",
    "frontend/css": "stylesheets",
    "frontend/css/base.css": "shared tokens, buttons, tables, dialog",
    "frontend/css/landing.css": "landing page styles",
    "frontend/css/book.css": "booking page styles",
    "frontend/css/admin.css": "admin panel styles",
    "frontend/js": "ES modules (classes)",
    "frontend/js/common.js": "ApiClient, Toast, formatting helpers",
    "frontend/js/landing.js": "LandingPage – category cards from the live fleet",
    "frontend/js/book.js": "FleetView, BookingDialog, BookingFinder, BookPage",
    "frontend/js/admin.js": "AuthGate, Overview/Bookings/Fleet/Customers views",
    "data": "runtime data (auto-created)",
    "data/rental.db": "SQLite database – vehicles, customers, bookings",
    "data/rental_data.json": "old JSON data – imported into rental.db once",
    "docs": "documentation",
    "docs/project_structure.pdf": "this file",
    "tests": "unit tests (python -m unittest)",
    "README.md": "setup and usage guide",
    ".gitignore": "keeps runtime data and caches out of git",
    "requirements.txt": "optional dependency for the PDF",
    "__init__.py": "package exports",
}
SKIP = {"__pycache__", ".pytest_cache", ".git", ".venv", "venv", ".idea", ".vscode",
        "rental.db-journal", "rental.db-wal", "rental.db-shm"}


def collect_tree() -> list[tuple[int, str, str, bool]]:
    rows = [(0, ROOT.name + "/", DESCRIPTIONS[""], True)]

    def walk(folder: Path, depth: int):
        entries = sorted((p for p in folder.iterdir()
                          if p.name not in SKIP and not p.name.endswith((".pyc", ".tmp"))),
                         key=lambda p: (p.is_file(), p.name.lower()))
        for p in entries:
            rel = p.relative_to(ROOT).as_posix()
            if p.is_dir():
                rows.append((depth, p.name + "/", DESCRIPTIONS.get(rel, ""), True))
                walk(p, depth + 1)
            else:
                desc = DESCRIPTIONS.get(rel) or (DESCRIPTIONS["__init__.py"] if p.name == "__init__.py"
                                                 else module_doc(p) if p.suffix == ".py" else "")
                rows.append((depth, p.name, desc, False))
        if folder.name == "data" and not (folder / "rental.db").exists():
            rows.append((depth, "rental.db", DESCRIPTIONS["data/rental.db"], False))

    walk(ROOT, 1)
    if not (ROOT / "data").exists():
        rows.append((1, "data/", DESCRIPTIONS["data"], True))
        rows.append((2, "rental.db", DESCRIPTIONS["data/rental.db"], False))
    return rows


def page_overview(pdf: Pdf, classes: dict[str, ClassInfo], js_classes, routes):
    pdf.new_page("Rental Car Booking System – Project Structure",
                 "Small car-rental app built with object-oriented Python. Every diagram in this PDF "
                 "is generated from the source code by docs/generate_diagram.py.")
    # ---- folder tree
    x0, top = M, 104
    pdf.text(x0, top, "1.  Folder structure", BOLD, 13)
    rows = collect_tree()
    step = min(17.5, (H - 70 - (top + 26)) / len(rows))
    y = top + 26
    stack: list[tuple[int, float]] = []  # (depth, y) of open folders
    for depth, name, desc, is_dir in rows:
        x = x0 + 8 + depth * 18
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            px, py = x0 + 8 + stack[-1][0] * 18 + 5, stack[-1][1]
            pdf.poly([(px, py + 4), (px, y - 3), (x - 3, y - 3)], LINE, .8)
        icon_fill = HexColor("#fbbf24") if is_dir else HexColor("#cbd5e1")
        pdf.rect(x, y - 9, 10, 8 if is_dir else 9, icon_fill, None, 1.5)
        pdf.text(x + 15, y, name, BOLD if is_dir else MONO, 9 if is_dir else 8.6, INK)
        if desc:
            pdf.text(x0 + 245, y, _fit(desc, SANS, 8.3, 345), SANS, 8.3, MUTED)
        if is_dir:
            stack.append((depth, y))
        y += step

    # ---- right column
    rx, rw = 660, W - M - 660
    pdf.text(rx, top, "2.  How to run", BOLD, 13)
    steps = [
        ("cd rental-car-booking-system", "open the project folder"),
        ("python main.py", "start the server (no packages needed)"),
        ("set ADMIN_PASSWORD=...", "optional – default admin password is admin123"),
        ("python -m unittest discover tests", "run the unit tests"),
        ("python docs/generate_diagram.py", "rebuild this PDF (needs reportlab)"),
    ]
    yy = top + 22
    for cmd, note in steps:
        pdf.rect(rx, yy - 12, 230, 17, HexColor("#111827"), None, 4)
        pdf.text(rx + 7, yy, cmd, MONO, 8.4, HexColor("#e5e7eb"))
        pdf.text(rx + 240, yy, note, SANS, 8.8, MUTED)
        yy += 21

    page_notes = {"/": "landing page", "/book": "book a car, find / cancel my booking",
                  "/admin": "password login, then bookings, fleet, customers"}
    yy += 8
    pdf.text(rx, yy, "Pages:", BOLD, 9.5)
    for i, (url, html) in enumerate(parse_pages().items()):
        pdf.text(rx + 50 + i * 165, yy, url, MONO, 9, HexColor("#1d4ed8"))
        pdf.text(rx + 50 + i * 165 + 8 + pdfmetrics.stringWidth(url, MONO, 9), yy, html, SANS, 8, MUTED)
    yy += 26

    pdf.text(rx, yy, "3.  REST API (parsed from backend/api/server.py)", BOLD, 13)
    yy += 12
    row_h = 15.5
    pdf.rect(rx, yy, rw, 18 + len(routes) * row_h, white, HexColor("#e5e7eb"), 6)
    pdf.text(rx + 10, yy + 13, "METHOD", BOLD, 7.5, MUTED)
    pdf.text(rx + 60, yy + 13, "PATH", BOLD, 7.5, MUTED)
    pdf.text(rx + 275, yy + 13, "HANDLER", BOLD, 7.5, MUTED)
    pdf.text(rx + rw - 10, yy + 13, "ACCESS", BOLD, 7.5, MUTED, "right")
    yy += 18
    for r in routes:
        col = HexColor("#15803d") if r.method == "GET" else HexColor("#b45309")
        pdf.text(rx + 10, yy + 11, r.method, BOLD, 8.2, col)
        pdf.text(rx + 60, yy + 11, r.path, MONO, 8.2, INK)
        pdf.text(rx + 275, yy + 11, f"{r.handler}()", MONO, 8.2, MUTED)
        access = "admin session" if r.admin else "public"
        pdf.text(rx + rw - 10, yy + 11, access, BOLD if r.admin else SANS, 8,
                 HexColor("#b91c1c") if r.admin else MUTED, "right")
        yy += row_h

    yy += 24
    pdf.text(rx, yy, "4.  Tech stack & numbers", BOLD, 13)
    py_files = [p for p in (ROOT / "backend").rglob("*.py")]
    loc = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in py_files)
    project_classes = [c for c in classes.values() if c.module.startswith("backend")]
    tests = len(re.findall(r"^\s+def test_", (ROOT / "tests" / "test_rental_system.py")
                           .read_text(encoding="utf-8"), re.M))
    stats = [(str(len(project_classes)), "Python classes"), (str(len(js_classes)), "JavaScript classes"),
             (str(len(routes)), "API endpoints"), (str(tests), "unit tests"), (f"{loc}", "backend lines")]
    yy += 14
    tile_w = (rw - 4 * 10) / 5
    for i, (num, lab) in enumerate(stats):
        tx = rx + i * (tile_w + 10)
        pdf.rect(tx, yy, tile_w, 52, white, HexColor("#e5e7eb"), 6)
        pdf.text(tx + tile_w / 2, yy + 26, num, BOLD, 18, HexColor("#1d4ed8"), "center")
        pdf.text(tx + tile_w / 2, yy + 42, lab, SANS, 8, MUTED, "center")
    yy += 70
    stack_rows = [
        ("Backend", "Python 3.10+, standard library only: http.server, sqlite3, hashlib, abc, enum"),
        ("Database", "SQLite (data/rental.db) – 4 tables with foreign keys, behind an abstract Repository"),
        ("Admin", "password only · PBKDF2 hash · HttpOnly session cookie · lock-out after 5 wrong tries"),
        ("Frontend", "HTML5, CSS3, JavaScript ES modules with classes + fetch()"),
        ("Docs", "reportlab – this PDF is drawn from the parsed source code"),
    ]
    for k, v in stack_rows:
        pdf.text(rx, yy, k, BOLD, 9.5)
        pdf.para(rx + 70, yy - 9.5, v, rw - 70, SANS, 9, MUTED)
        yy += 18


# ================================================================ page 2 – architecture
def page_architecture(pdf: Pdf, js_classes):
    pdf.new_page("Architecture – layers and request flow",
                 "Each layer only talks to the layer below it. main.py is the composition root that "
                 "wires the objects together.")
    left, right = M, W - M
    width = right - left

    def band(top, h, title, path, theme):
        t = THEMES[theme]
        pdf.rect(left, top, width, h, t.fill, t.stroke, 10, 1.2)
        pdf.text(left + 14, top + 20, title, BOLD, 12, t.stroke)
        pdf.text(left + 14, top + 34, path, MONO, 8, MUTED)

    def node(x, top, w, h, title, sub, theme, mono_sub=False):
        t = THEMES[theme]
        pdf.rect(x, top, w, h, white, t.stroke, 6, 1)
        pdf.text(x + w / 2, top + 17, title, BOLD, 10, INK, "center")
        if sub:
            lines = simpleSplit(sub, MONO if mono_sub else SANS, 7.6, w - 12)
            for i, ln in enumerate(lines[:4]):
                pdf.text(x + w / 2, top + 30 + i * 10, ln, MONO if mono_sub else SANS, 7.6, MUTED, "center")
        return Box(x, top, w, h)

    # --- presentation
    inner = left + 250  # content starts right of the band titles
    band(92, 150, "Presentation layer", "frontend/  ·  runs in the browser", "frontend")
    pages = parse_pages()
    node(inner, 108, 170, 116, "3 pages",
         "  ·  ".join(f"{url} {html}" for url, html in pages.items()) + "  ·  css/ shared + per page",
         "frontend")
    app_box = node(inner + 186, 108, right - 14 - inner - 186, 116, "js/  –  ES modules with classes", "",
                   "frontend")
    files: dict[str, list[str]] = {}
    for jc in js_classes:
        files.setdefault(jc.file, []).append(jc.name)
    cols = 2
    cw = (app_box.w - 20 - (cols - 1) * 8) / cols
    for i, (fname, names) in enumerate(files.items()):
        cx = app_box.x + 10 + (i % cols) * (cw + 8)
        ct = app_box.top + 26 + (i // cols) * 44
        pdf.rect(cx, ct, cw, 38, THEMES["frontend"].fill, THEMES["frontend"].stroke, 4, .8)
        pdf.text(cx + 6, ct + 13, fname, BOLD, 8.6, INK)
        pdf.text(cx + 6, ct + 27, _fit(", ".join(names), MONO, 7, cw - 12), MONO, 7, MUTED)

    # --- api
    band(290, 130, "API layer", "backend/api/server.py  ·  JSON over HTTP", "api")
    y = 318
    gap = 34
    nw = (right - 14 - inner - 3 * gap) / 4
    srv = node(inner, y, nw, 70, "ThreadingHTTPServer", "stdlib · one thread per request", "external")
    hdl = node(srv.right + gap, y, nw, 70, "RentalRequestHandler",
               "do_GET / do_POST · pages + static files · checks admin cookie · RentalError -> 4xx", "api")
    rtr = node(hdl.right + gap, y, nw, 70, "Router", "(method, regex) -> Route (handler, admin flag)", "api")
    n_routes = len(parse_routes())
    n_admin = sum(r.admin for r in parse_routes())
    api = node(rtr.right + gap, y, nw, 70, "RentalAPI",
               f"{n_routes} endpoints ({n_admin} admin-only) · Request in, ApiResponse out", "api")
    for a, b, lab in [(srv, hdl, "request"), (hdl, rtr, "resolve()"), (rtr, api, "handler()")]:
        link(pdf, [(a.right, a.cy), (b.x, b.cy)], label=lab, label_at=((a.right + b.x) / 2, a.cy - 8))

    # fetch arrows between presentation and api
    fx = hdl.cx
    link(pdf, [(fx - 20, 242), (fx - 20, 318)], end="filled")
    link(pdf, [(fx + 20, 318), (fx + 20, 242)], end="filled")
    pdf.label(fx + 30, 266, "fetch('/api/...')  JSON request", 8, INK, "left")
    pdf.label(fx + 30, 280, "JSON response / static files", 8, INK, "left")

    # --- service
    band(468, 112, "Service layer", "backend/services/  ·  business rules", "service")
    svc = node(hdl.x, 492, nw, 72, "RentalService  (Facade)",
               "availability · quote · bookings · fleet admin · stats · DB first, then memory",
               "service")
    auth = node(rtr.x, 492, nw, 72, "AuthService",
                "PBKDF2 password hash · session tokens · 5 tries then 5 min lock", "service")
    seed = node(api.x, 492, api.w, 72, "seed_data.py", "DEFAULT_FLEET – 9 cars loaded on first run",
                "service")
    link(pdf, [(api.x + 24, api.bottom), (api.x + 24, 438), (svc.cx, 438), (svc.cx, svc.top)], end="filled")
    link(pdf, [(api.x + 48, api.bottom), (api.x + 48, 452), (auth.cx, 452), (auth.cx, auth.top)],
         end="filled")
    pdf.label(svc.cx + 8, 462, "method calls", 8, INK, "left")
    pdf.label(auth.cx + 8, 470, "login / check session", 8, INK, "left")

    # --- domain + storage
    split = left + width * .62
    t = THEMES["model"]
    pdf.rect(left, 628, split - left - 10, 170, t.fill, t.stroke, 10, 1.2)
    pdf.text(left + 14, 648, "Domain models – the OOP core", BOLD, 12, t.stroke)
    pdf.text(left + 14, 662, "backend/models/", MONO, 8, MUTED)
    model_nodes = [
        ("Vehicle", "EconomyCar · SUV · LuxuryCar · ElectricCar"),
        ("Customer", "extends Person"),
        ("Booking", "BookingStatus enum"),
        ("Payment", "Cash · Card · Wallet"),
        ("Factories", "VehicleFactory · PaymentFactory"),
    ]
    mw = (split - left - 10 - 28 - 4 * 10) / 5
    mboxes = []
    for i, (title, sub) in enumerate(model_nodes):
        mboxes.append(node(left + 14 + i * (mw + 10), 690, mw, 62, title, sub, "model"))
    pdf.text(left + 14, 780, "All of them implement the Serializable interface (to_dict): saved to the database and sent as JSON.",
             SANS, 8.5, MUTED)

    t = THEMES["storage"]
    pdf.rect(split, 628, right - split, 170, t.fill, t.stroke, 10, 1.2)
    pdf.text(split + 14, 648, "Storage", BOLD, 12, t.stroke)
    pdf.text(split + 14, 662, "backend/storage/", MONO, 8, MUTED)
    repo = node(split + 14, 680, 140, 46, "Repository", "abstract load() / save()", "storage")
    jrepo = node(split + 14, 742, 140, 46, "SqliteRepository", "one transaction per save", "storage")
    inherit(pdf, repo, [jrepo])
    # data cylinder
    cx, ctop, cw2, ch = split + 200, 700, 150, 70
    c = pdf.c
    c.setFillColor(white)
    c.setStrokeColor(t.stroke)
    c.setLineWidth(1)
    c.rect(cx, pdf.Y(ctop + ch), cw2, ch - 8, stroke=0, fill=1)
    c.line(cx, pdf.Y(ctop + 8), cx, pdf.Y(ctop + ch))
    c.line(cx + cw2, pdf.Y(ctop + 8), cx + cw2, pdf.Y(ctop + ch))
    c.ellipse(cx, pdf.Y(ctop + ch + 8), cx + cw2, pdf.Y(ctop + ch - 8), stroke=1, fill=1)
    c.ellipse(cx, pdf.Y(ctop + 16), cx + cw2, pdf.Y(ctop), stroke=1, fill=1)
    pdf.text(cx + cw2 / 2, ctop + 38, "data/rental.db", BOLD, 9, INK, "center")
    pdf.text(cx + cw2 / 2, ctop + 51, "SQLite · 4 tables", SANS, 7.4, MUTED, "center")
    link(pdf, [(jrepo.right, jrepo.cy), (cx, jrepo.cy)], end="filled", label="SQL (sqlite3)",
         label_at=((jrepo.right + cx) / 2, jrepo.cy - 10))

    # service -> models / storage
    link(pdf, [(svc.x + 60, svc.bottom), (svc.x + 60, 628)], end="filled")
    pdf.label(svc.x + 52, 608, "creates & owns model objects", 8, INK, "right")
    rx_ = repo.top_at(.82)[0]
    link(pdf, [(svc.right - 20, svc.bottom), (svc.right - 20, 604), (rx_, 604), (rx_, repo.top)],
         end="filled")
    pdf.label(rx_ + 8, 618, "_repo.save(...)", 8, INK, "left")

    # main.py note
    note_x, note_top = left + 14, 510
    pdf.rect(note_x, note_top, inner - note_x - 20, 64, white, THEMES["external"].stroke, 6, .8, (3, 2))
    pdf.text(note_x + 8, note_top + 13, "main.py  –  composition root", BOLD, 8.6)
    for i, ln in enumerate(["repo = SqliteRepository('data/rental.db')",
                            "service = RentalService(repo)",
                            "auth = AuthService(ADMIN_PASSWORD)",
                            "create_server(service, auth, ...)"]):
        pdf.text(note_x + 8, note_top + 26 + i * 10, ln, MONO, 7, INK)


# ================================================================ page 3 – domain classes
def page_domain(pdf: Pdf, cl: dict[str, ClassInfo]):
    pdf.new_page("Class diagram – domain models",
                 "backend/models/  ·  members are read from the code with Python's ast module")
    r1 = 96
    ser = class_box(pdf, cl["Serializable"], M, r1, 200, "model")
    vf = class_box(pdf, cl["VehicleFactory"], 256, r1, 210, "factory")
    bk = class_box(pdf, cl["Booking"], 486, r1, 250, "model")
    bs = class_box(pdf, cl["BookingStatus"], 770, r1, 120, "model")
    pf = class_box(pdf, cl["PaymentFactory"], 916, r1, W - M - 916, "factory")
    pdf.para(M, ser.bottom + 8, "Implemented by Vehicle, Person, Payment and Booking - every model can turn "
             "itself into a dict for the JSON file and the API.", 200, SANS, 8, MUTED)

    r2 = max(b.bottom for b in (vf, bk, bs, pf)) + 48
    veh = class_box(pdf, cl["Vehicle"], 170, r2, 275, "model")
    cus = class_box(pdf, cl["Customer"], 476, r2, 180, "model")
    per = class_box(pdf, cl["Person"], 690, r2, 140, "model")
    pay = class_box(pdf, cl["Payment"], 862, r2, 210, "model")

    r3 = max(b.bottom for b in (veh, cus, per, pay)) + 48
    sw = 134
    vkids = [class_box(pdf, cl[n], M + i * (sw + 6), r3, sw, "model")
             for i, n in enumerate(["EconomyCar", "SUV", "LuxuryCar", "ElectricCar"])]
    pkids = [class_box(pdf, cl[n], W - M - (3 - i) * (sw + 6) + 6, r3, sw, "model")
             for i, n in enumerate(["CashPayment", "CardPayment", "WalletPayment"])]

    inherit(pdf, veh, vkids)
    inherit(pdf, pay, pkids)
    inherit_side(pdf, (cus.right, cus.top + 30), (per.x, cus.top + 30))

    # Booking compositions
    mid = bk.bottom + (r2 - bk.bottom) / 2
    link(pdf, elbow(bk.bottom_at(.15), veh.top_at(.82), mid), start="diamond", label="_vehicle  1",
         label_at=(veh.top_at(.82)[0] + 34, r2 - 8))
    link(pdf, [bk.bottom_at(.3), (bk.bottom_at(.3)[0], cus.top)], start="diamond", label="_customer  1",
         label_at=(bk.bottom_at(.3)[0] + 36, r2 - 8))
    link(pdf, elbow(bk.bottom_at(.85), pay.top_at(.3), mid), start="diamond", label="_payment  1",
         label_at=(pay.top_at(.3)[0] - 34, r2 - 8))
    link(pdf, [(bk.right, bk.top + 40), (bs.x, bk.top + 40)], label="_status",
         label_at=((bk.right + bs.x) / 2, bk.top + 33))
    # factories
    link(pdf, [(vf.cx, vf.bottom), (vf.cx, veh.top)], dashed=True, label="«creates»",
         label_at=(vf.cx, (vf.bottom + veh.top) / 2))
    link(pdf, [(pay.top_at(.75)[0], pf.bottom), pay.top_at(.75)], dashed=True, label="«creates»",
         label_at=(pay.top_at(.75)[0], (pf.bottom + pay.top) / 2))

    legend(pdf, M, ser.bottom + 62, 205)


def inherit_list(pdf: Pdf, parent: Box, children: list[Box], bus_x: float):
    """Generalisation for children stacked vertically under a parent (bus on the left)."""
    for ch in children:
        pdf.poly([(ch.x, ch.top + 22), (bus_x, ch.top + 22)], LINE)
    pdf.poly([(bus_x, parent.bottom + 10), (bus_x, children[-1].top + 22)], LINE)
    pdf.head((bus_x, parent.bottom), (bus_x, parent.bottom + 10), "triangle", INK, 11)


def package_box(pdf: Pdf, x, top, w, title, lines, theme="model") -> Box:
    t = THEMES[theme]
    h = 30 + len(lines) * 14
    pdf.rect(x, top - 12, 60, 14, t.head, t.stroke, 2, .8)
    pdf.rect(x, top, w, h, t.fill, t.stroke, 3, 1)
    pdf.text(x + 8, top - 2, "«package»", SANS, 6.5, t.stroke)
    pdf.text(x + 8, top + 16, title, BOLD, 9.5)
    for i, n in enumerate(lines):
        pdf.text(x + 10, top + 32 + i * 14, n, SANS, 8.3, INK)
    return Box(x, top, w, h)


# ================================================================ page 4 – HTTP / API layer
def page_api(pdf: Pdf, cl: dict[str, ClassInfo]):
    pdf.new_page("Class diagram – HTTP / API layer and errors",
                 "backend/api/server.py  ·  backend/exceptions.py")
    r1 = 96
    base = class_box(pdf, cl["BaseHTTPRequestHandler"], M, r1, 230, "external")
    exc = class_box(pdf, cl["Exception"], 950, r1, W - M - 950, "external", compact=True)

    r2 = base.bottom + 44
    hdl = class_box(pdf, cl["RentalRequestHandler"], M, r2, 300, "api")
    api = class_box(pdf, cl["RentalAPI"], 380, r2, 290, "api")
    rtr = class_box(pdf, cl["Router"], 715, r2, 200, "api")
    route = class_box(pdf, cl["Route"], 715, rtr.bottom + 46, 200, "api")
    inherit(pdf, base, [hdl])
    link(pdf, [(hdl.right, hdl.top + 40), (api.x, hdl.top + 40)], label="api",
         label_at=((hdl.right + api.x) / 2, hdl.top + 33))
    link(pdf, [(api.right, api.top + 40), (rtr.x, api.top + 40)], start="diamond", label="router",
         label_at=((api.right + rtr.x) / 2, api.top + 33))
    link(pdf, [(rtr.cx, rtr.bottom), (rtr.cx, route.top)], start="diamond", label="_routes  *",
         label_at=(rtr.cx + 30, (rtr.bottom + route.top) / 2))

    r3 = hdl.bottom + 46
    req = class_box(pdf, cl["Request"], M, r3, 145, "api")
    res = class_box(pdf, cl["ApiResponse"], M + 155, r3, 145, "api")
    link(pdf, [(hdl.top_at(.25)[0], hdl.bottom), (req.cx, req.top)], dashed=True, label="«creates»",
         label_at=(req.cx, (hdl.bottom + req.top) / 2))
    link(pdf, [(api.x, res.top + 30), (res.right, res.top + 30)], dashed=True)
    pdf.label((api.x + res.right) / 2, res.top + 23, "returns", 7.5, MUTED)

    # exceptions – vertical tree on the right
    ex_x = 950
    rerr = class_box(pdf, cl["RentalError"], ex_x, exc.bottom + 40, W - M - ex_x, "error")
    inherit(pdf, exc, [rerr])
    y = rerr.bottom + 24
    kids = []
    for name in ("NotFoundError", "VehicleNotAvailableError", "AuthError"):
        kids.append(class_box(pdf, cl[name], ex_x + 34, y, W - M - ex_x - 34, "error"))
        y = kids[-1].bottom + 14
    inherit_list(pdf, rerr, kids, ex_x + 16)
    auth_err = kids[-1]
    tma = class_box(pdf, cl["TooManyAttemptsError"], ex_x + 64, auth_err.bottom + 24, W - M - ex_x - 64, "error")
    inherit_list(pdf, auth_err, [tma], ex_x + 48)
    pdf.para(ex_x, tma.bottom + 12, "Services raise these. RentalRequestHandler catches RentalError and "
             "replies with its status_code and an {error} message.", W - M - ex_x, SANS, 8, MUTED)

    legend(pdf, 380, max(api.bottom, route.bottom) + 40, 300)


# ================================================================ page 5 – services + storage
def page_services(pdf: Pdf, cl: dict[str, ClassInfo]):
    pdf.new_page("Class diagram – services and storage",
                 "backend/services  ·  backend/storage  ·  RentalService gets its Repository from main.py "
                 "(dependency injection)")
    caller = ClassInfo("RentalAPI", "backend/api/server.py")
    api = class_box(pdf, caller, 470, 96, 200, "external", compact=True)

    r2 = api.bottom + 48
    repo = class_box(pdf, cl["Repository"], M, r2, 300, "storage")
    svc = class_box(pdf, cl["RentalService"], 375, r2, 350, "service")
    auth = class_box(pdf, cl["AuthService"], 770, r2, W - M - 770, "service")
    link(pdf, elbow((api.top_at(.35)[0], api.bottom), (svc.top_at(.4)[0], svc.top)), label="_service",
         label_at=(svc.top_at(.4)[0] + 30, svc.top - 14))
    link(pdf, elbow((api.top_at(.75)[0], api.bottom), (auth.top_at(.3)[0], auth.top)), label="_auth",
         label_at=(auth.top_at(.3)[0] + 24, auth.top - 14))
    link(pdf, [(svc.x, svc.top + 40), (repo.right, svc.top + 40)], start="diamond",
         label="_repo  (injected)", label_at=((svc.x + repo.right) / 2, svc.top + 33))

    r3 = repo.bottom + 46
    kw = (repo.w - 10) / 2
    sq = class_box(pdf, cl["SqliteRepository"], M, r3, kw, "storage")
    mr = class_box(pdf, cl["InMemoryRepository"], M + kw + 10, r3, kw, "storage")
    inherit(pdf, repo, [sq, mr])
    pdf.para(M, max(sq.bottom, mr.bottom) + 10, "SqliteRepository (data/rental.db) is used by main.py; "
             "InMemoryRepository is used by the unit tests. RentalService does not know which one it has.",
             repo.w, SANS, 8, MUTED)

    pk = package_box(pdf, 770, auth.bottom + 50, 180, "backend.models",
                     ["Vehicle + 4 subclasses", "VehicleFactory", "Customer / Person", "Booking, BookingStatus",
                      "Payment + 3 subclasses", "PaymentFactory"])
    link(pdf, [(svc.right, pk.top + 30), (pk.x, pk.top + 30)], dashed=True, label="«uses»",
         label_at=((svc.right + pk.x) / 2, pk.top + 23))
    seed = package_box(pdf, pk.right + 20, pk.top, W - M - pk.right - 20, "seed_data.py",
                       ["DEFAULT_FLEET", "9 cars, loaded into an", "empty database on first run"], "service")
    legend(pdf, 770, max(pk.bottom, seed.bottom) + 40, W - M - 770)


# ================================================================ page 6 – database
def page_database(pdf: Pdf):
    tables, indexes = parse_schema()
    pdf.new_page("Database – SQLite (data/rental.db)",
                 "Read from SqliteRepository.SCHEMA by running it in an in-memory database  ·  "
                 "PK = primary key  ·  FK = foreign key")
    t = THEMES["storage"]
    by_name = {name: (cols, fks) for name, cols, fks in tables}
    positions = {"customers": (M + 30, 150), "bookings": (450, 110), "vehicles": (W - M - 300, 150),
                 "counters": (M + 30, 470)}
    tw, row_h, head_h = 290, 19, 30
    boxes: dict[str, Box] = {}
    row_y: dict[tuple[str, str], float] = {}
    for name, (x, top) in positions.items():
        if name not in by_name:
            continue
        cols, fks = by_name[name]
        fk_cols = {f["from"] for f in fks}
        h = head_h + len(cols) * row_h + 6
        pdf.rect(x + 3, top + 3, tw, h, HexColor("#e5e7eb"), None, 6)
        pdf.rect(x, top, tw, h, white, t.stroke, 6, 1.2)
        pdf.c.setFillColor(t.head)
        pdf.c.roundRect(x + .6, pdf.Y(top + head_h) + .5, tw - 1.2, head_h - 1, 5.5, stroke=0, fill=1)
        pdf.c.rect(x + .6, pdf.Y(top + head_h) + .5, tw - 1.2, 6, stroke=0, fill=1)
        pdf.text(x + 12, top + 20, name, BOLD, 11.5, INK)
        pdf.text(x + tw - 12, top + 20, f"{len(cols)} columns", SANS, 7.5, MUTED, "right")
        for i, col in enumerate(cols):
            y = top + head_h + i * row_h
            if i % 2:
                pdf.rect(x + 1, y, tw - 2, row_h, HexColor("#f7faf8"), None, 0)
            key = "PK" if col["pk"] else ("FK" if col["name"] in fk_cols else "")
            if key:
                pdf.rect(x + 8, y + 4, 20, 12, HexColor("#fde68a") if key == "PK" else HexColor("#dbeafe"),
                         None, 3)
                pdf.text(x + 18, y + 13, key, BOLD, 6.5, INK, "center")
            pdf.text(x + 34, y + 13.5, col["name"], MONO if not key else BOLD, 8.6, INK)
            flags = [col["type"]]
            if col["notnull"] and not col["pk"]:
                flags.append("NOT NULL")
            if col["unique"]:
                flags.append("UNIQUE")
            if col["default"] is not None:
                flags.append(f"= {col['default']}")
            pdf.text(x + tw - 10, y + 13.5, "  ".join(flags), MONO, 7.4, MUTED, "right")
            row_y[(name, col["name"])] = y + row_h / 2
        boxes[name] = Box(x, top, tw, h)

    # relationships (one -> many), drawn from the foreign keys
    for name, cols, fks in tables:
        for fk in fks:
            if name not in boxes or fk["table"] not in boxes:
                continue
            child, parent = boxes[name], boxes[fk["table"]]
            cy, py = row_y[(name, fk["from"])], row_y[(fk["table"], fk["to"])]
            if parent.x < child.x:
                a, b = (child.x, cy), (parent.right, py)
                mid = (a[0] + b[0]) / 2
            else:
                a, b = (child.right, cy), (parent.x, py)
                mid = (a[0] + b[0]) / 2
            pdf.poly([a, (mid, cy), (mid, py), b], t.stroke, 1.3)
            # crow's foot (many) at the child side, bar (one) at the parent side
            dx = -1 if parent.x < child.x else 1
            for off in (-5, 0, 5):
                pdf.poly([(a[0] + dx * 10, cy), (a[0], cy + off)], t.stroke, 1.1)
            pdf.poly([(b[0] - dx * 6, py - 6), (b[0] - dx * 6, py + 6)], t.stroke, 1.3)
            pdf.label(mid, min(cy, py) - 8, f"{fk['table']} 1 : N {name}", 7.5, t.stroke)

    # notes
    nx, ny, nw = 450, max(b.bottom for b in boxes.values()) + 40, W - M - 450
    notes = [
        ("Relationships", "One customer has many bookings; one vehicle has many bookings. Foreign keys are "
                          "switched on (PRAGMA foreign_keys = ON), so a booking can never point to a missing "
                          "car or customer."),
        ("Rules in the database", "CHECK (daily_rate > 0) and CHECK (status IN confirmed / cancelled / "
                                  "completed); customers.email is UNIQUE."),
        ("Indexes", ", ".join(indexes) + " - make 'bookings of this car / customer' fast."),
        ("Transactions", "SqliteRepository.save() writes everything for one action (e.g. new customer + "
                         "booking + id counters) in ONE transaction - all or nothing."),
        ("Upserts", "INSERT ... ON CONFLICT DO UPDATE, so the same save() call adds new rows and updates "
                    "existing ones (rate change, retire a car, booking status)."),
        ("Old data", "If data/rental_data.json exists and the database is empty, main.py imports it once "
                     "(import_legacy_json). The JSON file itself is not changed."),
    ]
    y = ny
    for title, text in notes:
        pdf.text(nx, y + 10, title, BOLD, 9.5, INK)
        y += pdf.para(nx + 150, y, text, nw - 150, SANS, 9, MUTED, 12.5) + 10

    legend_y = boxes["counters"].bottom + 30 if "counters" in boxes else ny
    pdf.text(M + 30, legend_y, "Legend", BOLD, 9)
    pdf.rect(M + 30, legend_y + 8, 20, 12, HexColor("#fde68a"), None, 3)
    pdf.text(M + 40, legend_y + 17, "PK", BOLD, 6.5, INK, "center")
    pdf.text(M + 58, legend_y + 17, "primary key (unique id of the row)", SANS, 8, INK)
    pdf.rect(M + 30, legend_y + 26, 20, 12, HexColor("#dbeafe"), None, 3)
    pdf.text(M + 40, legend_y + 35, "FK", BOLD, 6.5, INK, "center")
    pdf.text(M + 58, legend_y + 35, "foreign key (points to another table)", SANS, 8, INK)
    pdf.text(M + 30, legend_y + 54, "Line: bar = one, crow's foot = many", SANS, 8, INK)


# ================================================================ page 5 – sequence
def page_sequence(pdf: Pdf):
    pdf.new_page("Sequence diagram – customer books a car",
                 "POST /api/bookings   ·   solid arrow = call   ·   dashed arrow = return value")
    actors = [
        ("Customer", "browser", "external"),
        ("BookingDialog", "js/book.js", "frontend"),
        ("RentalRequestHandler", "api/server.py", "api"),
        ("RentalAPI", "api/server.py", "api"),
        ("RentalService", "services", "service"),
        ("Vehicle", "SUV / EconomyCar ...", "model"),
        ("PaymentFactory", "models/payment.py", "factory"),
        ("Booking", "models/booking.py", "model"),
        ("SqliteRepository", "data/rental.db", "storage"),
    ]
    n = len(actors)
    span = (W - 2 * M) / n
    xs = [M + span * (i + .5) for i in range(n)]
    top, bottom = 96, H - 50
    for (name, sub, theme), x in zip(actors, xs):
        t = THEMES[theme]
        pdf.rect(x - span / 2 + 6, top, span - 12, 36, t.fill, t.stroke, 5, 1)
        pdf.text(x, top + 16, name, BOLD, 9.2, INK, "center")
        pdf.text(x, top + 28, sub, SANS, 7, MUTED, "center")
        pdf.poly([(x, top + 36), (x, bottom)], LINE, .8, (3, 3))

    idx = {a[0]: i for i, a in enumerate(actors)}
    steps = [
        ("Customer", "BookingDialog", "fill form, click Confirm", False),
        ("BookingDialog", "RentalRequestHandler", "POST /api/bookings  {name, email, vehicle_id, dates, payment}", False),
        ("RentalRequestHandler", "RentalRequestHandler", "_read_body() -> Request (public route, no login)", False),
        ("RentalRequestHandler", "RentalAPI", "router.resolve() -> create_booking(req)", False),
        ("RentalAPI", "RentalService", "create_booking(**data)", False),
        ("RentalService", "RentalService", "_parse_range()  +  is_available()", False),
        ("RentalService", "RentalService", "_customer_for(email)  -> existing or new Customer", False),
        ("RentalService", "Vehicle", "calculate_rent(days)   (polymorphic)", False),
        ("Vehicle", "RentalService", "rent", True),
        ("RentalService", "PaymentFactory", "create(method, rent)", False),
        ("PaymentFactory", "RentalService", "Cash | Card | WalletPayment", True),
        ("RentalService", "Booking", "Booking(id, customer, vehicle, start, end, payment)", False),
        ("RentalService", "SqliteRepository", "save(customers, bookings, counters)  – one transaction", False),
        ("RentalService", "RentalAPI", "booking.to_dict()  (then added to memory)", True),
        ("RentalAPI", "RentalRequestHandler", "ApiResponse(201, booking)", True),
        ("RentalRequestHandler", "BookingDialog", "201 Created  +  JSON", True),
        ("BookingDialog", "Customer", "shows booking ID, refreshes the fleet", True),
    ]
    y = top + 66
    for i, (a, b, text, ret) in enumerate(steps, 1):
        xa, xb = xs[idx[a]], xs[idx[b]]
        num = f"{i}. "
        if a == b:
            pts = [(xa, y - 6), (xa + 34, y - 6), (xa + 34, y + 10), (xa + 2, y + 10)]
            pdf.poly(pts, INK, 1)
            pdf.head(pts[-1], pts[-2], "filled", INK, 7)
            pdf.label(xa + 40, y + 6, num + text, 8, INK, "left")
            y += 40
            continue
        pdf.poly([(xa, y), (xb, y)], INK, 1, (4, 3) if ret else None)
        pdf.head((xb, y), (xa, y), "open" if ret else "filled", INK, 8)
        mid = (xa + xb) / 2
        pdf.label(mid, y - 5, num + text, 8, MUTED if ret else INK)
        y += 34

    # alternative path note
    nx, ny = xs[idx["RentalService"]] - 30, y + 2
    nw = W - M - nx
    pdf.rect(nx, ny, nw, 46, HexColor("#fef2f2"), THEMES["error"].stroke, 5, .9, (3, 2))
    pdf.text(nx + 10, ny + 16, "alt  [car already booked / bad input]", BOLD, 8.6, THEMES["error"].stroke)
    pdf.text(nx + 10, ny + 32, "VehicleNotAvailableError / RentalError is raised -> handler returns 409 / 400 "
             "-> dialog shows the message", SANS, 8, INK)


# ================================================================ page 6 – OOP concepts
def page_concepts(pdf: Pdf, cl: dict[str, ClassInfo], js_classes):
    pdf.new_page("OOP concepts – where each one is used",
                 "Lists in the last column marked (auto) are computed from the source code")
    project = {k: v for k, v in cl.items() if v.module.startswith("backend")}
    inherit_pairs = [f"{c.name} -> {b}" for c in project.values() for b in c.bases
                     if b not in ("ABC", "Serializable", "Enum", "Exception")]
    abstracts = [c.name for c in project.values() if c.abstract]
    def names(info: ClassInfo) -> set[str]:
        return {m[2:].split("(")[0].split(" :")[0].split(" {")[0].strip() for m in info.methods}

    overridden = sorted({n for c in project.values() for b in c.bases if b in cl
                         for n in names(c) & names(cl[b])})
    setters = [f"{c.name}.{m.split(' ')[1]}" for c in project.values() for m in c.methods if "{get,set}" in m]
    multi = [f"{c.name}({', '.join(c.bases)})" for c in project.values() if len(c.bases) > 1]

    rows = [
        ("Class & object", "A class is a blueprint; objects are the real things built from it.",
         f"{len(project)} Python classes (auto). Each car, customer, booking and payment is an object; "
         "RentalService holds them in dictionaries keyed by id."),
        ("Encapsulation", "Hide internal data; expose it only through methods / properties that validate.",
         "Validated setters (auto): " + ", ".join(setters) + ". Private Vehicle.__daily_rate and "
         "AuthService.__password_hash (never leaves the class). Booking status changes only via cancel() / "
         "complete(). Person/Customer and Vehicle validate their data in the constructor."),
        ("Abstraction", "Define WHAT must exist (abstract class) without saying HOW.",
         "Abstract classes (auto): " + ", ".join(abstracts) + ". Uses abc.ABC + @abstractmethod; "
         "Vehicle() cannot be instantiated (tested)."),
        ("Inheritance", "A child class reuses and extends a parent class (is-a).",
         "(auto) " + "; ".join(inherit_pairs) + "."),
        ("Polymorphism", "Same method name, different behaviour per class; caller does not care which.",
         "Overridden in subclasses (auto): " + ", ".join(overridden) + ". RentalService calls "
         "vehicle.calculate_rent(days) and payment.fee() without checking the type."),
        ("super() / constructor chaining", "Child constructor runs the parent's constructor first.",
         "SUV / LuxuryCar / ElectricCar.__init__ -> Vehicle.__init__; Customer -> Person; "
         "to_dict() in subclasses extends super().to_dict()."),
        ("Interface", "A contract of methods that classes promise to implement.",
         "Serializable (only abstract to_dict()). Implemented by Vehicle, Person, Payment, Booking."),
        ("Multiple inheritance", "A class can extend more than one parent.",
         "(auto) " + "; ".join(multi) + "."),
        ("Composition (has-a)", "An object is built from other objects it owns.",
         "Booking has a Customer, a Vehicle and a Payment. RentalService owns vehicles, customers, bookings "
         "and a Repository. RentalAPI has a Router, which has many Route objects."),
        ("Class attributes & class methods", "Data / behaviour that belongs to the class, not an instance.",
         "WEEKLY_DISCOUNT, FEE_RATE, MAX_ATTEMPTS, SCHEMA, status_code; VehicleFactory.create(), "
         "PaymentFactory.create(), Customer.from_dict(), Repository.booking_row()."),
        ("Data classes", "Small classes that only carry data (@dataclass writes __init__ for you).",
         "Request, ApiResponse and Route in the HTTP layer."),
        ("Enum", "A fixed set of named values.", "BookingStatus: CONFIRMED, CANCELLED, COMPLETED."),
        ("Custom exceptions", "Your own error classes in a hierarchy.",
         "RentalError -> NotFoundError (404), VehicleNotAvailableError (409), AuthError (401) -> "
         "TooManyAttemptsError (429). The status_code class attribute becomes the HTTP status."),
        ("Design patterns", "Re-usable OOP solutions.",
         "Factory (VehicleFactory, PaymentFactory) · Repository (Repository / SqliteRepository / "
         "InMemoryRepository) · Dependency Injection (RentalService(repository), AuthService(password)) · "
         "Facade (RentalService) · Template method (BaseHTTPRequestHandler -> do_GET / do_POST)."),
        ("OOP in the frontend", "JavaScript ES6 classes in ES modules.",
         "(auto) " + ", ".join(f"{jc.name}" for jc in js_classes) + ". Each page has one App-style class "
         "(LandingPage, BookPage, AdminApp) that composes the views."),
    ]
    cols = [(M, 170), (M + 170, 330), (M + 500, W - 2 * M - 500)]
    y = 92
    pdf.rect(M, y, W - 2 * M, 22, INK, None, 4)
    for (x, w), head in zip(cols, ["Concept", "Meaning", "Where in this project"]):
        pdf.text(x + 10, y + 15, head, BOLD, 9.5, white)
    y += 22
    for i, (concept, meaning, where) in enumerate(rows):
        size, lead = 9.6, 13.2
        lines = max(len(simpleSplit(meaning, SANS, size, cols[1][1] - 20)),
                    len(simpleSplit(where, SANS, size, cols[2][1] - 20)), 1)
        h = lines * lead + 20
        pdf.rect(M, y, W - 2 * M, h, white if i % 2 else HexColor("#f3f4f6"), None, 0)
        pdf.text(cols[0][0] + 10, y + 21, concept, BOLD, 10.5, HexColor("#1d4ed8"))
        pdf.para(cols[1][0] + 10, y + 8, meaning, cols[1][1] - 20, SANS, size, INK, lead)
        pdf.para(cols[2][0] + 10, y + 8, where, cols[2][1] - 20, SANS, size, INK, lead)
        y += h


# ================================================================ main
def main() -> None:
    classes = parse_backend()
    js_classes = parse_js_classes(ROOT / "frontend" / "js")
    routes = parse_routes()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    pdf = Pdf(OUTPUT)
    page_overview(pdf, classes, js_classes, routes)
    page_architecture(pdf, js_classes)
    page_domain(pdf, classes)
    page_api(pdf, classes)
    page_services(pdf, classes)
    page_database(pdf)
    page_sequence(pdf)
    page_concepts(pdf, classes, js_classes)
    pdf.save()
    print(f"PDF written to {OUTPUT.relative_to(ROOT)}  ({pdf.page} pages, "
          f"{sum(1 for c in classes.values() if c.module.startswith('backend'))} classes parsed)")


if __name__ == "__main__":
    main()
