import os
import aiohttp
import asyncio
import logging
from aiogram import Bot
from aiohttp import web
from dotenv import load_dotenv
from aiogram.types import FSInputFile

from .common import (
    get_push_targets, send_telegram_message, send_discord_message,
    generate_trader_summary_image, format_timestamp_ms_to_utc,
    create_async_response
)

load_dotenv()
DISCORD_BOT_COPY = os.getenv("DISCORD_BOT_COPY")

logger = logging.getLogger(__name__)

async def handle_send_copy_signal(request: web.Request, *, bot: Bot):
    """
    處理 /api/send_copy_signal 介面：
    1. 先同步驗證輸入資料，失敗直接回傳 400。
    2. 成功則立即回 200，並將實際推送工作交由背景協程處理。
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
        validate_copy_signal(data)
    except ValueError as err:
        return web.json_response({"status": "400", "message": str(err)}, status=400)

    # 背景處理，不阻塞 HTTP 回應
    return create_async_response(process_copy_signal, data, bot)

def validate_copy_signal(data: dict) -> None:
    """驗證 copy signal 請求資料，失敗時拋出 ValueError。"""

    required_fields = {
        "trader_uid", "trader_name", "trader_pnl", "trader_pnlpercentage",
        "trader_detail_url", "pair", "base_coin", "quote_coin",
        "pair_leverage", "pair_type", "price", "time", "trader_url",
        "pair_side", "pair_margin_type"
    }

    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        raise ValueError(f"缺少欄位: {', '.join(missing)}")

    # 數值與類型檢查
    try:
        pnl = float(data["trader_pnl"])
        pnl_perc = float(data["trader_pnlpercentage"])
        float(data["pair_leverage"])
    except (TypeError, ValueError):
        raise ValueError("trader_pnlpercentage / pair_leverage / trader_pnl 必須為數字格式")

    # 正負號須一致
    if (pnl >= 0) ^ (pnl_perc >= 0):
        raise ValueError("trader_pnl 與 trader_pnlpercentage 正負號不一致")

    if data["pair_type"] not in {"buy", "sell"}:
        raise ValueError("pair_type 只能是 'buy' 或 'sell'")

    # pair_side 必須為 1 或 2（字串或數字）
    if str(data["pair_side"]) not in {"1", "2"}:
        raise ValueError("pair_side 只能是 '1'(Long) 或 '2'(Short)")

    # pair_margin_type 必須為 1 或 2（字串或數字）
    if str(data["pair_margin_type"]) not in {"1", "2"}:
        raise ValueError("pair_margin_type 只能是 '1'(Cross) 或 '2'(Isolated)")

    # time 欄位必須為毫秒級時間戳（13 位數/大於等於 1e12）
    try:
        ts_val = int(float(data["time"]))
    except (TypeError, ValueError):
        raise ValueError("time 必須為毫秒級時間戳 (數字格式)")

    # 檢查是否可能為秒級時間戳（10 位數），若是則判定錯誤
    if ts_val < 10**12:
        raise ValueError("time 必須為毫秒級時間戳 (13 位)")

async def process_copy_signal(data: dict, bot: Bot) -> None:
    """背景協程：查詢推送目標、產圖並發送訊息。"""
    try:
        trader_uid = str(data["trader_uid"])

        # 獲取推送目標
        push_targets = await get_push_targets(trader_uid)

        if not push_targets:
            logger.warning(f"未找到符合條件的推送頻道: {trader_uid}")
            return

        # 產生交易員統計圖片
        # img_path = generate_trader_summary_image(
        #     data["trader_url"],
        #     data["trader_name"],
        #     data["trader_pnlpercentage"],
        #     data["trader_pnl"],
        # )
        # if not img_path:
        #     logger.warning("圖片生成失敗，取消推送")
        #     return

        # 將毫秒級時間戳轉為 UTC+0 可讀格式
        formatted_time = format_timestamp_ms_to_utc(data.get('time'))

        # 文案映射
        pair_type_map = {"buy": "Open", "sell": "Close"}
        pair_side_map = {"1": "Long", "2": "Short", 1: "Long", 2: "Short"}
        margin_type_map = {"1": "Cross", "2": "Isolated", 1: "Cross", 2: "Isolated"}

        # 準備發送任務
        tasks = []
        for chat_id, topic_id, jump in push_targets:
            # 取得映射值
            pair_type_str = pair_type_map.get(str(data.get("pair_type", "")).lower(), str(data.get("pair_type", "")))
            pair_side_str = pair_side_map.get(str(data.get("pair_side", "")), str(data.get("pair_side", "")))
            margin_type_str = margin_type_map.get(str(data.get("pair_margin_type", "")), str(data.get("pair_margin_type", "")))

            caption = (
                f"⚡️**{data['trader_name']}** New Trade Open\n\n"
                f"📢{data['pair']} {margin_type_str} {data['pair_leverage']}X\n\n"
                f"⏰Time: {formatted_time} (UTC+0)\n"
                f"➡️Direction: {pair_type_str} {pair_side_str}\n"
                f"🎯Entry Price: ${data['price']}"
            )
            
            if jump == "1":
                # 使用 Markdown 格式創建可點擊的超連結
                trader_name = data.get('trader_name', 'Trader')
                detail_url = data.get('trader_detail_url', '')
                caption += f"\n\n[About {trader_name}, more actions>>]({detail_url})"
            
            tasks.append(
                send_telegram_message(
                    bot=bot,
                    chat_id=chat_id,
                    topic_id=topic_id,
                    text=caption,
                    # photo_path=img_path,
                    parse_mode="Markdown"
                )
            )

        # 等待 Telegram 發送結果
        await asyncio.gather(*tasks, return_exceptions=True)

        # 同步發送至 Discord Bot
        if DISCORD_BOT_COPY:
            await send_discord_message(DISCORD_BOT_COPY, data)

    except Exception as e:
        logger.error(f"推送 copy signal 失敗: {e}") 