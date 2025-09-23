import os
import aiohttp
import asyncio
import logging
from aiogram import Bot
from aiohttp import web
from dotenv import load_dotenv
from aiogram.types import FSInputFile
import aiofiles
import tempfile
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
from datetime import datetime, timezone
import uuid
import hashlib
import time

load_dotenv()
SOCIAL_API = os.getenv("SOCIAL_API")
DISCORD_BOT = os.getenv("DISCORD_BOT")

logger = logging.getLogger(__name__)

# 添加图片生成锁，防止并发冲突
_image_generation_lock = asyncio.Lock()

# 全局去重缓存，用于防止重复推送
_message_dedup_cache = {}
_task_dedup_cache = {}
_message_dedup_lock = asyncio.Lock()

# 清理过期缓存的任务
async def cleanup_dedup_cache():
    """清理过期的去重缓存"""
    current_time = time.time()
    expired_keys = []
    
    # 清理消息缓存（1分钟过期）
    for key, (timestamp, _) in _message_dedup_cache.items():
        if current_time - timestamp > 60:  # 1分钟
            expired_keys.append(key)
    
    for key in expired_keys:
        del _message_dedup_cache[key]
    
    # 清理任务缓存（1分钟过期）
    expired_task_keys = []
    for key, (timestamp, _) in _task_dedup_cache.items():
        if current_time - timestamp > 60:  # 1分钟
            expired_task_keys.append(key)
    
    for key in expired_task_keys:
        del _task_dedup_cache[key]
    
    if expired_keys or expired_task_keys:
        logger.debug(f"清理了 {len(expired_keys)} 个消息缓存和 {len(expired_task_keys)} 个任务缓存")


def _normalize_template_lang(lang_code: str) -> str:
    """將外部語言碼轉為模板語言碼。
    支援：en/zh-CN/zh-TW/ru/id/ja/pt/fr/es/tr/de/it/ar/fa/vi/tl/th/da/pl/ko。
    接受多種變體：下劃線/連字號/區域碼/大小寫混用。
    """
    if not lang_code:
        return 'en'

    raw = str(lang_code).strip()
    # 快速命中常見英文與中文變體
    if raw in ('en', 'en_US', 'en-US', 'en-Us'):
        return 'en'
    if raw in ('zh_CN', 'zh-CN', 'zh-Hans'):
        return 'zh-CN'
    if raw in ('zh_TW', 'zh-TW', 'zh-Hant', 'zh-HK'):
        return 'zh-TW'
    if raw == 'zh':
        return 'zh-CN'

    # 一般規則：取主標籤（語言部分），再做對應
    code = raw.replace('_', '-').lower()
    primary = code.split('-')[0]

    # 舊代碼兼容：印尼語可能為 in 或 id
    if primary == 'in':
        primary = 'id'

    supported = {
        'en', 'ru', 'id', 'ja', 'pt', 'fr', 'es', 'tr', 'de', 'it',
        'ar', 'fa', 'vi', 'tl', 'th', 'da', 'pl', 'ko'
    }
    if primary in supported:
        return primary

    return 'en'

