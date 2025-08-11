AI_TRANSLATE_HINT = {
    "zh_CN": "\n~~~由 AI 自動翻譯，僅供參考~~~",
    "zh_TW": "\n~~~由 AI 自動翻譯，僅供參考~~~",
    "en_US": "\n~~~Automatically translated by AI. For reference only.~~~",
    "ru_RU": "\n~~~Переведено ИИ, только для справки~~~",
    "in_ID": "\n~~~Diterjemahkan AI, hanya sebagai referensi~~~",
    "ja_JP": "\n~~~AI翻訳、参考用です~~~",
    "pt_PT": "\n~~~Traduzido por IA, apenas para referência~~~",
    "fr_FR": "\n~~~Traduction IA, à titre indicatif~~~",
    "es_ES": "\n~~~Traducción por IA, solo para referencia~~~",
    "tr_TR": "\n~~~Yapay zeka çevirisi, sadece bilgi amaçlı~~~",
    "de_DE": "\n~~~KI-Übersetzung, nur zur Orientierung~~~",
    "it_IT": "\n~~~Tradotto da AI, solo a scopo informativo~~~",
    "vi_VN": "\n~~~Dịch bởi AI, chỉ mang tính tham khảo~~~",
    "tl_PH": "\n~~~Isinalin ng AI, para sa sanggunian lamang~~~",
    "ar_AE": "\n~~~مترجم بواسطة الذكاء الاصطناعي، للاستشارة فقط~~~",
    "fa_IR": "\n~~~ترجمه شده توسط هوش مصنوعی، فقط برای مرجع~~~",
    "km_KH": "\n~~~បកប្រែដោយ AI សម្រាប់គោលបំណងយោបល់ប៉ុណ្ណោះ~~~",
    "ko_KR": "\n~~~AI 자동 번역 내용이며, 참고용입니다.~~~",
    "ms_MY": "\n~~~Diterjemahkan oleh AI, untuk rujukan sahaja~~~",
    "th_TH": "\n~~~แปลโดย AI เฉพาะเพื่อการอ้างอิง~~~",
}

# 語言代碼映射表，將社群語言代碼映射到接口語言代碼
LANGUAGE_CODE_MAPPING = {
    "zh": "zh_CN",
    "en": "en_US", 
    "ru": "ru_RU",
    "id": "in_ID",
    "ja": "ja_JP",
    "pt": "pt_PT",
    "fr": "fr_FR",
    "es": "es_ES",
    "tr": "tr_TR",
    "de": "de_DE",
    "it": "it_IT",
    "vi": "vi_VN",
    "tl": "tl_PH",
    "ar": "ar_AE",
    "fa": "fa_IR",
    "km": "km_KH",
    "ko": "ko_KR",
    "ms": "ms_MY",
    "th": "th_TH",
}

def escape_markdown_v2(text):
    escape_chars = r'_ * [ ] ( ) ~ ` > # + - = | { } . !'.split()
    for ch in escape_chars:
        text = text.replace(ch, '\\' + ch)
    # 確保換行符不被轉義，保持原始換行
    # 在 MarkdownV2 中，換行符需要保持原樣
    return text

def get_multilingual_content(post, lang):
    """
    根據語言代碼取得對應的翻譯內容並加上 AI 提示
    
    Args:
        post: 文章資料，包含 translations 物件
        lang: 社群語言代碼 (如 "zh_CN", "en_US", "ja_JP")
    
    Returns:
        str: HTML 格式的完整內容
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # 處理 lang 為 None 的情況，默認為英文
    if lang is None:
        lang = "en_US"
        logger.info(f"lang 為 None，設置為默認值: {lang}")
    
    # 檢查是否為英文語言代碼
    is_english = lang in ['en', 'en_US']
    logger.info(f"語言代碼: {lang}, 是否為英文: {is_english}")
    
    # 檢查 translations 是否為 null 或空
    translations = post.get("translations")
    logger.info(f"translations 存在: {translations is not None}, 內容: {translations}")
    
    if not translations or (isinstance(translations, dict) and len(translations) == 0):
        # translations 為 null、空或空字典，直接使用原始 content
        # 原始 content 通常是英文，所以不需要 AI 提示詞
        content = post.get("content", "")
        logger.info(f"translations 為空，使用原始 content，不添加 AI 提示詞")
        return content
    
    # 直接使用傳入的語言代碼，因為現在已經是下劃線形式
    api_lang_code = lang
    
    # 從 translations 中取得對應語言內容
    content = translations.get(api_lang_code)
    logger.info(f"從 translations 中取得語言 {api_lang_code} 的內容: {content is not None}")
    
    # 如果沒有對應翻譯，fallback 到英文，再 fallback 到原始 content
    if not content:
        content = translations.get("en_US") or post.get("content", "")
        logger.info(f"fallback 到英文或原始內容，不添加 AI 提示詞")
        # fallback 到英文或原始內容時，不加上 AI 提示詞
        return content
    
    # 如果是英文，不加上 AI 提示詞
    if is_english:
        full_content = content
        logger.info(f"英文內容，不添加 AI 提示詞")
    else:
        # 翻譯系統已經修復，內容格式正確，直接使用
        logger.info(f"翻譯內容: {repr(content)}")
        
        # 加上對應語言的 AI 提示，並多一個換行
        hint = AI_TRANSLATE_HINT.get(lang, AI_TRANSLATE_HINT["en_US"])
        full_content = content + "\n" + hint
        logger.info(f"非英文內容，添加 AI 提示詞: {hint}")
    
    logger.info(f"最終內容: {repr(full_content)}")
    return full_content 