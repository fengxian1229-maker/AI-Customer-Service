#!/usr/bin/env python3
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


INPUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else None
DOWNLOAD_OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else None

FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def register_font():
    for path in FONT_PATHS:
        if Path(path).exists():
            pdfmetrics.registerFont(TTFont("CJK", path))
            pdfmetrics.registerFont(TTFont("CJKB", path))
            return
    raise RuntimeError("找不到可用中文字型，無法輸出 PDF")


def clean(text):
    text = str(text or "").strip()
    text = re.sub(r"https?://\S+", "[URL]", text)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def parse_time(text):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(text or ""), fmt)
        except ValueError:
            pass
    return datetime.min


def group_display(case):
    names = {
        2: "COP-Jue999",
        11: "COP-JG7",
        12: "COP-GNA777",
        13: "COP-PAG99",
        23: "test",
        24: "COP-CUM777",
        25: "COP-CON777",
        28: "COP-ZAP69",
    }
    out = []
    for group_id in case.get("groupIds") or []:
        try:
            numeric = int(group_id)
        except (TypeError, ValueError):
            numeric = None
        out.append(f"{group_id}（{names.get(numeric)}）" if names.get(numeric) else str(group_id))
    return ", ".join(out) or "?"


def main():
    if not INPUT or not OUT:
        print("Usage: build-official-chat-report-pdf.py <input-json> <out-pdf> [download-copy]", file=sys.stderr)
        sys.exit(1)

    register_font()
    data = json.loads(INPUT.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    cases.sort(key=lambda c: ((c.get("classOrder") or 99), -parse_time(c.get("startTW") or c.get("endTW")).timestamp()))

    definitions = data.get("classDefinitions") or [
        ["機器人獨立完成", "未由真人接管；包含自助教學、收件送後台、等待客戶補資料、等待後台結果、僅開啟選單或無有效問題。"],
        ["機器人判定轉真人", "Ai Jtest 判定問題需要真人客服，或系統紀錄顯示由 Ai Jtest 轉接。"],
        ["客戶手動轉真人", "客戶主動選擇人工服務，或真人客服在沒有機器人判定轉接的情況下接管。"],
    ]
    counts = Counter(c.get("className") or "未分類" for c in cases)

    base = ParagraphStyle("base", parent=getSampleStyleSheet()["Normal"], fontName="CJK", fontSize=8.2, leading=10.5, wordWrap="CJK")
    small = ParagraphStyle("small", parent=base, fontSize=7.2, leading=9.2, wordWrap="CJK")
    note = ParagraphStyle("note", parent=base, fontSize=8.2, leading=11, textColor=colors.HexColor("#4B5563"), wordWrap="CJK")
    h1 = ParagraphStyle("h1", parent=base, fontName="CJKB", fontSize=14, leading=18, spaceBefore=12, spaceAfter=6, wordWrap="CJK")
    h2 = ParagraphStyle("h2", parent=base, fontName="CJKB", fontSize=10.5, leading=13, spaceBefore=10, spaceAfter=4, wordWrap="CJK")
    title = ParagraphStyle("title", parent=base, fontName="CJKB", fontSize=18, leading=23, alignment=1, spaceAfter=6, wordWrap="CJK")
    subtitle = ParagraphStyle("subtitle", parent=note, alignment=1, spaceAfter=10, wordWrap="CJK")

    def p(text, style=base):
        return Paragraph(clean(text), style)

    def table(rows, widths, header=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        style = [
            ("FONTNAME", (0, 0), (-1, -1), "CJK"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        if header:
            style += [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
                ("FONTNAME", (0, 0), (-1, 0), "CJKB"),
            ]
        t.setStyle(TableStyle(style))
        return t

    group_label = data.get("groupLabel") or ",".join(map(str, data.get("groupIds") or []))
    group_names = ", ".join(data.get("groupPlatformNames") or [])
    story = [
        Paragraph(data.get("title") or "Ai Jtest 正式群組對話紀錄（全中文，三分類）", title),
        Paragraph(f"{data.get('sinceTW')} 至 {data.get('untilTW')}｜LiveChat API 重新抓取", subtitle),
        p(f"範圍：只含 LiveChat group {group_label}（{group_names}），排除測試 group 23，且 Ai Jtest 實際有發出訊息或選單的 thread。總數：{len(cases)} 筆。", note),
        p("本版只保留三類：機器人獨立完成、機器人判定轉真人、客戶手動轉真人。", note),
        p(data.get("reportNote") or "注意：本檔已將對話內容中文化；帳號、電話、姓名、圖片檔名與品牌名保留原樣。", note),
        Paragraph("分類定義", h1),
        table([[p("分類"), p("判定方式")]] + [[p(a, small), p(b, small)] for a, b in definitions], [2.0 * inch, 5.2 * inch]),
        Spacer(1, 8),
        Paragraph("統計", h1),
        table([[p("分類"), p("筆數")]] + [[p(a, small), p(str(counts.get(a, 0)), small)] for a, _ in definitions] + [[p("合計", small), p(str(len(cases)), small)]], [5.2 * inch, 1.0 * inch]),
        p("排序：先依三分類排序；同一分類內，時間越新的對話排越前。", note),
    ]

    for class_name, _ in definitions:
        group = [c for c in cases if c.get("className") == class_name]
        story.append(Paragraph(f"{class_name}（{len(group)} 筆）", h1))
        if not group:
            story.append(p("此分類沒有對話。", note))
            continue
        for index, case in enumerate(group, 1):
            story.append(Paragraph(f"{index}. {case.get('customerName') or '未知客戶'}", h2))
            story.append(p("｜".join([
                f"總序號：{case.get('serial')}",
                f"Chat ID：{case.get('chatId')}",
                f"Thread ID：{case.get('threadId')}",
                f"時間：{case.get('startTW')} 至 {case.get('endTW')}",
                f"Group：{group_display(case)}",
            ]), note))
            story.append(p(f"判定理由：{case.get('classReason') or ''}", note))
            story.append(p(f"新版統計分類：{case.get('className') or ''}", note))

            rows = [[p("時間", small), p("說話者", small), p("內容", small)]]
            for line in case.get("transcript") or []:
                rows.append([
                    p(str(line.get("timeTW") or "")[-8:], small),
                    p(line.get("speaker") or "", small),
                    p(line.get("zh") or line.get("original") or "", small),
                ])
            story.append(table(rows, [0.72 * inch, 1.25 * inch, 5.35 * inch]))
            story.append(Spacer(1, 9))

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("CJK", 7)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawRightString(doc.pagesize[0] - 0.45 * inch, doc.pagesize[1] - 0.35 * inch, "Ai Jtest 正式群組對話紀錄")
        canvas.drawRightString(doc.pagesize[0] - 0.45 * inch, 0.35 * inch, str(canvas.getPageNumber()))
        canvas.restoreState()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(OUT), pagesize=letter, rightMargin=0.45 * inch, leftMargin=0.45 * inch, topMargin=0.55 * inch, bottomMargin=0.55 * inch)
    doc.build(story, onFirstPage=page, onLaterPages=page)
    if DOWNLOAD_OUT:
        DOWNLOAD_OUT.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(OUT, DOWNLOAD_OUT)
    print(OUT)


if __name__ == "__main__":
    main()