async def get_push_targets(trader_uid: str, signal_type: str = "copy") -> list:
    """
    根據 trader_uid 獲取推送目標（固定使用 copy 類型）
    
    Args:
        trader_uid: 交易員UID
        signal_type: 已棄用，固定使用 "copy"
    
    Returns:
        list: [(chat_id, topic_id, jump, lang), ...] 其中 lang 為標準化模板語言碼
    """
    try:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        payload = {"brand": "BYD", "type": "TELEGRAM"}
        
        async with aiohttp.ClientSession() as session:
            async with session.post(SOCIAL_API, headers=headers, data=payload) as resp:
                if resp.status != 200:
                    logger.error(f"獲取 socials 數據失敗: {resp.status}")
                    return []
                    
                social_data = await resp.json()
        
        def collect(filter_type: str):
            collected = []
            for social in social_data.get("data", []):
                chat_id = social.get("socialGroup")
                group_lang = _normalize_template_lang(social.get("lang"))
                for chat in social.get("chats", []):
                    if (
                        str(chat.get("type", "")).lower() == str(filter_type or "copy").lower()
                        and chat.get("enable")
                        and str(chat.get("traderUid")) == str(trader_uid)
                    ):
                        topic_id = chat.get("chatId")
                        raw_jump = chat.get("jump", "0")
                        if isinstance(raw_jump, bool):
                            jump = "1" if raw_jump else "0"
                        elif isinstance(raw_jump, (int, float)):
                            jump = "1" if int(raw_jump) == 1 else "0"
                        elif isinstance(raw_jump, str):
                            val = raw_jump.strip().lower()
                            jump = "1" if val in {"1", "true", "yes", "y", "on"} else "0"
                        else:
                            jump = "0"
                        if chat_id and topic_id:
                            collected.append((chat_id, int(topic_id), jump, group_lang))
            if collected:
                unique = {}
                for chat_id, topic_id, jump, group_lang in collected:
                    unique[(chat_id, topic_id)] = (jump, group_lang)
                collected = [(cid, tid, jg[0], jg[1]) for (cid, tid), jg in unique.items()]
            return collected

        # 先嘗試指定類型；若為空且不是 copy，再回退 copy
        push_targets = collect(signal_type or "copy")
        if not push_targets and (signal_type or "copy").lower() != "copy":
            logger.info(f"[get_push_targets] 未找到類型 {signal_type}，回退 copy 類型")
            push_targets = collect("copy")

        logger.info(f"[get_push_targets] trader_uid={trader_uid}, type={signal_type}, 命中 {len(push_targets)} 個推送目標")
        return push_targets
        
    except Exception as e:
        logger.error(f"獲取推送目標失敗: {e}")
        return []

async def send_telegram_message(bot: Bot, chat_id: int, topic_id: int, 
                              text: str = None, photo_path: str = None, 
                              parse_mode: str = "Markdown", trader_uid: str = None) -> bool:
    """
    發送 Telegram 消息
    
    Args:
        bot: Telegram Bot 實例
        chat_id: 群組ID
        topic_id: 主題ID
        text: 文本內容
        photo_path: 圖片路徑
        parse_mode: 解析模式
        trader_uid: 交易員UID，用於去重
    
    Returns:
        bool: 發送是否成功
    """
    # 消息去重检查
    if trader_uid and text:
        message_hash = generate_message_hash(trader_uid, text, chat_id, topic_id)
        # 使用帶鎖檢查避免競態
        if await is_duplicate_message_safe(message_hash):
            logger.info(f"跳过重复消息发送: trader_uid={trader_uid}, chat_id={chat_id}, topic_id={topic_id}")
            return True  # 返回True表示"成功"跳过重复消息
    
    max_retries = 2
    retry_delay = 1.0
    
    for attempt in range(max_retries + 1):
        try:
            if photo_path:
                # 驗證圖片文件是否存在且有效
                if not os.path.exists(photo_path):
                    logger.error(f"圖片文件不存在: {photo_path}")
                    return False
                
                if os.path.getsize(photo_path) == 0:
                    logger.error(f"圖片文件為空: {photo_path}")
                    return False
                
                photo = FSInputFile(photo_path)
                await bot.send_photo(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    photo=photo,
                    caption=text,
                    parse_mode=parse_mode
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    text=text,
                    parse_mode=parse_mode
                )
            return True
            
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"發送 Telegram 消息失敗 (嘗試 {attempt + 1}/{max_retries + 1}): {e}")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # 指數退避
            else:
                logger.error(f"發送 Telegram 消息最終失敗: {e}")
                return False
    
    return False

