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

import os
import json
import time
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_I18N_DIR = os.path.join(os.path.dirname(__file__), 'i18n')
_DEFAULT_LANG_FOR_TEMPLATES = 'en'  # 語言包使用 en/zh-TW/zh-CN 簡碼
_DEFAULT_LANG_FOR_ARTICLES = 'en_US'  # 既有文章翻譯函式沿用 en_US

# 簡碼到模板檔名對應
_TEMPLATE_LANG_TO_FILE = {
    'en': 'en.json',
    'zh-TW': 'zh-TW.json',
    'zh-CN': 'zh-CN.json',
    # 擴充其餘語言模板
    'ru': 'ru.json',
    'id': 'id.json',
    'ja': 'ja.json',
    'pt': 'pt.json',
    'fr': 'fr.json',
    'es': 'es.json',
    'tr': 'tr.json',
    'de': 'de.json',
    'it': 'it.json',
    'ar': 'ar.json',
    'fa': 'fa.json',
    'vi': 'vi.json',
    'tl': 'tl.json',
    'th': 'th.json',
    'da': 'da.json',
    'pl': 'pl.json',
    'ko': 'ko.json',
}

# 語言偏好快取（LRU 簡化：使用 dict + TTL）
_language_cache: Dict[str, Dict[str, Any]] = {}
_LANGUAGE_TTL_SECONDS = int(os.getenv('LANGUAGE_CACHE_TTL', '1200'))  # 20 分鐘
_LANGUAGE_API_URL = os.getenv('LANGUAGE_API_URL', '')

# 模板快取
_templates_cache: Dict[str, Dict[str, Any]] = {}


def _now() -> float:
    return time.time()


def _cache_get(cache_key: str) -> Optional[str]:
    item = _language_cache.get(cache_key)
    if not item:
        return None
    if _now() - item['ts'] > _LANGUAGE_TTL_SECONDS:
        _language_cache.pop(cache_key, None)
        return None
    return item['lang']


def _cache_set(cache_key: str, lang: str) -> None:
    _language_cache[cache_key] = {"lang": lang, "ts": _now()}


def _load_templates(lang: str) -> Dict[str, Any]:
    if lang in _templates_cache:
        return _templates_cache[lang]
    filename = _TEMPLATE_LANG_TO_FILE.get(lang)
    if not filename:
        # fallback 到英文
        filename = _TEMPLATE_LANG_TO_FILE['en']
        lang = 'en'
    path = os.path.join(_I18N_DIR, filename)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            _templates_cache[lang] = data
            return data
    except Exception as e:
        logger.warning(f"Load templates failed for {lang}: {e}. Fallback to en.")
        if lang != 'en':
            return _load_templates('en')
        return {}


