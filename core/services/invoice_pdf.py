from __future__ import annotations

from io import BytesIO
from pathlib import Path

from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils import timezone


def _read_instruction_text(invoice) -> str:
    try:
        return render_to_string(
            "core/crud/invoice/pdf/donation_statement_text.txt",
            {"invoice": invoice},
        ).strip()
    except TemplateDoesNotExist:
        return (
            "Please submit your course donation to the official TM center account shared by your coordinator.\n"
            f"Include donation/invoice number {invoice.invoice_number} in your transfer notes.\n"
            "For support, contact the TMIS admin team at the center office."
        )


def _draw_tm_logo_from_static_svg(canvas_obj, *, logo_svg_path: Path, left_margin: float, page_height: float, top_margin: float):
    """Use TMIS static logo and draw it left-aligned without hard-failing PDF generation."""
    target_width_pts = 1.95 * 72
    target_height_pts = 0.56 * 72
    x = left_margin
    y = page_height - top_margin - target_height_pts

    # Preferred path: render SVG directly when svglib is available.
    if logo_svg_path.exists():
        try:
            from reportlab.graphics import renderPDF
            from svglib.svglib import svg2rlg

            drawing = svg2rlg(str(logo_svg_path))
            if drawing is not None:
                scale = min(
                    target_width_pts / max(drawing.width, 1),
                    target_height_pts / max(drawing.height, 1),
                )
                drawing.width *= scale
                drawing.height *= scale
                drawing.scale(scale, scale)
                renderPDF.draw(drawing, canvas_obj, x, page_height - top_margin - drawing.height)
                return
        except Exception:
            pass

    # Fallback path: use the committed static PNG asset if SVG renderer is unavailable.
    logo_png_path = logo_svg_path.with_suffix(".png")
    if logo_png_path.exists():
        try:
            from reportlab.lib.utils import ImageReader

            image = ImageReader(str(logo_png_path))
            canvas_obj.drawImage(
                image,
                x,
                y,
                width=target_width_pts,
                height=target_height_pts,
                preserveAspectRatio=True,
                mask="auto",
                anchor="sw",
            )
        except Exception:
            # Never break PDF download because of logo rendering.
            return


