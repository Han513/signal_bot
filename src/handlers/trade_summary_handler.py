import os
import asyncio
import logging
import uuid
import tempfile
from aiogram import Bot
from aiohttp import web
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from .common import (
    get_push_targets, send_telegram_message, send_discord_message,
    format_float, format_timestamp_ms_to_utc, create_async_response
)
from multilingual_utils import get_preferred_language, render_template

load_dotenv()
DISCORD_BOT_SUMMARY = os.getenv("DISCORD_BOT_SUMMARY")

logger = logging.getLogger(__name__)

# 添加图片生成锁，防止并发冲突
_image_generation_lock = asyncio.Lock()

async def handle_trade_summary(request: web.Request, *, bot: Bot):
    """
    處理 /api/signal/completed_trade 介面：發送已完成交易總結
    """
    # Content-Type 檢查
    if request.content_type != "application/json":
        return web.json_response({"status": "400", "message": "Content-Type must be application/json"}, status=400)

    # 解析 JSON
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "400", "message": "Invalid JSON body"}, status=400)

    # 資料驗證
    try:
        validate_trade_summary(data)
    except ValueError as err:
        return web.json_response({"status": "400", "message": str(err)}, status=400)

    # 背景處理，不阻塞 HTTP 回應
    return create_async_response(process_trade_summary, data, bot)

def validate_trade_summary(data: dict) -> None:
    """驗證交易總結請求資料，失敗時拋出 ValueError。"""
    required_fields = {
        "trader_uid", "trader_name", "trader_detail_url", "pair", "pair_side",
        "pair_margin_type", "pair_leverage", "entry_price", "exit_price",
        "realized_pnl", "realized_pnl_percentage", "close_time"
    }

    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        raise ValueError(f"缺少欄位: {', '.join(missing)}")

    # 檢查 pair_side
    if str(data["pair_side"]) not in {"1", "2"}:
        raise ValueError("pair_side 只能是 '1'(Long) 或 '2'(Short)")

    # 檢查 pair_margin_type
    if str(data["pair_margin_type"]) not in {"1", "2"}:
        raise ValueError("pair_margin_type 只能是 '1'(Cross) 或 '2'(Isolated)")

    # 數值檢查
    try:
        float(data["entry_price"])
        float(data["exit_price"])
        float(data["realized_pnl"])
        float(data["realized_pnl_percentage"])
        float(data["pair_leverage"])
    except (TypeError, ValueError):
        raise ValueError("數值欄位必須為正確的數字格式")

    # time 欄位檢查
    try:
        ts_val = int(float(data["close_time"]))
        if ts_val < 10**12:
            raise ValueError("close_time 必須為毫秒級時間戳 (13 位)")
    except (TypeError, ValueError):
        raise ValueError("close_time 必須為毫秒級時間戳 (數字格式)")

def format_trade_summary_text(data: dict, include_link: bool = True) -> str:
    """格式化交易總結文本"""
    # 文案映射
    pair_side_map = {"1": "Long", "2": "Short", 1: "Long", 2: "Short"}
    margin_type_map = {"1": "Cross", "2": "Isolated", 1: "Cross", 2: "Isolated"}
    
    pair_side = pair_side_map.get(str(data.get("pair_side", "")), str(data.get("pair_side", "")))
    margin_type = margin_type_map.get(str(data.get("pair_margin_type", "")), str(data.get("pair_margin_type", "")))
    
    # 格式化數值
    entry_price = str(data.get("entry_price", 0))
    exit_price = str(data.get("exit_price", 0))
    realized_pnl = format_float(float(data.get("realized_pnl_percentage", 0)) * 100)
    leverage = format_float(data.get("pair_leverage", 0))
    
    # 格式化時間
    formatted_time = format_timestamp_ms_to_utc(data.get('close_time'))
    
    text = (
        f"⚡️{data.get('trader_name', 'Trader')} Close Position\n\n"
        f"📢{data.get('pair', '')} {margin_type} {leverage}X\n"
        f"⏰Time: {formatted_time} (UTC+0)\n"
        f"➡️Direction: Close {pair_side}\n"
        f"🙌🏻ROI: {realized_pnl}%\n"
        f"🎯Entry Price: ${entry_price}\n"
        f"💰Exit Price: ${exit_price}"
    )
    
    if include_link:
        # 使用 Markdown 格式創建可點擊的超連結
        trader_name = data.get('trader_name', 'Trader')
        detail_url = data.get('trader_detail_url', '')
        text += f"\n\n[About {trader_name}, more actions>>]({detail_url})"
    
    return text

