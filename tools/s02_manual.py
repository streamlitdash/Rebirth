"""Generate the README diagrams and a printable PDF manual.

Run from any working directory with ``python tools/s02_manual.py``. The README
is the content source; this script only supplies deterministic diagram drawing
and a small Markdown-to-ReportLab renderer.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    CondPageBreak,
    Image as PdfImage,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DOCS = ROOT / "docs"
PDF_PATH = ROOT / "output" / "pdf" / "s01_guide.pdf"

PASTEL_BLUE = "#C4DEF5"
PASTEL_YELLOW = "#F7E5B7"
PASTEL_GREEN = "#CDE8D2"
PASTEL_GREY = "#EEF1F4"
INK = "#151515"
MUTED = "#58616B"


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = ([Path("C:/Windows/Fonts/arialbd.ttf")] if bold else []) + [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _wrapped(draw: ImageDraw.ImageDraw, text: str, width: int, font) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [""]


def _box(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    title: str,
    body: str,
    *,
    fill: str,
) -> None:
    x1, y1, x2, y2 = bounds
    draw.rounded_rectangle(bounds, radius=18, fill=fill, outline=INK, width=3)
    title_font = _font(27, bold=True)
    body_font = _font(21)
    title_lines = _wrapped(draw, title, x2 - x1 - 34, title_font)
    body_lines = _wrapped(draw, body, x2 - x1 - 34, body_font)
    y = y1 + 20
    for line in title_lines:
        draw.text((x1 + 18, y), line, fill=INK, font=title_font)
        y += 33
    y += 4
    for line in body_lines:
        draw.text((x1 + 18, y), line, fill=MUTED, font=body_font)
        y += 27


def _arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    label: str = "",
) -> None:
    draw.line([start, end], fill=INK, width=4)
    x2, y2 = end
    x1, y1 = start
    if abs(x2 - x1) >= abs(y2 - y1):
        direction = 1 if x2 > x1 else -1
        head = [(x2, y2), (x2 - 14 * direction, y2 - 9), (x2 - 14 * direction, y2 + 9)]
    else:
        direction = 1 if y2 > y1 else -1
        head = [(x2, y2), (x2 - 9, y2 - 14 * direction), (x2 + 9, y2 - 14 * direction)]
    draw.polygon(head, fill=INK)
    if label:
        font = _font(18, bold=True)
        midpoint = ((x1 + x2) // 2, (y1 + y2) // 2)
        draw.rounded_rectangle(
            (midpoint[0] - 75, midpoint[1] - 17, midpoint[0] + 75, midpoint[1] + 17),
            radius=7,
            fill="white",
        )
        bbox = draw.textbbox((0, 0), label, font=font)
        draw.text(
            (midpoint[0] - (bbox[2] - bbox[0]) / 2, midpoint[1] - 12),
            label,
            fill=INK,
            font=font,
        )


def _canvas(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (1600, 820), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1600, 8), fill=INK)
    draw.text((64, 38), title, fill=INK, font=_font(38, bold=True))
    draw.text((64, 88), subtitle, fill=MUTED, font=_font(22))
    return image, draw


def draw_architecture(path: Path) -> None:
    image, draw = _canvas(
        "Cube architecture",
        "Strict connectors feed one transactional manager; UI reads only committed snapshots.",
    )
    boxes = [
        (
            (55, 235, 300, 475),
            "Browser",
            "Header, refresh hero, pages, native disclosures",
            PASTEL_GREY,
        ),
        (
            (350, 235, 605, 475),
            "Dash UI",
            "Factory, components, callbacks, progress endpoints",
            PASTEL_BLUE,
        ),
        (
            (655, 200, 945, 510),
            "Refresh Manager",
            "Dates, validation, per-underlying loops, P&L, atomic revision",
            PASTEL_YELLOW,
        ),
        (
            (995, 120, 1535, 330),
            "Personal connectors",
            "Checker, market-state resolver, Risk, Open, Current, Portfolio, thresholds",
            PASTEL_GREEN,
        ),
        (
            (995, 390, 1535, 600),
            "Committed readers",
            "Risk Explorer, Quick Risk, full MarketBook Search, PL workflow",
            PASTEL_BLUE,
        ),
    ]
    for bounds, title, body, fill in boxes:
        _box(draw, bounds, title, body, fill=fill)
    _arrow(draw, (300, 355), (350, 355), label="callbacks")
    _arrow(draw, (605, 355), (655, 355), label="refresh/read")
    _arrow(draw, (945, 275), (995, 225), label="dated calls")
    _arrow(draw, (945, 430), (995, 495), label="snapshot")
    image.save(path, format="PNG", optimize=True)


def draw_dates(path: Path) -> None:
    image, draw = _canvas(
        "One authoritative date chain",
        "The manager computes dates once. Connectors receive them; they do not subtract again.",
    )
    _box(
        draw,
        (60, 255, 390, 425),
        "Market date",
        "Latest weekday; weekend rolls to Friday unless a valid Force Market is applied",
        fill=PASTEL_BLUE,
    )
    _box(
        draw,
        (500, 145, 900, 295),
        "Market-state resolver",
        "Called once; returns exact Live or OFFICIAL",
        fill=PASTEL_GREEN,
    )
    _box(
        draw,
        (500, 385, 900, 555),
        "Checker date",
        "market_date - one business day",
        fill=PASTEL_YELLOW,
    )
    _box(
        draw,
        (1080, 210, 1515, 370),
        "Checker + Portfolio + Open",
        "All receive checker_date (T-1)",
        fill=PASTEL_GREEN,
    )
    _box(
        draw,
        (1080, 475, 1515, 645),
        "Suggested Risk",
        "checker_date - BDay(Age) per source",
        fill=PASTEL_GREEN,
    )
    _box(
        draw,
        (500, 620, 900, 770),
        "Effective Risk",
        "Force Risk wins last; otherwise suggested date",
        fill=PASTEL_BLUE,
    )
    _arrow(draw, (390, 315), (500, 225), label="once")
    _arrow(draw, (390, 385), (500, 470), label="- BDay(1)")
    _arrow(draw, (900, 470), (1080, 295), label="same date")
    _arrow(draw, (900, 510), (1080, 555), label="Age")
    _arrow(draw, (1080, 605), (900, 695), label="derived")
    image.save(path, format="PNG", optimize=True)


def draw_market(path: Path) -> None:
    image, draw = _canvas(
        "Per-underlying market flow",
        "Market order is preserved in the full MarketBook and used to order Risk tenors.",
    )
    _box(
        draw,
        (45, 365, 300, 585),
        "Validated Risk",
        "Unique Underlyings and Risk-only tenor keys",
        fill=PASTEL_BLUE,
    )
    _box(
        draw,
        (365, 145, 665, 285),
        "Market-state resolver",
        "One call for market_date; exact Live or OFFICIAL",
        fill=PASTEL_YELLOW,
    )
    _box(
        draw,
        (365, 335, 665, 475),
        "Open loop",
        "checker_date (T-1) + status + one Underlying",
        fill=PASTEL_GREEN,
    )
    _box(
        draw,
        (365, 540, 665, 680),
        "Current loop",
        "market_date + status + one Underlying",
        fill=PASTEL_GREEN,
    )
    _box(
        draw,
        (735, 365, 1035, 585),
        "Full MarketBook",
        "Outer merge; all market tenors; authoritative orders",
        fill=PASTEL_YELLOW,
    )
    _box(
        draw,
        (1110, 230, 1545, 420),
        "Quick Market",
        "Complete exact curve/surface, including market-only tenors",
        fill=PASTEL_BLUE,
    )
    _box(
        draw,
        (1110, 525, 1545, 705),
        "Risk + P&L",
        "Left join keeps only Risk tenors, sorted by market order",
        fill=PASTEL_GREY,
    )
    _arrow(draw, (515, 285), (515, 335), label="same status")
    _arrow(draw, (585, 285), (585, 540), label="same status")
    _arrow(draw, (300, 430), (365, 405), label="each Udl")
    _arrow(draw, (300, 530), (365, 610), label="each Udl")
    _arrow(draw, (665, 405), (735, 440))
    _arrow(draw, (665, 610), (735, 520))
    _arrow(draw, (1035, 425), (1110, 335), label="all tenors")
    _arrow(draw, (1035, 530), (1110, 615), label="left join")
    image.save(path, format="PNG", optimize=True)


def draw_startup(path: Path) -> None:
    image, draw = _canvas(
        "Nonblocking cold startup",
        "The shell paints before financial connectors run, and only one process-wide writer starts.",
    )
    nodes = [
        (
            (45, 245, 285, 475),
            "Import app",
            "Build callables, manager, routes; no source I/O",
            PASTEL_GREY,
        ),
        (
            (340, 245, 580, 475),
            "First paint",
            "Header, controls, progress hero",
            PASTEL_BLUE,
        ),
        (
            (635, 245, 875, 475),
            "Coordinator",
            "One boot ID, one attempt ID, one pending start",
            PASTEL_YELLOW,
        ),
        (
            (930, 245, 1170, 475),
            "Revision 1",
            "Checker -> Risk -> Market -> P&L -> validate",
            PASTEL_GREEN,
        ),
        (
            (1225, 245, 1555, 475),
            "Atomic recovery",
            "Commit once; reconnect after worker replacement",
            PASTEL_BLUE,
        ),
    ]
    for bounds, title, body, fill in nodes:
        _box(draw, bounds, title, body, fill=fill)
    for left, right, label in (
        ((285, 360), (340, 360), "HTTP"),
        ((580, 360), (635, 360), "/startz"),
        ((875, 360), (930, 360), "thread"),
        ((1170, 360), (1225, 360), "commit"),
    ):
        _arrow(draw, left, right)
        font = _font(18, bold=True)
        midpoint = (left[0] + right[0]) // 2
        bbox = draw.textbbox((0, 0), label, font=font)
        draw.text(
            (midpoint - (bbox[2] - bbox[0]) / 2, 205),
            label,
            fill=INK,
            font=font,
        )
    draw.text(
        (470, 620),
        "Exact /progressz URL   |   boot-ID restart recovery   |   watchdog never starts a duplicate writer",
        fill=INK,
        font=_font(24, bold=True),
    )
    image.save(path, format="PNG", optimize=True)


def _register_fonts() -> tuple[str, str, str]:
    font_sets = [
        (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/consola.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
        ),
    ]
    for regular, bold, mono in font_sets:
        if regular.is_file() and bold.is_file() and mono.is_file():
            pdfmetrics.registerFont(TTFont("Manual", str(regular)))
            pdfmetrics.registerFont(TTFont("ManualBold", str(bold)))
            pdfmetrics.registerFont(TTFont("ManualMono", str(mono)))
            return "Manual", "ManualBold", "ManualMono"
    return "Helvetica", "Helvetica-Bold", "Courier"


def _inline_markup(value: str, *, mono_font: str) -> str:
    placeholders: dict[str, str] = {}

    def save(markup: str) -> str:
        token = f"@@INLINE{len(placeholders)}@@"
        placeholders[token] = markup
        return token

    value = re.sub(
        r"`([^`]+)`",
        lambda match: save(
            f'<font name="{mono_font}" backcolor="#F1F3F5">'
            f"{html.escape(match.group(1))}</font>"
        ),
        value,
    )
    value = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: save(
            f"<u>{html.escape(match.group(1))}</u> "
            f'<font color="#58616B">({html.escape(match.group(2))})</font>'
        ),
        value,
    )
    value = html.escape(value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", value)
    # Markdown links can contain inline-code labels. Restore outer placeholders
    # first so a nested token is present before its own replacement runs.
    for token, markup in reversed(placeholders.items()):
        value = value.replace(token, markup)
    return value


def _styles() -> tuple[dict[str, ParagraphStyle], str]:
    regular, bold, mono = _register_fonts()
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "ManualTitle",
            parent=base["Title"],
            fontName=bold,
            fontSize=27,
            leading=32,
            textColor=colors.HexColor(INK),
            alignment=TA_LEFT,
            spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "ManualH2",
            parent=base["Heading2"],
            fontName=bold,
            fontSize=17,
            leading=21,
            textColor=colors.HexColor(INK),
            spaceBefore=12,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "ManualH3",
            parent=base["Heading3"],
            fontName=bold,
            fontSize=13,
            leading=17,
            textColor=colors.HexColor(INK),
            spaceBefore=9,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "ManualBody",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=9.4,
            leading=13.3,
            textColor=colors.HexColor(INK),
            spaceAfter=6,
            alignment=TA_LEFT,
        ),
        "bullet": ParagraphStyle(
            "ManualBullet",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=9.2,
            leading=13,
            leftIndent=13,
            firstLineIndent=-8,
            bulletIndent=3,
            spaceAfter=3,
        ),
        "code": ParagraphStyle(
            "ManualCode",
            parent=base["Code"],
            fontName=mono,
            fontSize=7.7,
            leading=10.1,
            leftIndent=7,
            rightIndent=7,
            borderColor=colors.HexColor("#D7DDE3"),
            borderWidth=0.5,
            borderPadding=7,
            backColor=colors.HexColor("#F7F8FA"),
            spaceBefore=4,
            spaceAfter=8,
        ),
        "small": ParagraphStyle(
            "ManualSmall",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=7.4,
            leading=9.5,
        ),
        "small_bold": ParagraphStyle(
            "ManualSmallBold",
            parent=base["BodyText"],
            fontName=bold,
            fontSize=7.4,
            leading=9.5,
        ),
    }
    return styles, mono


def _table_flowable(lines: list[str], styles, mono_font: str, width: float) -> Table:
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines
    ]
    if len(rows) > 1 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
        rows.pop(1)
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    contents = [
        [
            Paragraph(
                _inline_markup(cell, mono_font=mono_font),
                styles["small_bold"] if row_index == 0 else styles["small"],
            )
            for cell in row
        ]
        for row_index, row in enumerate(normalized)
    ]
    table = Table(
        contents, colWidths=[width / column_count] * column_count, repeatRows=1
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(PASTEL_BLUE)),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(INK)),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B7C0C9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _page(canvas, document) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D7DDE3"))
    canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor(MUTED))
    canvas.drawString(18 * mm, 9 * mm, "Cube — Risk & P&L manual")
    canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Page {document.page}")
    canvas.restoreState()


def build_pdf() -> None:
    styles, mono_font = _styles()
    document = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=19 * mm,
        title="Cube — Risk & P&L",
        author="Cube project",
        subject="Architecture, connector contracts, operation, and extension guide",
    )
    story = []
    lines = README.read_text(encoding="utf-8").splitlines()
    paragraph: list[str] = []
    code: list[str] = []
    in_code = False
    first_heading = True

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(item.strip() for item in paragraph)
            story.append(
                Paragraph(_inline_markup(text, mono_font=mono_font), styles["body"])
            )
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            if in_code:
                story.append(Preformatted("\n".join(code), styles["code"]))
                code.clear()
                in_code = False
            else:
                in_code = True
            index += 1
            continue
        if in_code:
            code.append(line.rstrip())
            index += 1
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            table_lines: list[str] = []
            while index < len(lines):
                candidate = lines[index].strip()
                if not (candidate.startswith("|") and candidate.endswith("|")):
                    break
                table_lines.append(candidate)
                index += 1
            story.append(
                _table_flowable(table_lines, styles, mono_font, document.width)
            )
            story.append(Spacer(1, 7))
            continue
        image_match = re.fullmatch(r"!\[[^]]*\]\(([^)]+)\)", stripped)
        if image_match:
            flush_paragraph()
            path = ROOT / image_match.group(1)
            if path.is_file():
                with Image.open(path) as source:
                    ratio = source.height / source.width
                story.append(
                    PdfImage(
                        str(path), width=document.width, height=document.width * ratio
                    )
                )
                story.append(Spacer(1, 8))
            index += 1
            continue
        if stripped.startswith("# "):
            flush_paragraph()
            if not first_heading:
                story.append(PageBreak())
            story.append(
                Paragraph(
                    _inline_markup(stripped[2:], mono_font=mono_font), styles["title"]
                )
            )
            story.append(Spacer(1, 3))
            first_heading = False
        elif stripped.startswith("## "):
            flush_paragraph()
            story.append(CondPageBreak(36 * mm))
            story.append(
                Paragraph(
                    _inline_markup(stripped[3:], mono_font=mono_font), styles["h2"]
                )
            )
        elif stripped.startswith("### "):
            flush_paragraph()
            story.append(CondPageBreak(25 * mm))
            story.append(
                Paragraph(
                    _inline_markup(stripped[4:], mono_font=mono_font), styles["h3"]
                )
            )
        elif re.match(r"^[-*] ", stripped):
            flush_paragraph()
            story.append(
                Paragraph(
                    _inline_markup(stripped[2:], mono_font=mono_font),
                    styles["bullet"],
                    bulletText="•",
                )
            )
        elif re.match(r"^\d+\. ", stripped):
            flush_paragraph()
            number, body = stripped.split(". ", 1)
            story.append(
                Paragraph(
                    _inline_markup(body, mono_font=mono_font),
                    styles["bullet"],
                    bulletText=f"{number}.",
                )
            )
        elif not stripped:
            flush_paragraph()
            story.append(Spacer(1, 2))
        else:
            paragraph.append(stripped)
        index += 1
    flush_paragraph()
    document.build(story, onFirstPage=_page, onLaterPages=_page)


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    draw_architecture(DOCS / "s01_flow.png")
    draw_dates(DOCS / "s02_dates.png")
    draw_market(DOCS / "s03_market.png")
    draw_startup(DOCS / "s04_startup.png")
    build_pdf()
    print(f"Generated {PDF_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
