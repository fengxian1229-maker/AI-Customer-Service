from collections import Counter
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.reporting.daily_chat_report.formatting import datetime_label, format_message_content, speaker_label, time_label
from app.reporting.daily_chat_report.models import CATEGORY_ORDER, LINGXI_CATEGORY_ORDER, ReportCategory, ReportThread
from app.reporting.daily_chat_report.translation import Translator


def render_daily_chat_report_pdf(
    threads: list[ReportThread],
    *,
    start_at: datetime,
    end_at: datetime,
    output_path: str | Path,
    translator: Translator,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    font_name = _register_cjk_font()
    styles = _styles(font_name)
    category_order = _category_order(threads)
    document = SimpleDocTemplate(
        str(output),
        pagesize=letter,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
    )
    story = [
        Paragraph("LingXi 正式群組對話紀錄（繁體中文）", styles["TitleCn"]),
        Paragraph(f"{datetime_label(start_at)} 至 {datetime_label(end_at)}｜資料庫匯出", styles["Centered"]),
        Spacer(1, 10),
        Paragraph(_scope_text(threads), styles["BodyCn"]),
        Spacer(1, 12),
        Paragraph("分類定義", styles["HeadingCn"]),
        _definition_table(styles, category_order),
        Spacer(1, 14),
        Paragraph("統計", styles["HeadingCn"]),
        _stats_table(threads, category_order),
        Spacer(1, 10),
        Paragraph(_sort_text(category_order), styles["BodyCn"]),
    ]
    sequence = 1
    for category in category_order:
        grouped = [thread for thread in threads if thread.category == category]
        story.append(Spacer(1, 12))
        story.append(Paragraph(f"{category.value}（{len(grouped)} 筆）", styles["HeadingCn"]))
        for thread in grouped:
            story.extend(_thread_story(sequence, thread, styles, translator))
            sequence += 1
    document.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return output


def _thread_story(index: int, thread: ReportThread, styles, translator: Translator) -> list:
    rows = [[Paragraph("時間", styles["TableHeader"]), Paragraph("說話者", styles["TableHeader"]), Paragraph("內容", styles["TableHeader"])]]
    for message in thread.messages:
        rows.append(
            [
                Paragraph(time_label(message.sort_at), styles["TableCell"]),
                Paragraph(speaker_label(message), styles["TableCell"]),
                Paragraph(_escape_lines(format_message_content(message, translator)), styles["TableCell"]),
            ]
        )
    table = Table(rows, colWidths=[0.72 * inch, 1.3 * inch, 5.15 * inch], repeatRows=1)
    table.setStyle(_base_table_style())
    return [
        Spacer(1, 8),
        Paragraph(f"{index}. {thread.customer_name}", styles["ThreadTitle"]),
        Paragraph(
            "｜".join(
                [
                    f"總序號：{index}",
                    f"Chat ID：{thread.chat_id}",
                    f"Thread ID：{thread.thread_id or ''}",
                    f"時間：{datetime_label(thread.start_at)} 至 {datetime_label(thread.end_at)}",
                    f"Group：{thread.group_label}",
                ]
            ),
            styles["BodyCn"],
        ),
        Paragraph(f"判定理由：{thread.category_reason}", styles["BodyCn"]),
        Paragraph(f"新版統計分類：{thread.category.value}", styles["BodyCn"]),
        table,
    ]


def _definition_table(styles, category_order):
    rows = [[Paragraph("分類", styles["TableHeader"]), Paragraph("判定方式", styles["TableHeader"])]]
    for category in category_order:
        rows.append(
            [
                Paragraph(category.value, styles["TableCell"]),
                Paragraph(_category_definition(category), styles["TableCell"]),
            ]
        )
    table = Table(rows, colWidths=[1.8 * inch, 5.25 * inch])
    table.setStyle(_base_table_style())
    return table


def _stats_table(threads: list[ReportThread], category_order):
    counts = Counter(thread.category for thread in threads)
    styles = _styles(_register_cjk_font())
    rows = [[Paragraph("分類", styles["TableHeader"]), Paragraph("筆數", styles["TableHeader"])]]
    for category in category_order:
        rows.append([Paragraph(category.value, styles["TableCell"]), Paragraph(str(counts.get(category, 0)), styles["TableCell"])])
    rows.append([Paragraph("合計", styles["TableCell"]), Paragraph(str(len(threads)), styles["TableCell"])])
    table = Table(rows, colWidths=[4.8 * inch, 1.0 * inch])
    table.setStyle(_base_table_style())
    return table


def _scope_text(threads: list[ReportThread]) -> str:
    groups = sorted({thread.group_id for thread in threads if thread.group_id is not None})
    platforms = sorted({f"COP-{thread.platform}" for thread in threads if thread.platform})
    category_order = _category_order(threads)
    category_text = (
        "本版只保留 LingXi 客服參與分類。"
        if category_order == LINGXI_CATEGORY_ORDER
        else "本版只保留三類：機器人獨立完成、機器人判定轉真人、客戶手動轉真人。"
    )
    return (
        f"範圍：只含 LiveChat group {','.join(str(group) for group in groups) or '無'}"
        f"（{'、'.join(platforms) or '未知平台'}），排除測試 group 23，且只保留 LingXi 客服參與過的 thread。"
        f"總數：{len(threads)}。<br/>{category_text}"
        "<br/>注意：本檔已將對話內容中文化；帳號、電話、姓名、圖片檔名與品牌名保留原樣。"
    )


def _category_order(threads: list[ReportThread]):
    if any(thread.category == ReportCategory.LINGXI_AGENT_PARTICIPATED for thread in threads):
        return LINGXI_CATEGORY_ORDER
    return CATEGORY_ORDER


def _category_definition(category: ReportCategory) -> str:
    definitions = {
        ReportCategory.BOT_COMPLETED: "未由真人接管；包含自助教學、收件送後台、等待客戶補資料、等待後台結果、僅開啟選單或無有效問題。",
        ReportCategory.ROBOT_HANDOFF: "Ai Jtest 判定問題需要真人客服，或系統紀錄顯示由 Ai Jtest 轉接。",
        ReportCategory.CUSTOMER_MANUAL_HANDOFF: "客戶主動選擇人工服務，或真人客服在沒有機器人判定轉接的情況下接管。",
        ReportCategory.LINGXI_AGENT_PARTICIPATED: "LingXi 客服已參與對話；本報表只收錄此類聊天。",
    }
    return definitions[category]


def _sort_text(category_order) -> str:
    if category_order == LINGXI_CATEGORY_ORDER:
        return "排序：時間越新的對話排越前。"
    return "排序：先依三分類排序；同一分類內，時間越新的對話排越前。"


def _styles(base_font: str):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("TitleCn", parent=styles["Title"], fontName=base_font, fontSize=18, leading=24, alignment=TA_CENTER))
    styles.add(ParagraphStyle("Centered", parent=styles["BodyText"], fontName=base_font, fontSize=9, leading=12, alignment=TA_CENTER, textColor=colors.HexColor("#374151")))
    styles.add(ParagraphStyle("BodyCn", parent=styles["BodyText"], fontName=base_font, fontSize=8.6, leading=11.5, textColor=colors.HexColor("#111827")))
    styles.add(ParagraphStyle("HeadingCn", parent=styles["Heading2"], fontName=base_font, fontSize=14, leading=18, spaceAfter=8))
    styles.add(ParagraphStyle("ThreadTitle", parent=styles["Heading3"], fontName=base_font, fontSize=11, leading=14, spaceAfter=4))
    styles.add(ParagraphStyle("TableHeader", parent=styles["BodyText"], fontName=base_font, fontSize=8, leading=10))
    styles.add(ParagraphStyle("TableCell", parent=styles["BodyText"], fontName=base_font, fontSize=7.8, leading=10))
    return styles


def _register_cjk_font() -> str:
    font_name = "DailyChatReportCJK"
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(font_name, str(path)))
            return font_name
        except Exception:
            continue
    return "Helvetica"


def _base_table_style() -> TableStyle:
    return TableStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
    )


def _page_footer(canvas, document) -> None:
    canvas.saveState()
    footer_font = "DailyChatReportCJK" if "DailyChatReportCJK" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    canvas.setFont(footer_font, 8)
    canvas.setFillColor(colors.HexColor("#6b7280"))
    canvas.drawRightString(letter[0] - 0.45 * inch, 0.35 * inch, str(document.page))
    canvas.drawRightString(letter[0] - 0.55 * inch, letter[1] - 0.35 * inch, "LingXi 正式群組對話紀錄")
    canvas.restoreState()


def _escape_lines(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