def generate_trade_summary_image(data: dict) -> str:
    """生成交易總結圖片 - 配合新背景圖格式，支持并发安全"""
    try:
        # 生成唯一的临时文件路径，避免并发冲突
        unique_id = str(uuid.uuid4())[:8]
        temp_path = f"/tmp/trade_summary_{unique_id}.png"
        
        # 載入背景圖
        bg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'pics', 'trade_summary.png'))
        if os.path.exists(bg_path):
            # 创建背景图的副本，避免并发修改
            img = Image.open(bg_path).convert('RGB').copy()
        else:
            # 如果背景圖不存在，創建預設背景
            img = Image.new('RGB', (1200, 675), color=(40, 40, 40))
        
        draw = ImageDraw.Draw(img)
        
        # 載入字體
        font_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'text'))
        bold_font_path = os.path.join(font_dir, 'BRHendrix-Bold-BF6556d1b5459d3.otf')
        medium_font_path = os.path.join(font_dir, 'BRHendrix-Medium-BF6556d1b4e12b2.otf')
        noto_bold_font_path = os.path.join(font_dir, 'NotoSansSC-Bold.ttf')
        
        try:
            # 大字體用於主要數值
            large_font = ImageFont.truetype(bold_font_path, 110)
            # 中等字體用於標籤
            medium_font = ImageFont.truetype(noto_bold_font_path, 53)
            # 小字體用於其他信息
            small_font = ImageFont.truetype(noto_bold_font_path, 35)
        except Exception as e:
            logger.warning(f"字體載入失敗: {e}")
            return None
        
        # 格式化數值
        realized_pnl = format_float(float(data.get("realized_pnl_percentage", 0)) * 100)
        entry_price = str(data.get("entry_price", 0))
        exit_price = str(data.get("exit_price", 0))
        leverage = format_float(data.get("pair_leverage", 0))
        
        # 判斷盈虧顏色
        is_positive = float(data.get("realized_pnl_percentage", 0)) >= 0
        pnl_color = (0, 191, 99) if is_positive else (237, 29, 36)  # 綠色或紅色
        
        # 判斷交易方向顏色
        is_long = str(data.get("pair_side", "")) == "1"
        direction_color = (0, 191, 99) if is_long else (237, 29, 36)  # Long用綠色，Short用紅色
        
        # 在背景圖上填充數值到對應位置
        # 根據第二張照片的風格調整位置，增加間距並靠左
        
        # 交易對標題 (頂部)
        pair_text = f"{data.get('pair', '')} Perpetual"
        draw.text((80, 70), pair_text, font=medium_font, fill=(255, 255, 255))
        
        # 槓桿信息 (交易對下方) - 根據方向設置顏色
        pair_side_map = {"1": "Long", "2": "Short", 1: "Long", 2: "Short"}
        pair_side = pair_side_map.get(str(data.get("pair_side", "")), str(data.get("pair_side", "")))
        leverage_text = f"{pair_side} {leverage}X"
        draw.text((80, 140), leverage_text, font=small_font, fill=direction_color)
        
        # Cumulative ROI 標籤
        draw.text((80, 265), "Cumulative ROI", font=medium_font, fill=(200, 200, 200))
        
        # ROI 數值 (主要顯示，在標籤下方) - 根據盈虧設置顏色
        roi_text = f"{realized_pnl}%"
        draw.text((80, 340), roi_text, font=large_font, fill=pnl_color)
        
        # 價格信息 (底部) - 分開繪製標籤和數值
        # Exit Price 標籤和數值 (在上方)
        draw.text((80, 500), "Exit Price", font=small_font, fill=(200, 200, 200))
        draw.text((290, 500), exit_price, font=small_font, fill=(255, 255, 255))
        
        # Entry Price 標籤和數值 (在下方)
        draw.text((80, 560), "Entry Price", font=small_font, fill=(200, 200, 200))
        draw.text((290, 560), entry_price, font=small_font, fill=(255, 255, 255))
        
        # 保存圖片到唯一路径
        img.save(temp_path, quality=95)
        
        # 清理图像对象
        img.close()
        
        logger.info(f"成功生成交易总结图片: {temp_path}")
        return temp_path
        
    except Exception as e:
        logger.error(f"生成交易總結圖片失敗: {e}")
        return None

async def generate_trade_summary_image_async(data: dict) -> str:
    """异步生成交易总结图片，使用锁确保线程安全"""
    async with _image_generation_lock:
        return generate_trade_summary_image(data)