async def send_discord_message(discord_webhook_url: str, data: dict) -> bool:
    """
    發送 Discord 消息
    
    Args:
        discord_webhook_url: Discord webhook URL
        data: 要發送的數據
    
    Returns:
        bool: 發送是否成功
    """
    if not discord_webhook_url:
        return True
        
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(discord_webhook_url, json=data) as resp:
                resp_json = await resp.json()
                logger.info(f"Discord 發送結果: {resp.status} - {resp_json}")
                return resp.status == 200
    except Exception as e:
        logger.error(f"發送 Discord 消息失敗: {e}")
        return False

def format_float(value):
    """
    將數字格式化為最多兩位小數，去除多餘的0
    例如: 1050.00 -> 1050, 12.50 -> 12.5
    """
    try:
        f = round(float(value), 2)
        if f == int(f):
            return str(int(f))
        elif (f * 10) == int(f * 10):
            return f"{f:.1f}"
        else:
            return f"{f:.2f}"
    except Exception:
        return str(value)

def format_timestamp_ms_to_utc(ms_value):
    """
    將毫秒級時間戳轉為 UTC+0 的時間字串 (YYYY-MM-DD HH:MM:SS)
    """
    try:
        ts_int = int(float(ms_value))
        dt = datetime.fromtimestamp(ts_int / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ms_value)

def is_all_english(s):
    """檢查字符串是否全為英文"""
    try:
        s.encode('ascii')
        return True
    except UnicodeEncodeError:
        return False

def generate_message_hash(trader_uid: str, content: str, chat_id: int, topic_id: int) -> str:
    """生成消息的唯一哈希值"""
    message_data = f"{trader_uid}:{content}:{chat_id}:{topic_id}"
    return hashlib.md5(message_data.encode('utf-8')).hexdigest()

def generate_task_hash(func_name: str, *args, **kwargs) -> str:
    """生成任务的唯一哈希值"""
    # 提取关键参数用于去重
    key_parts = [func_name]
    
    # 对于持仓报告，使用 trader_uid 和 infos 内容作为去重键
    if func_name == "process_holding_report_list":
        if args and len(args) > 1:
            data_raw = kwargs.get('data_raw') or args[1] if len(args) > 1 else None
            if data_raw:
                # 提取所有 trader_uid 和 infos 的关键信息
                trader_keys = []
                for trader in data_raw:
                    trader_uid = str(trader.get("trader_uid", ""))
                    infos = trader.get("infos", [])
                    # 为每个 info 生成简化的键
                    info_keys = []
                    for info in infos:
                        info_key = f"{info.get('pair', '')}:{info.get('entry_price', '')}:{info.get('current_price', '')}"
                        info_keys.append(info_key)
                    trader_keys.append(f"{trader_uid}:{','.join(sorted(info_keys))}")
                key_parts.extend(sorted(trader_keys))
    
    task_data = ":".join(str(part) for part in key_parts)
    return hashlib.md5(task_data.encode('utf-8')).hexdigest()

def is_duplicate_message(message_hash: str) -> bool:
    """检查是否为重复消息（非協程上下文，無鎖）。
    如果不存在，會將該 hash 寫入快取，表示已在本輪準備發送。
    有效期為 1 分鐘。
    """
    current_time = time.time()
    
    # 清理过期缓存
    expired_keys = []
    for key, (timestamp, _) in _message_dedup_cache.items():
        if current_time - timestamp > 60:  # 1分钟过期
            expired_keys.append(key)
    
    for key in expired_keys:
        del _message_dedup_cache[key]
    
    # 检查是否已存在
    if message_hash in _message_dedup_cache:
        logger.warning(f"检测到重复消息，跳过发送: {message_hash}")
        return True
    
    # 添加到缓存
    _message_dedup_cache[message_hash] = (current_time, True)
    return False

async def is_duplicate_message_safe(message_hash: str) -> bool:
    """攔截併發的帶鎖版本，避免競態條件。"""
    async with _message_dedup_lock:
        return is_duplicate_message(message_hash)

