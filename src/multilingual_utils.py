AI_TRANSLATE_HINT = {
    "zh": "\n~~~由 AI 自動翻譯，僅供參考~~~",
    "en": "\n~~~Automatically translated by AI. For reference only.~~~",
    "ru": "\n~~~Переведено ИИ, только для справки~~~",
    "id": "\n~~~Diterjemahkan AI, hanya sebagai referensi~~~",
    "ja": "\n~~~AI翻訳、参考用です~~~",
    "pt": "\n~~~Traduzido por IA, apenas para referência~~~",
    "fr": "\n~~~Traduction IA, à titre indicatif~~~",
    "es": "\n~~~Traducción por IA, solo ~~~referencia~~~",
    "tr": "\n~~~Yapay zeka çevirisi, sadece bilgi amaçlı~~~",
    "de": "\n~~~KI-Übersetzung, nur zur Orientierung~~~",
    "it": "\n~~~Tradotto da AI, solo a scopo informativo~~~",
    "vi": "\n~~~Dịch bởi AI, chỉ mang tính tham khảo~~~",
    "tl": "\n~~~Isinalin ng AI, para sa sanggunian lamang~~~",
    "ar": "\n~~~مترجم بواسطة الذكاء الاصطناعي، للاستشارة فقط~~~",
    "fa": "\n~~~ترجمه شده توسط هوش مصنوعی، فقط برای مرجع~~~",
    "km": "\n~~~បកប្រែដោយ AI សម្រាប់គោលបំណងយោបល់ប៉ុណ្ណោះ~~~",
    "ko": "\n~~~AI 자동 번역 내용이며, 참고용입니다.~~~",
    "ms": "\n~~~Diterjemahkan oleh AI, untuk rujukan sahaja~~~",
    "th": "\n~~~แปลโดย AI เฉพาะเพื่อการอ้างอิง~~~",
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
    return text

def get_multilingual_content(post, lang):
    """
    根據語言代碼取得對應的翻譯內容並加上 AI 提示
    
    Args:
        post: 文章資料，包含 translations 物件
        lang: 社群語言代碼 (如 "zh", "en", "ja")
    
    Returns:
        str: 跳脫後的完整內容
    """
    # 檢查 translations 是否為 null 或空
    translations = post.get("translations")
    if not translations:
        # translations 為 null 或空，直接使用原始 content
        content = post.get("content", "")
        hint = AI_TRANSLATE_HINT.get(lang, AI_TRANSLATE_HINT["en"])
        full_content = content + "\n" + hint
        return escape_markdown_v2(full_content)
    
    # 取得對應的接口語言代碼
    api_lang_code = LANGUAGE_CODE_MAPPING.get(lang, "en_US")
    
    # 從 translations 中取得對應語言內容
    content = translations.get(api_lang_code)
    
    # 如果沒有對應翻譯，fallback 到英文，再 fallback 到原始 content
    if not content:
        content = translations.get("en_US") or post.get("content", "")
    
    # 加上對應語言的 AI 提示，並多一個換行
    hint = AI_TRANSLATE_HINT.get(lang, AI_TRANSLATE_HINT["en"])
    full_content = content + "\n" + hint
    
    return escape_markdown_v2(full_content) 