async def cleanup_temp_image(image_path: str):
    """清理临时图片文件"""
    try:
        if image_path and os.path.exists(image_path):
            os.remove(image_path)
            logger.debug(f"已清理临时图片: {image_path}")
    except Exception as e:
        logger.warning(f"清理临时图片失败: {e}")

async def process_trade_summary(data: dict, bot: Bot) -> None:
    """背景協程：處理交易總結推送"""
    img_path = None
    try:
        trader_uid = str(data["trader_uid"])

        # 獲取推送目標
        push_targets = await get_push_targets(trader_uid)

        if not push_targets:
            logger.warning(f"未找到符合條件的交易總結推送頻道: {trader_uid}")
            return

        # 异步生成交易總結圖片，使用锁确保线程安全
        img_path = await generate_trade_summary_image_async(data)
        if not img_path:
            logger.warning("交易總結圖片生成失敗，取消推送")
            return

        # 準備發送任務（以 (chat_id, topic_id) 去重）
        tasks = []
        seen = set()
        for chat_id, topic_id, jump, group_lang in push_targets:
            key = (chat_id, topic_id)
            if key in seen:
                continue
            seen.add(key)
            include_link = (jump == "1")

            # 語言
            api_lang = await get_preferred_language(user_id=None, chat_id=str(chat_id))
            lang = group_lang or api_lang or 'en'
            logger.info(f"[i18n] trade_summary chat_id={chat_id}, topic_id={topic_id}, group_lang={group_lang}, api_lang={api_lang}, resolved={lang}")

            # 文案映射與數值
            pair_side_map = {"1": "Long", "2": "Short", 1: "Long", 2: "Short"}
            margin_type_map = {"1": "Cross", "2": "Isolated", 1: "Cross", 2: "Isolated"}
            pair_side = pair_side_map.get(str(data.get("pair_side", "")), str(data.get("pair_side", "")))
            margin_type = margin_type_map.get(str(data.get("pair_margin_type", "")), str(data.get("pair_margin_type", "")))
            formatted_time = format_timestamp_ms_to_utc(data.get('close_time'))

            tpl = {
                "trader_name": data.get('trader_name', 'Trader'),
                "pair": data.get('pair', ''),
                "pair_side": pair_side,
                "margin_type": margin_type,
                "leverage": format_float(data.get('pair_leverage', 0)),
                "formatted_time": formatted_time,
                "realized_pnl": format_float(float(data.get('realized_pnl_percentage', 0)) * 100),
                "entry_price": str(data.get('entry_price', 0)),
                "exit_price": str(data.get('exit_price', 0)),
                "trader_detail_url": data.get('trader_detail_url', ''),
            }

            text = render_template("trade.close.body", lang, tpl, fallback_lang='en') or (
                f"⚡️{tpl['trader_name']} Close Position\n\n"
                f"📢{tpl['pair']} {tpl['margin_type']} {tpl['leverage']}X\n"
                f"⏰Time: {tpl['formatted_time']} (UTC+0)\n"
                f"➡️Direction: Close {tpl['pair_side']}\n"
                f"🙌🏻ROI: {tpl['realized_pnl']}%\n"
                f"🎯Entry Price: ${tpl['entry_price']}\n"
                f"💰Exit Price: ${tpl['exit_price']}"
            )

            if include_link:
                more = render_template("copy.open.more", lang, {
                    "trader_name": tpl['trader_name'],
                    "detail_url": tpl['trader_detail_url']
                }, fallback_lang='en') or f"[About {tpl['trader_name']}, more actions>>]({tpl['trader_detail_url']})"
                text += f"\n\n{more}"
            
            tasks.append(
                send_telegram_message(
                    bot=bot,
                    chat_id=chat_id,
                    topic_id=topic_id,
                    text=text,
                    photo_path=img_path,
                    parse_mode="Markdown",
                    trader_uid=trader_uid
                )
            )

        # 等待 Telegram 發送結果
        await asyncio.gather(*tasks, return_exceptions=True)

        # 同步發送至 Discord Bot
        if DISCORD_BOT_SUMMARY:
            try:
                first_chat_id, _, _, first_group_lang = push_targets[0]
                first_api_lang = await get_preferred_language(user_id=None, chat_id=str(first_chat_id))
                discord_lang = first_group_lang or first_api_lang or 'en'
            except Exception:
                discord_lang = 'en'
            payload = dict(data)
            payload["lang"] = discord_lang
            await send_discord_message(DISCORD_BOT_SUMMARY, payload)

    except Exception as e:
        logger.error(f"推送交易總結失敗: {e}")
    finally:
        # 清理临时图片文件
        if img_path:
            await cleanup_temp_image(img_path) 