def has_message_hash(message_hash: str) -> bool:
    """檢查 hash 是否已在快取中（非協程，無副作用）。"""
    return message_hash in _message_dedup_cache

async def has_message_hash_safe(message_hash: str) -> bool:
    """帶鎖檢查 hash 是否存在，用於重試時判斷是否已實際發送成功。"""
    async with _message_dedup_lock:
        return has_message_hash(message_hash)

def is_duplicate_task(task_hash: str) -> bool:
    """检查是否为重复任务"""
    current_time = time.time()
    
    # 清理过期缓存
    expired_keys = []
    for key, (timestamp, _) in _task_dedup_cache.items():
        if current_time - timestamp > 60:  # 1分钟过期
            expired_keys.append(key)
    
    for key in expired_keys:
        del _task_dedup_cache[key]
    
    # 检查是否已存在
    if task_hash in _task_dedup_cache:
        logger.warning(f"检测到重复任务，跳过执行: {task_hash}")
        return True
    
    # 添加到缓存
    _task_dedup_cache[task_hash] = (current_time, True)
    return False

def generate_trader_summary_image(trader_url, trader_name, pnl_percentage, pnl):
    """
    生成交易員統計圖片 - 支持并发安全
    """
    import time
    
    # 重試機制
    max_retries = 2
    retry_delay = 0.5
    
    for attempt in range(max_retries + 1):
        try:
            # 字體設定與尺寸
            number_font_size = 100
            label_font_size = 45
            title_font_size = 70
            avatar_size = 180
            
            # 使用唯一文件名避免衝突
            unique_id = str(uuid.uuid4())[:8]
            temp_path = f"/tmp/trader_summary_full_{unique_id}.png"

            # 背景圖
            bg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'pics', 'copy_trade.png'))
            if os.path.exists(bg_path):
                # 创建背景图的副本，避免并发修改
                img = Image.open(bg_path).convert('RGB').copy()
            else:
                img = Image.new('RGB', (1200, 675), color=(0, 0, 0))
            draw = ImageDraw.Draw(img)

            # 頭像處理
            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                response = requests.get(trader_url, timeout=10, headers=headers)  # 增加超時時間
                response.raise_for_status()
                avatar = Image.open(BytesIO(response.content)).resize((avatar_size, avatar_size)).convert("RGBA")
            except Exception as e:
                logger.warning(f"頭像下載失敗: {e}, 使用預設頭像")
                avatar = Image.new('RGBA', (avatar_size, avatar_size), (200, 200, 200, 255))

            mask = Image.new('L', (avatar_size, avatar_size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)
            avatar.putalpha(mask)
            avatar_x, avatar_y = 100, 150
            img.paste(avatar, (avatar_x, avatar_y), avatar)

            # 字體載入
            font_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'text'))
            bold_font_path = os.path.join(font_dir, 'BRHendrix-Bold-BF6556d1b5459d3.otf')
            noto_bold_font_path = os.path.join(font_dir, 'NotoSansSC-Bold.ttf')

            try:
                number_font = ImageFont.truetype(bold_font_path, number_font_size)
                label_font = ImageFont.truetype(noto_bold_font_path, label_font_size)
                title_font = ImageFont.truetype(
                    bold_font_path if is_all_english(trader_name) else noto_bold_font_path,
                    title_font_size
                )
            except Exception as e:
                logger.warning(f"字體載入失敗: {e}")
                # 使用預設字體
                try:
                    number_font = ImageFont.load_default()
                    label_font = ImageFont.load_default()
                    title_font = ImageFont.load_default()
                except Exception as e2:
                    logger.error(f"連預設字體都無法載入: {e2}")
                    if attempt < max_retries:
                        continue
                    return None

            # 名稱
            name_x = avatar_x + avatar_size + 30
            if is_all_english(trader_name):
                # 英文名往下微調
                name_y = avatar_y + (avatar_size - title_font_size) // 2 + 13
            else:
                # 中文名維持原本
                name_y = avatar_y + (avatar_size - title_font_size) // 2
            draw.text((name_x, name_y), trader_name, font=title_font, fill=(255, 255, 255))

            # 數值處理
            try:
                pnl_perc_value = float(pnl_percentage) * 100
            except Exception:
                pnl_perc_value = 0.0
            is_positive = pnl_perc_value >= 0
            color = (0, 191, 99) if is_positive else (237, 29, 36)

            roi_text = f"{format_float(pnl_perc_value)}%"
            try:
                pnl_val = float(pnl)
            except Exception:
                pnl_val = 0.0
            pnl_text = f"${format_float(abs(pnl_val))}"
            if not is_positive:
                pnl_text = f"-{pnl_text}"

            # ROI & PNL位置（水平對齊）
            roi_x, roi_y = 100, 415
            pnl_x, pnl_y = 550, 415

            draw.text((roi_x, roi_y), roi_text, font=number_font, fill=color)
            draw.text((pnl_x, pnl_y), pnl_text, font=number_font, fill=color)

            draw.text((roi_x, roi_y + number_font_size + 5), "7D ROI", font=label_font, fill=(200, 200, 200))
            draw.text((pnl_x, pnl_y + number_font_size + 5), "7D PNL", font=label_font, fill=(200, 200, 200))

            # 輸出圖片
            try:
                img.save(temp_path, quality=95, format='PNG')
                
                # 清理图像对象
                img.close()
                
                # 驗證生成的圖片文件
                if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    # 添加小延遲確保文件完全寫入
                    time.sleep(0.1)
                    logger.info(f"成功生成交易员统计图片: {temp_path}")
                    return temp_path
                else:
                    logger.error("生成的圖片文件無效或為空")
                    if attempt < max_retries:
                        continue
                    return None
            except Exception as e:
                logger.error(f"保存圖片失敗: {e}")
                if attempt < max_retries:
                    continue
                return None
                
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"圖片生成失敗 (嘗試 {attempt + 1}/{max_retries + 1}): {e}")
                time.sleep(retry_delay)
                retry_delay *= 2  # 指數退避
            else:
                logger.error(f"圖片生成最終失敗: {e}")
                return None
    
    return None