async def fetch_language_from_api(user_id: Optional[str] = None, chat_id: Optional[str] = None) -> Optional[str]:
    """
    透過外部 API 取得語言，回傳值例：'en', 'zh-TW', 'zh-CN'。
    若失敗或無法取得，回傳 None。
    """
    if not _LANGUAGE_API_URL:
        return None
    try:
        import aiohttp
        params = {}
        if user_id:
            params['user_id'] = user_id
        if chat_id:
            params['chat_id'] = chat_id
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(_LANGUAGE_API_URL, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                lang = data.get('lang') or data.get('language')
                if not lang:
                    return None
                # 使用統一正規化，支援所有語言與變體（en/zh-CN/zh-TW/ru/id/ja/...）
                return _normalize_template_lang_code(str(lang))
    except Exception as e:
        logger.warning(f"fetch_language_from_api failed: {e}")
        return None


async def get_preferred_language(user_id: Optional[str] = None, chat_id: Optional[str] = None, default_lang: str = _DEFAULT_LANG_FOR_TEMPLATES) -> str:
    """
    取得偏好語言（模板用）。先查快取，其次打 API。若失敗，回退 default_lang，再回退 'en'。
    回傳：'en' / 'zh-TW' / 'zh-CN' ...
    """
    cache_key = f"{user_id}:{chat_id}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    lang = await fetch_language_from_api(user_id, chat_id)
    if not lang:
        lang = default_lang or 'en'
    if lang not in _TEMPLATE_LANG_TO_FILE:
        lang = 'en'
    _cache_set(cache_key, lang)
    return lang


def _deep_get(d: Dict[str, Any], key_path: str) -> Optional[str]:
    cur = d
    for seg in key_path.split('.'):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    if isinstance(cur, str):
        return cur
    return None


def render_template(key: str, lang: str, data: Dict[str, Any], fallback_lang: str = _DEFAULT_LANG_FOR_TEMPLATES) -> str:
    """
    渲染模板：先嘗試 lang，缺失則回退 fallback_lang，再回退 'en'。缺變數時以安全替換。
    """
    def coalesce_lang(l: str) -> str:
        return l if l in _TEMPLATE_LANG_TO_FILE else 'en'

    lang = coalesce_lang(lang)
    fallback_lang = coalesce_lang(fallback_lang)

    # 讀取主語言模板
    tpl_main = _load_templates(lang)
    text = _deep_get(tpl_main, key)

    # 若主語言沒有，讀 fallback
    if text is None and fallback_lang != lang:
        tpl_fb = _load_templates(fallback_lang)
        text = _deep_get(tpl_fb, key)

    # 最後回退 en
    if text is None and lang != 'en' and fallback_lang != 'en':
        tpl_en = _load_templates('en')
        text = _deep_get(tpl_en, key)

    if not isinstance(text, str):
        return ""

    # 安全格式化：缺少的鍵以空字串替代
    class SafeDict(dict):
        def __missing__(self, key):
            return ""

    try:
        return text.format_map(SafeDict(data or {}))
    except Exception:
        # 任何格式化錯誤，直接回傳未格式化文本，避免炸裂
        return text


def escape_markdown_v2(text):
    escape_chars = r'_ * [ ] ( ) ~ ` > # + - = | { } . !'.split()
    for ch in escape_chars:
        text = text.replace(ch, '\\' + ch)
    # 確保換行符不被轉義，保持原始換行
    # 在 MarkdownV2 中，換行符需要保持原樣
    return text


def _normalize_template_lang_code(lang: str) -> str:
    """將外部語言簡碼正規化為模板檔案使用的簡碼。
    支援：en/zh-TW/zh-CN/ru/id/ja/pt/fr/es/tr/de/it/ar/fa/vi/tl/th/da/pl/ko。
    """
    if not lang:
        return 'en'
    raw = str(lang).strip()
    if raw in ('en', 'en_US', 'en-US', 'en-Us'):
        return 'en'
    if raw in ('zh_TW', 'zh-TW', 'zh-Hant', 'zh-HK'):
        return 'zh-TW'
    if raw in ('zh_CN', 'zh-CN', 'zh-Hans'):
        return 'zh-CN'
    if raw == 'zh':
        return 'zh-CN'

    code = raw.replace('_', '-').lower()
    primary = code.split('-')[0]
    if primary == 'in':
        primary = 'id'
    supported = {
        'ru','id','ja','pt','fr','es','tr','de','it','ar','fa','vi','tl','th','da','pl','ko'
    }
    if primary in supported:
        return primary
    return 'en'


def localize_pair_side(lang: str, pair_side_code) -> str:
    """將方向 1/2 或 long/short 依語言本地化。
    Args:
        lang: 語言碼（en/zh-TW/zh-CN 等）
        pair_side_code: '1' 或 '2'（也接受 1/2 或 'Long'/'Short'）
    Returns:
        本地化後的方向字串。
    """
    lang_key = _normalize_template_lang_code(lang)
    # 標準化代碼
    val = str(pair_side_code).strip().lower()
    if val in ('1', 'long', 'buy', 'open_long'):
        key = 'long'
    elif val in ('2', 'short', 'sell', 'open_short'):
        key = 'short'
    else:
        key = 'long'

    mapping = {
        'en': {'long': 'Long', 'short': 'Short'},
        'zh-CN': {'long': '多', 'short': '空'},
        'zh-TW': {'long': '多', 'short': '空'},
        'ja': {'long': 'ロング', 'short': 'ショート'},
        'ar': {'long': 'طويل', 'short': 'قصير'},
        'ru': {'long': 'Лонг', 'short': 'Шорт'},
        'id': {'long': 'Long', 'short': 'Pendek'},
        'pt': {'long': 'Longo', 'short': 'Curto'},
        'fr': {'long': 'Long', 'short': 'Court'},
        'es': {'long': 'Largo', 'short': 'Corto'},
        'tr': {'long': 'Uzun', 'short': 'Kısa'},
        'de': {'long': 'Lang', 'short': 'Kurz'},
        'it': {'long': 'Lungo', 'short': 'Corto'},
        'fa': {'long': 'لانگ', 'short': 'شورت'},
        'vi': {'long': 'Dài', 'short': 'Ngắn'},
        'tl': {'long': 'Long', 'short': 'Short'},
        'th': {'long': 'ยาว', 'short': 'สั้น'},
        'da': {'long': 'Lang', 'short': 'Kort'},
        'pl': {'long': 'Długo', 'short': 'Krótko'},
        'ko': {'long': '롱', 'short': '숏'},
    }
    return mapping.get(lang_key, mapping['en'])[key]

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