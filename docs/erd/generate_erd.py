#!/usr/bin/env python3
"""Generate a vector PDF ERD from the live Django model metadata.

Run from the Django project root:
    python3 docs/erd/generate_erd.py
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TMIS.settings")

import django  # noqa: E402

django.setup()

from django.apps import apps  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import models  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER, TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A3, landscape  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_PDF = OUTPUT_DIR / "TMIS_entity_relationship_diagram.pdf"

DOMAIN_COLORS = {
    "Identity & CRM": colors.HexColor("#1D4ED8"),
    "Academics": colors.HexColor("#047857"),
    "Finance": colors.HexColor("#B45309"),
    "Engagement & lifecycle": colors.HexColor("#7E22CE"),
    "Platform": colors.HexColor("#475569"),
}

DOMAIN_MODELS = {
    "Platform": ["User"],
    "Identity & CRM": [
        "Contact",
        "Prospect",
        "Student",
        "Teacher",
        "TeacherSpecialization",
        "Meditator",
    ],
    "Academics": ["Course", "Location", "CourseSession", "Enrollment", "InterviewForm"],
    "Finance": ["Invoice", "Payment", "Disbursement"],
    "Engagement & lifecycle": ["Inquiry", "Communication", "MeditatorTransitionEvent"],
}

# Coordinates are fractions of the overview drawing area. They intentionally
# keep the central journey (Contact -> Prospect -> Student -> Enrollment) clear.
OVERVIEW_POSITIONS = {
    "User": (0.05, 0.88),
    "Contact": (0.05, 0.66),
    "Prospect": (0.25, 0.66),
    "Student": (0.45, 0.66),
    "Meditator": (0.65, 0.82),
    "Teacher": (0.25, 0.88),
    "TeacherSpecialization": (0.45, 0.88),
    "Course": (0.05, 0.38),
    "Location": (0.05, 0.15),
    "CourseSession": (0.25, 0.38),
    "Enrollment": (0.45, 0.38),
    "InterviewForm": (0.65, 0.38),
    "Invoice": (0.65, 0.15),
    "Payment": (0.85, 0.15),
    "Disbursement": (0.45, 0.15),
    "Inquiry": (0.85, 0.66),
    "Communication": (0.85, 0.42),
    "MeditatorTransitionEvent": (0.85, 0.88),
}


def domain_for(model_name: str) -> str:
    for domain, names in DOMAIN_MODELS.items():
        if model_name in names:
            return domain
    return "Platform"


def collect_models():
    core_models = list(apps.get_app_config("core").get_models())
    return [get_user_model(), *core_models]


def collect_relationships(model_list):
    included = set(model_list)
    relationships = []
    for source in model_list:
        if source is get_user_model():
            continue
        for field in [*source._meta.fields, *source._meta.many_to_many]:
            if not isinstance(field, (models.ForeignKey, models.OneToOneField, models.ManyToManyField)):
                continue
            target = field.related_model
            if target not in included:
                continue
            if isinstance(field, models.OneToOneField):
                kind = "1:1"
            elif isinstance(field, models.ManyToManyField):
                kind = "M:N"
            else:
                kind = "1:N"
            relationships.append(
                {
                    "source": source.__name__,
                    "target": target.__name__,
                    "field": field.name,
                    "kind": kind,
                    "optional": bool(getattr(field, "null", False) or getattr(field, "blank", False)),
                    "on_delete": getattr(getattr(field, "remote_field", None), "on_delete", None),
                }
            )
    return relationships


def field_type(field) -> str:
    if isinstance(field, models.ManyToManyField):
        return f"M2M<{field.related_model.__name__}>"
    if isinstance(field, models.OneToOneField):
        return f"O2O<{field.related_model.__name__}>"
    if isinstance(field, models.ForeignKey):
        return f"FK<{field.related_model.__name__}>"
    internal = field.get_internal_type()
    return internal.removesuffix("Field")


def key_marker(field) -> str:
    marks = []
    if getattr(field, "primary_key", False):
        marks.append("PK")
    if isinstance(field, (models.ForeignKey, models.OneToOneField)):
        marks.append("FK")
    if getattr(field, "unique", False) and not getattr(field, "primary_key", False):
        marks.append("UQ")
    if isinstance(field, models.ManyToManyField):
        marks.append("M2M")
    return ", ".join(marks)


def cardinality_label(rel) -> str:
    if rel["kind"] == "M:N":
        return "0..*  —  0..*"
    source_cardinality = "0..1" if rel["optional"] else "1"
    target_cardinality = "0..1" if rel["kind"] == "1:1" else "0..*"
    return f"{source_cardinality}  —  {target_cardinality}"


def draw_overview(canvas, doc, model_list, relationships):
    page_w, page_h = landscape(A3)
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#F8FAFC"))
    canvas.rect(0, 0, page_w, page_h, fill=1, stroke=0)

    canvas.setFillColor(colors.HexColor("#0F172A"))
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(18 * mm, page_h - 18 * mm, "TMIS Entity Relationship Diagram")
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(colors.HexColor("#475569"))
    canvas.drawString(
        18 * mm,
        page_h - 24 * mm,
        "Core application models • vector overview • detailed entity dictionary follows",
    )

    left, bottom = 18 * mm, 28 * mm
    area_w, area_h = page_w - 36 * mm, page_h - 62 * mm
    box_w, box_h = 45 * mm, 20 * mm
    coords = {
        name: (left + x * (area_w - box_w), bottom + y * (area_h - box_h))
        for name, (x, y) in OVERVIEW_POSITIONS.items()
    }

    # Draw relationships beneath the entity cards.
    relation_colors = {
        "1:N": colors.HexColor("#64748B"),
        "1:1": colors.HexColor("#DC2626"),
        "M:N": colors.HexColor("#0891B2"),
    }
    for rel in relationships:
        if rel["source"] not in coords or rel["target"] not in coords:
            continue
        sx, sy = coords[rel["source"]]
        tx, ty = coords[rel["target"]]
        x1, y1 = sx + box_w / 2, sy + box_h / 2
        x2, y2 = tx + box_w / 2, ty + box_h / 2
        canvas.setStrokeColor(relation_colors[rel["kind"]])
        canvas.setLineWidth(0.75 if rel["kind"] == "1:N" else 1.1)
        if rel["optional"]:
            canvas.setDash(3, 2)
        else:
            canvas.setDash()
        canvas.line(x1, y1, x2, y2)
        canvas.setFillColor(relation_colors[rel["kind"]])
        canvas.circle(x1, y1, 1.5, fill=1, stroke=0)
        canvas.circle(x2, y2, 1.5, fill=1, stroke=0)

    model_by_name = {model.__name__: model for model in model_list}
    relation_counts = defaultdict(int)
    for rel in relationships:
        relation_counts[rel["source"]] += 1
        relation_counts[rel["target"]] += 1

    for name, (x, y) in coords.items():
        model = model_by_name[name]
        domain = domain_for(name)
        accent = DOMAIN_COLORS[domain]
        canvas.setFillColor(colors.white)
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.roundRect(x, y, box_w, box_h, 4, fill=1, stroke=1)
        canvas.setFillColor(accent)
        canvas.roundRect(x, y + box_h - 6 * mm, box_w, 6 * mm, 4, fill=1, stroke=0)
        canvas.rect(x, y + box_h - 6 * mm, box_w, 3 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 7.8)
        canvas.drawString(x + 2.3 * mm, y + box_h - 4.1 * mm, name)
        canvas.setFillColor(colors.HexColor("#334155"))
        canvas.setFont("Helvetica", 6.4)
        concrete_fields = [f for f in model._meta.fields if not getattr(f, "auto_created", False)]
        canvas.drawString(x + 2.3 * mm, y + 7.2 * mm, f"{len(concrete_fields)} fields")
        canvas.drawString(x + 2.3 * mm, y + 3.4 * mm, f"{relation_counts[name]} relationships")

    # Legend.
    legend_y = 12 * mm
    canvas.setFont("Helvetica-Bold", 7)
    canvas.setFillColor(colors.HexColor("#334155"))
    canvas.drawString(18 * mm, legend_y, "RELATIONSHIPS")
    legend_x = 47 * mm
    for label, color in [("1:N", relation_colors["1:N"]), ("1:1", relation_colors["1:1"]), ("M:N", relation_colors["M:N"])]:
        canvas.setStrokeColor(color)
        canvas.setLineWidth(1.2)
        canvas.line(legend_x, legend_y + 1, legend_x + 9 * mm, legend_y + 1)
        canvas.setFillColor(colors.HexColor("#334155"))
        canvas.drawString(legend_x + 11 * mm, legend_y - 1.6, label)
        legend_x += 28 * mm
    canvas.setDash(3, 2)
    canvas.setStrokeColor(colors.HexColor("#64748B"))
    canvas.line(legend_x, legend_y + 1, legend_x + 9 * mm, legend_y + 1)
    canvas.setDash()
    canvas.drawString(legend_x + 11 * mm, legend_y - 1.6, "optional source")

    legend_x += 43 * mm
    for domain, color in DOMAIN_COLORS.items():
        canvas.setFillColor(color)
        canvas.rect(legend_x, legend_y - 1, 3 * mm, 3 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#334155"))
        canvas.drawString(legend_x + 4 * mm, legend_y - 1.6, domain)
        legend_x += (len(domain) * 1.55 + 12) * mm

    canvas.restoreState()


def page_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#E2E8F0"))
    canvas.line(16 * mm, 12 * mm, landscape(A3)[0] - 16 * mm, 12 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawString(16 * mm, 7.5 * mm, "TMIS CRM • generated from Django model metadata")
    canvas.drawRightString(landscape(A3)[0] - 16 * mm, 7.5 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_story(model_list, relationships):
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "Title",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F172A"),
        alignment=TA_LEFT,
        spaceAfter=6 * mm,
    )
    subtitle = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#475569"),
        spaceAfter=5 * mm,
    )
    heading = ParagraphStyle(
        "DomainHeading",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=3 * mm,
        spaceAfter=3 * mm,
    )
    entity_heading = ParagraphStyle(
        "EntityHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=13,
        textColor=colors.white,
        leftIndent=2 * mm,
        spaceAfter=0,
    )
    cell = ParagraphStyle(
        "Cell",
        parent=styles["Normal"],
        fontSize=7.3,
        leading=9.2,
        textColor=colors.HexColor("#1E293B"),
    )
    cell_small = ParagraphStyle("CellSmall", parent=cell, fontSize=6.7, leading=8.2)
    story = [PageBreak()]
    story.extend(
        [
            Paragraph("Entity dictionary", title),
            Paragraph(
                "Concrete fields inherited from the abstract TimeStampedModel are included. "
                "PK = primary key, FK = foreign key, UQ = unique, M2M = many-to-many. "
                "Django reverse relations are omitted because they do not create columns.",
                subtitle,
            ),
        ]
    )

    model_by_name = {model.__name__: model for model in model_list}
    for domain, names in DOMAIN_MODELS.items():
        story.append(Paragraph(domain, heading))
        for name in names:
            model = model_by_name[name]
            accent = DOMAIN_COLORS[domain]
            table_rows = [[
                Paragraph("Key", cell),
                Paragraph("Field", cell),
                Paragraph("Type / target", cell),
                Paragraph("Required", cell),
                Paragraph("Database details", cell),
            ]]
            fields = [*model._meta.fields, *model._meta.many_to_many]
            for field in fields:
                if getattr(field, "auto_created", False) and not getattr(field, "concrete", False):
                    continue
                required = "No" if getattr(field, "null", False) or getattr(field, "blank", False) else "Yes"
                details = []
                max_length = getattr(field, "max_length", None)
                if max_length:
                    details.append(f"max {max_length}")
                if isinstance(field, models.DecimalField):
                    details.append(f"{field.max_digits},{field.decimal_places}")
                if getattr(field, "default", models.NOT_PROVIDED) is not models.NOT_PROVIDED:
                    default = field.default
                    details.append(f"default {getattr(default, '__name__', default)}")
                if getattr(field, "auto_now_add", False):
                    details.append("auto-created")
                elif getattr(field, "auto_now", False):
                    details.append("auto-updated")
                table_rows.append([
                    Paragraph(key_marker(field) or "—", cell_small),
                    Paragraph(field.name, cell),
                    Paragraph(field_type(field), cell_small),
                    Paragraph(required, cell_small),
                    Paragraph(", ".join(map(str, details)) or "—", cell_small),
                ])

            banner = Table([[Paragraph(name, entity_heading)]], colWidths=[377 * mm])
            banner.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), accent),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2.2 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2 * mm),
            ]))
            field_table = Table(
                table_rows,
                colWidths=[22 * mm, 53 * mm, 72 * mm, 25 * mm, 205 * mm],
                repeatRows=1,
            )
            field_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#334155")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1.5 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1.5 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 1.2 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2 * mm),
            ]))
            story.append(KeepTogether([banner, field_table, Spacer(1, 4 * mm)]))

    story.extend([PageBreak(), Paragraph("Relationship catalog", title)])
    story.append(Paragraph(
        "Each row is oriented as source.field → target. For 1:N relationships, the source "
        "holds the foreign key and many source rows may point to one target row.",
        subtitle,
    ))
    rel_rows = [[
        Paragraph("Source field", cell),
        Paragraph("Target", cell),
        Paragraph("Kind", cell),
        Paragraph("Cardinality (source — target)", cell),
        Paragraph("Optional?", cell),
    ]]
    for rel in sorted(relationships, key=lambda item: (item["source"], item["field"])):
        rel_rows.append([
            Paragraph(f'{rel["source"]}.{rel["field"]}', cell),
            Paragraph(rel["target"], cell),
            Paragraph(rel["kind"], cell),
            Paragraph(cardinality_label(rel), cell),
            Paragraph("Yes" if rel["optional"] else "No", cell),
        ])
    rel_table = Table(rel_rows, colWidths=[95 * mm, 60 * mm, 28 * mm, 105 * mm, 35 * mm], repeatRows=1)
    rel_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 1.7 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.7 * mm),
    ]))
    story.append(rel_table)
    story.append(Spacer(1, 7 * mm))
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph(f"Generated {generated} from the active Django model registry.", subtitle))
    return story


def generate():
    model_list = collect_models()
    relationships = collect_relationships(model_list)
    page_w, page_h = landscape(A3)
    doc = BaseDocTemplate(
        str(OUTPUT_PDF),
        pagesize=landscape(A3),
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="TMIS Entity Relationship Diagram",
        author="TMIS CRM",
        subject="Django model entity relationship diagram",
    )
    overview = PageTemplate(
        id="overview",
        frames=[Frame(0, 0, page_w, page_h, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)],
        onPage=lambda canvas, document: draw_overview(canvas, document, model_list, relationships),
        autoNextPageTemplate="details",
    )
    details = PageTemplate(
        id="details",
        frames=[Frame(16 * mm, 16 * mm, page_w - 32 * mm, page_h - 32 * mm, id="details-frame")],
        onPage=page_footer,
    )
    doc.addPageTemplates([overview, details])
    story = build_story(model_list, relationships)
    # The overview page is canvas-only; the first PageBreak switches to details.
    doc.build(story)
    return len(model_list), len(relationships)


if __name__ == "__main__":
    model_count, relationship_count = generate()
    print(f"Wrote {OUTPUT_PDF}")
    print(f"Included {model_count} entities and {relationship_count} relationships")
