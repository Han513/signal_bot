import os
import asyncio
import logging
from aiogram import Bot
from aiohttp import web
from dotenv import load_dotenv

from .common import (
    get_push_targets, send_telegram_message, send_discord_message,
    format_timestamp_ms_to_utc, format_float, create_async_response
)
from multilingual_utils import get_preferred_language, render_template, localize_pair_side

load_dotenv()
DISCORD_BOT_SCALP = os.getenv("DISCORD_BOT_SCALP")

logger = logging.getLogger(__name__)

async def handle_scalp_update(request: web.Request, *, bot: Bot):
    """
    處理 /api/signal/scalp_update 介面：發送止盈止損更新
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
        validate_scalp_update(data)
    except ValueError as err:
        return web.json_response({"status": "400", "message": str(err)}, status=400)

    # 背景處理，不阻塞 HTTP 回應
    return create_async_response(process_scalp_update, data, bot)

def validate_scalp_update(data: dict) -> None:
    """驗證止盈止損更新請求資料，失敗時拋出 ValueError。"""
    required_fields = {
        "trader_uid", "trader_name", "trader_detail_url", "pair", "pair_side",
        "time"
    }

    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        raise ValueError(f"缺少欄位: {', '.join(missing)}")

    # 檢查 pair_side
    if str(data["pair_side"]) not in {"1", "2"}:
        raise ValueError("pair_side 只能是 '1'(Long) 或 '2'(Short)")

    # 檢查是否為設置或更新操作
    has_tp_price = data.get("tp_price") is not None
    has_sl_price = data.get("sl_price") is not None
    
    if not has_tp_price and not has_sl_price:
        raise ValueError("至少需要提供 tp_price 或 sl_price 其中之一")

    # 數值檢查
    try:
        if has_tp_price:
            float(data["tp_price"])
        if has_sl_price:
            float(data["sl_price"])
        # 檢查 previous 價格（可選）
        if data.get("previous_tp_price"):
            float(data["previous_tp_price"]) 
        if data.get("previous_sl_price"):
            float(data["previous_sl_price"]) 
    except (TypeError, ValueError):
        raise ValueError("價格欄位必須為數字格式")

    # time 欄位檢查
    try:
        ts_val = int(float(data["time"]))
        if ts_val < 10**12:
            raise ValueError("time 必須為毫秒級時間戳 (13 位)")
    except (TypeError, ValueError):
        raise ValueError("time 必須為毫秒級時間戳 (數字格式)")

async def process_scalp_update(data: dict, bot: Bot) -> None:
    """背景協程：處理止盈止損更新推送"""
    try:
        trader_uid = str(data["trader_uid"])

        # 獲取推送目標
        push_targets = await get_push_targets(trader_uid)

        if not push_targets:
            logger.warning(f"未找到符合條件的止盈止損推送頻道: {trader_uid}")
            return

        # 格式化時間
        formatted_time = format_timestamp_ms_to_utc(data.get('time'))

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
            logger.info(f"[i18n] scalp chat_id={chat_id}, topic_id={topic_id}, group_lang={group_lang}, api_lang={api_lang}, resolved={lang}")

            # 文案映射
            pair_side = localize_pair_side(lang, data.get("pair_side", ""))

            # 判斷是否為更新操作
            has_previous_tp = bool(data.get("previous_tp_price"))
            has_previous_sl = bool(data.get("previous_sl_price"))
            is_update = has_previous_tp or has_previous_sl

            tpl = {
                "trader_name": data.get('trader_name', 'Trader'),
                "pair": data.get('pair', ''),
                "pair_side": pair_side,
                "formatted_time": formatted_time,
                "tp_price": str(data.get('tp_price', '')) if data.get('tp_price') else '',
                "sl_price": str(data.get('sl_price', '')) if data.get('sl_price') else '',
                "previous_tp_price": str(data.get('previous_tp_price', '')) if data.get('previous_tp_price') else '',
                "previous_sl_price": str(data.get('previous_sl_price', '')) if data.get('previous_sl_price') else '',
                "trader_detail_url": data.get('trader_detail_url', ''),
            }

            if is_update:
                header = render_template("scalp.tp_sl.update_header", lang, tpl, fallback_lang='en') or (
                    f"⚡️{tpl['trader_name']} TP/SL Update\n\n"
                    f"📢{tpl['pair']} {tpl['pair_side']}\n"
                    f"⏰Time: {tpl['formatted_time']} (UTC+0)"
                )
                lines = [header]
                if tpl['tp_price'] and tpl['previous_tp_price']:
                    lines.append(render_template("scalp.tp_sl.tp_update_line", lang, tpl, fallback_lang='en') or f"✅TP Price: ${tpl['previous_tp_price']} change to ${tpl['tp_price']}")
                elif tpl['tp_price']:
                    lines.append(render_template("scalp.tp_sl.tp_set_line", lang, tpl, fallback_lang='en') or f"✅TP Price: ${tpl['tp_price']}")
                if tpl['sl_price'] and tpl['previous_sl_price']:
                    lines.append(render_template("scalp.tp_sl.sl_update_line", lang, tpl, fallback_lang='en') or f"🛑SL Price: ${tpl['previous_sl_price']} change to ${tpl['sl_price']}")
                elif tpl['sl_price']:
                    lines.append(render_template("scalp.tp_sl.sl_set_line", lang, tpl, fallback_lang='en') or f"🛑SL Price: ${tpl['sl_price']}")
                text = "\n".join(lines)
            else:
                text = render_template("scalp.tp_sl.body", lang, tpl, fallback_lang='en') or (
                    f"⚡️{tpl['trader_name']} TP/SL Setting\n\n"
                    f"📢{tpl['pair']} {tpl['pair_side']}\n"
                    f"⏰Time: {tpl['formatted_time']} (UTC+0)"
                )
                setting_lines = []
                if tpl['tp_price']:
                    setting_lines.append(render_template("scalp.tp_sl.tp_set_line", lang, tpl, fallback_lang='en') or f"✅TP Price: ${tpl['tp_price']}")
                if tpl['sl_price']:
                    setting_lines.append(render_template("scalp.tp_sl.sl_set_line", lang, tpl, fallback_lang='en') or f"🛑SL Price: ${tpl['sl_price']}")
                if setting_lines:
                    text += "\n" + "\n".join(setting_lines)

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
                    parse_mode="Markdown",
                    trader_uid=trader_uid
                )
            )

        # 等待 Telegram 發送結果
        await asyncio.gather(*tasks, return_exceptions=True)

        # 同步發送至 Discord Bot
        if DISCORD_BOT_SCALP:
            await send_discord_message(DISCORD_BOT_SCALP, dict(data))

    except Exception as e:
        logger.error(f"推送止盈止損更新失敗: {e}")


def format_scalp_update_text(data: dict, formatted_time: str, include_link: bool = True) -> str:
    """格式化止盈止損更新文本"""
    # 文案映射
    pair_side = localize_pair_side('en', data.get("pair_side", ""))
    
    # 判斷是否為更新操作（有 previous 價格）
    has_previous_tp = bool(data.get("previous_tp_price"))
    has_previous_sl = bool(data.get("previous_sl_price"))
    is_update = has_previous_tp or has_previous_sl
    
    # 格式化價格
    tp_price = str(data.get("tp_price", "")) if data.get("tp_price") else ""
    sl_price = str(data.get("sl_price", "")) if data.get("sl_price") else ""
    previous_tp_price = str(data.get("previous_tp_price", "")) if data.get("previous_tp_price") else ""
    previous_sl_price = str(data.get("previous_sl_price", "")) if data.get("previous_sl_price") else ""
    
    if is_update:
        # 更新操作文案
        text = (
            f"⚡️{data.get('trader_name', 'Trader')} TP/SL Update\n\n"
            f"📢{data.get('pair', '')} {pair_side}\n"
            f"⏰Time: {formatted_time} (UTC+0)"
        )
        
        # 收集 TP/SL 更新行
        update_lines = []
        if tp_price and previous_tp_price:
            update_lines.append(f"✅TP Price: ${previous_tp_price} change to ${tp_price}")
        elif tp_price:
            update_lines.append(f"✅TP Price: ${tp_price}")
        
        if sl_price and previous_sl_price:
            update_lines.append(f"🛑SL Price: ${previous_sl_price} change to ${sl_price}")
        elif sl_price:
            update_lines.append(f"🛑SL Price: ${sl_price}")
        
        if update_lines:
            text += "\n" + "\n".join(update_lines)
    else:
        # 設置操作文案
        text = (
            f"⚡️{data.get('trader_name', 'Trader')} TP/SL Setting\n\n"
            f"📢{data.get('pair', '')} {pair_side}\n"
            f"⏰Time: {formatted_time} (UTC+0)"
        )
        
        # 收集 TP/SL 設置行
        setting_lines = []
        if tp_price:
            setting_lines.append(f"✅TP Price: ${tp_price}")
        if sl_price:
            setting_lines.append(f"🛑SL Price: ${sl_price}")
        
        if setting_lines:
            text += "\n" + "\n".join(setting_lines)
    
    if include_link:
        # 使用 Markdown 格式創建可點擊的超連結
        trader_name = data.get('trader_name', 'Trader')
        detail_url = data.get('trader_detail_url', '')
        text += f"\n\n[About {trader_name}, more actions>>]({detail_url})"
    
    return text 