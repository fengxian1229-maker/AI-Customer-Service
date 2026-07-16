from __future__ import annotations

from typing import Any

from app.services.language_policy import normalize_language_code


TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "傳": "传",
        "進": "进",
        "驟": "骤",
        "遊": "游",
        "戲": "戏",
        "頁": "页",
        "輸": "输",
        "實": "实",
        "際": "际",
        "經": "经",
        "並": "并",
        "單": "单",
        "圖": "图",
        "請": "请",
        "點": "点",
        "裡": "里",
        "鈕": "钮",
        "從": "从",
        "選": "选",
        "擇": "择",
        "這": "这",
        "個": "个",
        "憑": "凭",
        "證": "证",
        "申": "申",
        "請": "请",
        "錯": "错",
        "誤": "误",
        "畫": "画",
        "面": "面",
        "協": "协",
        "確": "确",
        "認": "认",
        "註": "注",
        "冊": "册",
        "號": "号",
        "帳": "账",
        "戶": "户",
        "後": "后",
        "臺": "台",
        "查": "查",
        "詢": "询",
        "轉": "转",
        "接": "接",
        "儲": "储",
        "值": "值",
        "關": "关",
        "於": "于",
        "無": "无",
        "當": "当",
        "態": "态",
        "數": "数",
        "據": "据",
        "資": "资",
        "料": "料",
        "與": "与",
        "電": "电",
        "話": "话",
        "聯": "联",
        "絡": "络",
        "郵": "邮",
        "箱": "箱",
        "機": "机",
        "簡": "简",
        "體": "体",
        "歡": "欢",
        "幫": "帮",
        "處": "处",
        "理": "理",
        "發": "发",
        "現": "现",
        "顯": "显",
        "示": "示",
        "額": "额",
        "費": "费",
        "時": "时",
        "間": "间",
        "內": "内",
    }
)

SIMPLIFIED_TO_TRADITIONAL = str.maketrans(
    {
        "传": "傳",
        "进": "進",
        "骤": "驟",
        "游": "遊",
        "戏": "戲",
        "页": "頁",
        "输": "輸",
        "实": "實",
        "际": "際",
        "经": "經",
        "并": "並",
        "单": "單",
        "图": "圖",
        "请": "請",
        "点": "點",
        "里": "裡",
        "钮": "鈕",
        "从": "從",
        "选": "選",
        "择": "擇",
        "这": "這",
        "个": "個",
        "凭": "憑",
        "证": "證",
        "错": "錯",
        "误": "誤",
        "画": "畫",
        "协": "協",
        "确": "確",
        "认": "認",
        "注": "註",
        "册": "冊",
        "号": "號",
        "账": "帳",
        "户": "戶",
        "后": "後",
        "台": "台",
        "询": "詢",
        "转": "轉",
        "储": "儲",
        "关": "關",
        "于": "於",
        "无": "無",
        "当": "當",
        "态": "態",
        "数": "數",
        "据": "據",
        "资": "資",
        "与": "與",
        "电": "電",
        "话": "話",
        "联": "聯",
        "络": "絡",
        "邮": "郵",
        "机": "機",
        "简": "簡",
        "体": "體",
        "欢": "歡",
        "帮": "幫",
        "处": "處",
        "发": "發",
        "现": "現",
        "显": "顯",
        "额": "額",
        "费": "費",
        "时": "時",
        "间": "間",
        "内": "內",
    }
)

TRADITIONAL_MARKERS = frozenset(
    "傳進驟遊戲頁輸實際經並單圖請點裡鈕從選擇這個憑證錯誤畫協確認註冊號帳戶後詢轉儲關於無當態數據資與電話聯絡郵機簡體歡幫處發現顯額費時間內"
)
SIMPLIFIED_MARKERS = frozenset(
    "传进骤游戏页输实际经并单图请点里钮从选择这个凭证错误画协确认注册号账户后询转储关于无当态数据资与电话联络邮机简体欢帮处发现显额费时间内"
)


def adapt_chinese_script(value: str | None, reply_language: str | None) -> str:
    text = str(value or "")
    language = normalize_language_code(reply_language)
    if language == "zh-Hans":
        return text.translate(TRADITIONAL_TO_SIMPLIFIED)
    if language == "zh-Hant":
        return text.translate(SIMPLIFIED_TO_TRADITIONAL)
    return text


def adapt_chinese_strings(value: Any, reply_language: str | None) -> Any:
    if isinstance(value, str):
        return adapt_chinese_script(value, reply_language)
    if isinstance(value, list):
        return [adapt_chinese_strings(item, reply_language) for item in value]
    if isinstance(value, tuple):
        return tuple(adapt_chinese_strings(item, reply_language) for item in value)
    if isinstance(value, dict):
        return {key: adapt_chinese_strings(item, reply_language) for key, item in value.items()}
    return value


def chinese_script_mismatch(text: str | None, reply_language: str | None) -> bool:
    value = str(text or "")
    language = normalize_language_code(reply_language)
    if language == "zh-Hans":
        return any(char in TRADITIONAL_MARKERS for char in value)
    if language == "zh-Hant":
        return any(char in SIMPLIFIED_MARKERS for char in value)
    if language not in {"unknown", "zh-Hans", "zh-Hant"}:
        return any(
            "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"
            for char in value
        )
    return False
