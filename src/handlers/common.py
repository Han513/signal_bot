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

load_dotenv()
SOCIAL_API = os.getenv("SOCIAL_API")
DISCORD_BOT = os.getenv("DISCORD_BOT")

logger = logging.getLogger(__name__)

async def get_push_targets(trader_uid: str, signal_type: str = "copy") -> list:
    """
    根據 trader_uid 獲取推送目標（固定使用 copy 類型）
    
    Args:
        trader_uid: 交易員UID
        signal_type: 已棄用，固定使用 "copy"
    
    Returns:
        list: [(chat_id, topic_id, jump), ...]
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
        
        push_targets = []
        for social in social_data.get("data", []):
            chat_id = social.get("socialGroup")
            for chat in social.get("chats", []):
                if (
                    chat.get("type") == "copy"
                    and chat.get("enable")
                    and str(chat.get("traderUid")) == str(trader_uid)
                ):
                    topic_id = chat.get("chatId")
                    jump = str(chat.get("jump", "1"))
                    if chat_id and topic_id:
                        push_targets.append((chat_id, int(topic_id), jump))
        
        return push_targets
        
    except Exception as e:
        logger.error(f"獲取推送目標失敗: {e}")
        return []

async def send_telegram_message(bot: Bot, chat_id: int, topic_id: int, 
                              text: str = None, photo_path: str = None, 
                              parse_mode: str = "Markdown") -> bool:
    """
    發送 Telegram 消息
    
    Args:
        bot: Telegram Bot 實例
        chat_id: 群組ID
        topic_id: 主題ID
        text: 文本內容
        photo_path: 圖片路徑
        parse_mode: 解析模式
    
    Returns:
        bool: 發送是否成功
    """
    try:
        if photo_path:
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
        logger.error(f"發送 Telegram 消息失敗: {e}")
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

def generate_trader_summary_image(trader_url, trader_name, pnl_percentage, pnl):
    """
    生成交易員統計圖片
    """
    # 字體設定與尺寸
    number_font_size = 100
    label_font_size = 45
    title_font_size = 70
    avatar_size = 180

    # 背景圖
    bg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'pics', 'copy_trade.png'))
    if os.path.exists(bg_path):
        img = Image.open(bg_path).convert('RGB')
    else:
        img = Image.new('RGB', (1200, 675), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 頭像處理
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(trader_url, timeout=5, headers=headers)
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
    temp_path = "/tmp/trader_summary_full.png"
    img.save(temp_path, quality=95)
    return temp_path

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
    創建異步響應的通用函數
    """
    asyncio.create_task(handle_async_task(task_func, *args, **kwargs))
    return web.json_response({"status": "200", "message": "接收成功，稍後發送"}, status=200) 