def build_invoice_pdf(*, invoice, participant, logo_svg_path: Path) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PDF generation dependency missing: reportlab is not installed in this Python environment."
        ) from exc

    def fmt_money(value) -> str:
        return f"GHS {value:.2f}"

    created_dt = timezone.localtime(invoice.created_at)
    donation_date = created_dt.strftime("%b %d, %Y")
    participant_name = str(participant.prospect if getattr(participant, "prospect_id", None) else participant)
    participant_email = (
        participant.prospect.contact.email
        if getattr(participant, "prospect_id", None) and getattr(participant.prospect, "contact_id", None)
        else ""
    ) or "Email not on file"
    participant_phone = (
        participant.prospect.contact.phone_number
        if getattr(participant, "prospect_id", None) and getattr(participant.prospect, "contact_id", None)
        else ""
    ) or "Phone not on file"
    course_name = invoice.enrollment.course.name if invoice.enrollment_id and invoice.enrollment.course_id else "-"
    suggested_donation = invoice.subtotal
    discount = invoice.discount_amount
    outstanding_donation = invoice.balance_due
    donation_status = (
        "Fulfilled"
        if invoice.payment_status == "Paid"
        else ("Partially Fulfilled" if invoice.payment_status == "Partial" else "Pending")
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.72 * inch,
        rightMargin=0.72 * inch,
        topMargin=0.78 * inch,
        bottomMargin=0.62 * inch,
        title=f"Donation Invoice {invoice.invoice_number}",
    )

    styles = getSampleStyleSheet()
    normal = ParagraphStyle(
        "NormalInvoice",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.8,
        leading=13,
        textColor=colors.HexColor("#1f2937"),
    )
    title = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        alignment=0,
        textColor=colors.HexColor("#111827"),
    )
    small_muted = ParagraphStyle(
        "SmallMuted",
        parent=normal,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#4b5563"),
    )
    section_title = ParagraphStyle(
        "SectionTitle",
        parent=normal,
        fontName="Helvetica-Bold",
        fontSize=10.2,
        leading=13.5,
        textColor=colors.HexColor("#111827"),
    )
    right_value = ParagraphStyle(
        "RightValue",
        parent=normal,
        alignment=TA_RIGHT,
    )
    terms_style = ParagraphStyle(
        "TermsStyle",
        parent=normal,
        fontSize=8.7,
        leading=12,
        textColor=colors.HexColor("#4b5563"),
    )

    story = []
    static_logo_path = Path(__file__).resolve().parent.parent / "static" / "core" / "images" / "tmis-logo.svg"
    story.append(Spacer(1, 0.68 * inch))

    # Header row: strong title left, structured metadata block right.
    header_table = Table(
        [
            [
                Paragraph("DONATION INVOICE", title),
                Paragraph(
                    "<b>Donation / Invoice #:</b> "
                    f"{invoice.invoice_number}<br/>"
                    f"<b>Donation Date:</b> {donation_date}<br/>"
                    f"<b>Donation Status:</b> {donation_status}",
                    small_muted,
                ),
            ],
            [
                Paragraph("TMIS Donation Statement", small_muted),
                Paragraph(f"<b>Course:</b> {course_name}", small_muted),
            ],
        ],
        colWidths=[3.8 * inch, 2.7 * inch],
    )
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BACKGROUND", (1, 0), (1, 1), colors.HexColor("#f8fafc")),
                ("BOX", (1, 0), (1, 1), 0.8, colors.HexColor("#d7dce6")),
                ("LEFTPADDING", (1, 0), (1, 1), 8),
                ("RIGHTPADDING", (1, 0), (1, 1), 8),
                ("TOPPADDING", (1, 0), (1, 1), 7),
                ("BOTTOMPADDING", (1, 0), (1, 1), 7),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 0.2 * inch))

    # Participant card
    participant_lines = "<br/>".join(
        [
            "<b>Participant</b>",
            participant_name,
            participant_email,
            participant_phone,
        ]
    )
    profile_table = Table(
        [[Paragraph(participant_lines, normal)]],
        colWidths=[6.5 * inch],
    )
    profile_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#d7dce6")),
            ]
        )
    )
    story.append(profile_table)
    story.append(Spacer(1, 0.24 * inch))

    # Donation table
    donation_table = Table(
        [
            ["Course Donation", "Donation", "Discount", "Outstanding Donation"],
            [
                Paragraph(course_name, normal),
                Paragraph(fmt_money(suggested_donation), right_value),
                Paragraph(f"-{fmt_money(discount)}", right_value),
                Paragraph(fmt_money(outstanding_donation), right_value),
            ],
        ],
        colWidths=[2.85 * inch, 1.2 * inch, 1.05 * inch, 1.4 * inch],
    )
    donation_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f7f8fb")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#4b5563")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, 1), 9.8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 1), (-1, 1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("ALIGN", (1, 0), (-1, 0), "LEFT"),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#d7dce6")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d7dce6")),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(donation_table)
    story.append(Spacer(1, 0.24 * inch))

    # Summary box on right
    summary_table = Table(
        [
            [Paragraph("Course Donation", small_muted), Paragraph(fmt_money(suggested_donation), right_value)],
            [Paragraph("Discount", small_muted), Paragraph(f"-{fmt_money(discount)}", right_value)],
            [Paragraph("Outstanding Donation", section_title), Paragraph(f"<b>{fmt_money(outstanding_donation)}</b>", right_value)],
        ],
        colWidths=[2.15 * inch, 1.45 * inch],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#d7dce6")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    summary_wrap = Table(
        [[Paragraph("&nbsp;", normal), summary_table]],
        colWidths=[2.9 * inch, 3.6 * inch],
    )
    summary_wrap.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(summary_wrap)
    story.append(Spacer(1, 0.24 * inch))

    instructions = _read_instruction_text(invoice)
    story.append(Paragraph("Donation Instructions", section_title))
    story.append(Spacer(1, 0.07 * inch))
    instruction_rows = []
    for line in instructions.splitlines():
        stripped = line.strip()
        if not stripped:
            instruction_rows.append([""])
            continue
        if stripped.lower().startswith("donation instructions"):
            continue
        if stripped.endswith(":"):
            instruction_rows.append([Paragraph(f"<b>{stripped}</b>", normal)])
        else:
            instruction_rows.append([Paragraph(f"• {stripped}", normal)])
    instructions_table = Table(instruction_rows or [[Paragraph("-", normal)]], colWidths=[6.5 * inch])
    instructions_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbfcfe")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#d7dce6")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(instructions_table)

    story.append(Spacer(1, 0.18 * inch))
    story.append(Paragraph("Terms", section_title))
    story.append(Spacer(1, 0.04 * inch))
    story.append(
        Paragraph(
            "All outstanding donations must be completed before participating in training sessions.",
            terms_style,
        )
    )
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("Generated by TMIS CRM", small_muted))

    def _draw_page(canvas_obj, _doc):
        _draw_tm_logo_from_static_svg(
            canvas_obj,
            logo_svg_path=static_logo_path,
            left_margin=doc.leftMargin,
            page_height=LETTER[1],
            top_margin=doc.topMargin,
        )

    doc.build(story, onFirstPage=_draw_page, onLaterPages=_draw_page)
    buffer.seek(0)
    return buffer.getvalue()
