import os
import asyncio
import logging
from aiogram import Bot
from aiohttp import web
from dotenv import load_dotenv

from .common import (
    get_push_targets, send_telegram_message, send_discord_message,
    format_float, create_async_response
)

load_dotenv()
DISCORD_BOT_HOLDING = os.getenv("DISCORD_BOT_HOLDING")

logger = logging.getLogger(__name__)

async def handle_holding_report(request: web.Request, *, bot: Bot):
    """
    處理 /api/report/holdings 介面：發送持倉報告
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
        validate_holding_report(data)
    except ValueError as err:
        return web.json_response({"status": "400", "message": str(err)}, status=400)

    # 背景處理，不阻塞 HTTP 回應
    return create_async_response(process_holding_report, data, bot)

def validate_holding_report(data: dict) -> None:
    """驗證持倉報告請求資料，失敗時拋出 ValueError。"""
    required_fields = {
        "trader_uid", "trader_name", "trader_detail_url", "pair", "pair_side",
        "pair_margin_type", "pair_leverage", "entry_price", "current_price",
        "unrealized_pnl_percentage"
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
        float(data["current_price"])
        float(data["unrealized_pnl_percentage"])
        float(data["pair_leverage"])
        # 檢查可選的止盈止損價格
        if data.get("tp_price"):
            float(data["tp_price"])
        if data.get("sl_price"):
            float(data["sl_price"])
    except (TypeError, ValueError):
        raise ValueError("數值欄位必須為數字格式")

async def process_holding_report(data: dict, bot: Bot) -> None:
    """背景協程：處理持倉報告推送"""
    try:
        trader_uid = str(data["trader_uid"])

        # 獲取推送目標
        push_targets = await get_push_targets(trader_uid)

        if not push_targets:
            logger.warning(f"未找到符合條件的持倉報告推送頻道: {trader_uid}")
            return

        # 準備發送任務
        tasks = []
        for chat_id, topic_id, jump in push_targets:
            text = format_holding_report_text(data, jump == "1")
            
            tasks.append(
                send_telegram_message(
                    bot=bot,
                    chat_id=chat_id,
                    topic_id=topic_id,
                    text=text,
                    parse_mode="Markdown"
                )
            )

        # 等待 Telegram 發送結果
        await asyncio.gather(*tasks, return_exceptions=True)

        # 同步發送至 Discord Bot
        if DISCORD_BOT_HOLDING:
            await send_discord_message(DISCORD_BOT_HOLDING, data)

    except Exception as e:
        logger.error(f"推送持倉報告失敗: {e}")

def format_holding_report_text(data: dict, include_link: bool = True) -> str:
    """格式化持倉報告文本"""
    # 文案映射
    pair_side_map = {"1": "Long", "2": "Short", 1: "Long", 2: "Short"}
    margin_type_map = {"1": "Cross", "2": "Isolated", 1: "Cross", 2: "Isolated"}
    
    pair_side = pair_side_map.get(str(data.get("pair_side", "")), str(data.get("pair_side", "")))
    margin_type = margin_type_map.get(str(data.get("pair_margin_type", "")), str(data.get("pair_margin_type", "")))
    
    # 格式化數值
    entry_price = format_float(data.get("entry_price", 0))
    current_price = format_float(data.get("current_price", 0))
    roi = format_float(data.get("unrealized_pnl_percentage", 0))
    leverage = format_float(data.get("pair_leverage", 0))
    
    # 判斷是否有設置止盈止損
    has_tp = bool(data.get("tp_price"))
    has_sl = bool(data.get("sl_price"))
    
    text = (
        f"⚡️{data.get('trader_name', 'Trader')} Trading Summary (Updated every 2 hours)\n\n"
        f"📢{data.get('pair', '')} {margin_type} {leverage}X\n"
        f"➡️Direction: {pair_side}\n"
        f"🎯Entry Price: ${entry_price}\n"
        f"📊Current Price: ${current_price}\n"
        f"🚀ROI: {roi}%"
    )
    
    # 如果有設置止盈止損，添加相關信息
    tp_sl_lines = []
    if has_tp:
        tp_price = format_float(data.get("tp_price", 0))
        tp_sl_lines.append(f"✅TP Price: ${tp_price}")
    if has_sl:
        sl_price = format_float(data.get("sl_price", 0))
        tp_sl_lines.append(f"🛑SL Price: ${sl_price}")
    
    if tp_sl_lines:
        text += "\n" + "\n".join(tp_sl_lines)
    
    if include_link:
        # 使用 Markdown 格式創建可點擊的超連結
        trader_name = data.get('trader_name', 'Trader')
        detail_url = data.get('trader_detail_url', '')
        text += f"\n\n[About {trader_name}, more actions>>]({detail_url})"
    
    return text

 