async def generate_trader_summary_image_async(trader_url, trader_name, pnl_percentage, pnl):
    """异步生成交易员统计图片，使用锁确保线程安全"""
    async with _image_generation_lock:
        return generate_trader_summary_image(trader_url, trader_name, pnl_percentage, pnl)

async def cleanup_temp_image(image_path: str):
    """清理临时图片文件"""
    try:
        if image_path and os.path.exists(image_path):
            os.remove(image_path)
            logger.debug(f"已清理临时图片: {image_path}")
    except Exception as e:
        logger.warning(f"清理临时图片失败: {e}")

async def handle_async_task(task_func, *args, **kwargs):
    """
    異步處理任務的通用函數
    """
    try:
        await task_func(*args, **kwargs)
    except Exception as e:
        logger.error(f"異步任務執行失敗: {e}")

def create_async_response(task_func, *args, **kwargs):
    """
    創建異步響應的通用函數，包含去重機制
    """
    # 生成任务哈希值用于去重
    task_hash = generate_task_hash(task_func.__name__, *args, **kwargs)
    
    # 检查是否为重复任务
    if is_duplicate_task(task_hash):
        logger.info(f"跳过重复任务执行: {task_func.__name__}")
        return web.json_response({"status": "200", "message": "任务已存在，跳过重复执行"}, status=200)
    
    # 创建异步任务
    asyncio.create_task(handle_async_task(task_func, *args, **kwargs))
    return web.json_response({"status": "200", "message": "接收成功，稍後發送"}, status=200) 