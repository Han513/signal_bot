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
from multilingual_utils import get_preferred_language, render_template, localize_pair_side


def flatten_holding_report_data(data):
    """
    將帶有 infos 的分組結構展平成單一持倉列表
    """
    if isinstance(data, list):
        flat = []
        for group in data:
            # group 是交易員字典
            base = {k: v for k, v in group.items() if k != "infos"}
            infos = group.get("infos", [])
            for info in infos:
                merged = {**base, **info}
                flat.append(merged)
        return flat if flat else data  # 若沒有 infos 則返回原始
    elif isinstance(data, dict) and "infos" in data:
        base = {k: v for k, v in data.items() if k != "infos"}
        flat = []
        for info in data["infos"]:
            merged = {**base, **info}
            flat.append(merged)
        return flat if flat else data
    else:
        return data

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
        data_raw = await request.json()
        logger.info(f"[持倉報告] 收到請求內容: {data_raw}")
    except Exception:
        return web.json_response({"status": "400", "message": "Invalid JSON body"}, status=400)

    # 展平數據，兼容 infos 分組格式（仅用于Telegram推送）
    data = flatten_holding_report_data(data_raw)

    # 資料驗證
    try:
        validate_holding_report(data_raw)
    except ValueError as err:
        return web.json_response({"status": "400", "message": str(err)}, status=400)

    # 背景處理，不阻塞 HTTP 回應
    return create_async_response(process_holding_report_list, data, bot, data_raw=data_raw)

def validate_holding_report(data) -> None:
    """支持批量trader+infos结构的校验"""
    if isinstance(data, list):
        if not data:
            raise ValueError("列表不能為空")
        for i, trader in enumerate(data):
            if not isinstance(trader, dict):
                raise ValueError(f"列表項目 {i} 必須為字典格式，收到: {type(trader)}")
            # 校验trader主字段
            required_fields = {"trader_uid", "trader_name", "trader_detail_url"}
            missing = [f for f in required_fields if not trader.get(f)]
            if missing:
                raise ValueError(f"trader {i} 缺少欄位: {', '.join(missing)}")
            # 校验infos
            infos = trader.get("infos")
            if not infos or not isinstance(infos, list):
                raise ValueError(f"trader {i} 缺少infos或格式錯誤")
            for j, info in enumerate(infos):
                validate_single_holding_report(info, f"trader {i} - info {j}")
    elif isinstance(data, dict):
        # 单个trader
        required_fields = {"trader_uid", "trader_name", "trader_detail_url"}
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            raise ValueError(f"trader 缺少欄位: {', '.join(missing)}")
        infos = data.get("infos")
        if not infos or not isinstance(infos, list):
            raise ValueError(f"trader 缺少infos或格式錯誤")
        for j, info in enumerate(infos):
            validate_single_holding_report(info, f"info {j}")
    else:
        raise ValueError("請求資料必須為字典或列表格式")

def validate_single_holding_report(data: dict, prefix: str = "") -> None:
    """驗證單個持倉報告項目（只校验币种相关字段）"""
    required_fields = {
        "pair", "pair_side", "pair_margin_type", "pair_leverage",
        "entry_price", "current_price", "unrealized_pnl_percentage"
    }
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        error_msg = f"缺少欄位: {', '.join(missing)}"
        if prefix:
            error_msg = f"{prefix} - {error_msg}"
        raise ValueError(error_msg)

    # 檢查 pair_side
    if str(data["pair_side"]) not in {"1", "2"}:
        error_msg = "pair_side 只能是 '1'(Long) 或 '2'(Short)"
        if prefix:
            error_msg = f"{prefix} - {error_msg}"
        raise ValueError(error_msg)

    # 檢查 pair_margin_type
    if str(data["pair_margin_type"]) not in {"1", "2"}:
        error_msg = "pair_margin_type 只能是 '1'(Cross) 或 '2'(Isolated)"
        if prefix:
            error_msg = f"{prefix} - {error_msg}"
        raise ValueError(error_msg)

    # 數值檢查
    try:
        float(data["entry_price"])
        float(data["current_price"])
        float(data["unrealized_pnl_percentage"])
        float(data["pair_leverage"])
        # 檢查可選的止盈止損價格
        if data.get("tp_price") not in (None, "", "None"):
            float(data["tp_price"])
        if data.get("sl_price") not in (None, "", "None"):
            float(data["sl_price"])
    except (TypeError, ValueError):
        error_msg = "數值欄位必須為數字格式"
        if prefix:
            error_msg = f"{prefix} - {error_msg}"
        raise ValueError(error_msg)

async def process_holding_report(data, bot: Bot) -> None:
    """背景協程：處理持倉報告推送，支持列表和字典格式"""
    try:
        # 如果是列表，合併所有項目為一條消息
        if isinstance(data, list):
            await process_holding_report_list(data, bot)
        else:
            # 如果是字典，處理單個項目
            await process_single_holding_report(data, bot)

    except Exception as e:
        logger.error(f"推送持倉報告失敗: {e}")

async def process_holding_report_list(data_list: list, bot: Bot, data_raw=None) -> None:
    """處理持倉報告列表，將每個trader的所有infos合併為一條消息推送"""
    try:
        if not data_raw:
            logger.warning("原始trader列表為空")
            return

        logger.info(f"[持倉報告] 原始trader數量: {len(data_raw)}")

        all_tasks = []
        skipped_count = 0
        
        for trader in data_raw:
            trader_uid = str(trader["trader_uid"])
            trader_name = trader.get("trader_name", "Unknown")
            
            logger.info(f"[持倉報告] 處理 trader: {trader_name} (UID: {trader_uid})")
            
            # 針對持倉報告取用 holding 類型的推送配置
            push_targets = await get_push_targets(trader_uid, signal_type="holding")
            
            if not push_targets:
                # logger.info(f"[持倉報告] trader_uid={trader_uid} ({trader_name}) 無推送目標，跳過")
                skipped_count += 1
                continue
            
            logger.info(f"[持倉報告] trader_uid={trader_uid} ({trader_name}) 找到 {len(push_targets)} 個推送目標")
            
            seen = set()
            for chat_id, topic_id, jump, group_lang in push_targets:
                print(f"chat_id: {chat_id}, topic_id: {topic_id}, jump: {jump}, group_lang: {group_lang}")
                key = (chat_id, topic_id)
                if key in seen:
                    continue
                seen.add(key)
                logger.info(f"[持倉報告] 準備發送到: chat_id={chat_id}, topic_id={topic_id}, jump={jump}")
                include_link = (jump == "1")
                logger.info(f"[持倉報告] 是否附加連結 include_link={include_link}")

                # 語言
                api_lang = await get_preferred_language(user_id=None, chat_id=str(chat_id))
                lang = group_lang or api_lang or 'en'
                logger.info(f"[i18n] holding(list) chat_id={chat_id}, topic_id={topic_id}, group_lang={group_lang}, api_lang={api_lang}, resolved={lang}")

                # 構建標題
                header = render_template(
                    "holding.summary.header",
                    lang,
                    {"trader_name": trader_name},
                    fallback_lang='en'
                ) or f"⚡️{trader_name} Trading Summary (Updated every 12 hours)"

                # 拼接每條 info
                parts = [header, ""]
                for i, data in enumerate(trader.get("infos", []), 1):
                    # 文案映射
                    margin_type_map = {"1": "Cross", "2": "Isolated", 1: "Cross", 2: "Isolated"}
                    pair_side = localize_pair_side(lang, data.get("pair_side", ""))
                    margin_type = margin_type_map.get(str(data.get("pair_margin_type", "")), str(data.get("pair_margin_type", "")))

                    tpl = {
                        "index": i,
                        "pair": data.get("pair", ""),
                        "pair_side": pair_side,
                        "margin_type": margin_type,
                        "entry_price": str(data.get("entry_price", 0)),
                        "current_price": str(data.get("current_price", 0)),
                        "roi": format_float(float(data.get("unrealized_pnl_percentage", 0)) * 100),
                        "leverage": format_float(data.get("pair_leverage", 0)),
                        "tp_price": str(data.get("tp_price", 0)) if data.get("tp_price") not in (None, "", "None") else "",
                        "sl_price": str(data.get("sl_price", 0)) if data.get("sl_price") not in (None, "", "None") else "",
                    }

                    item_text = render_template("holding.summary.item", lang, tpl, fallback_lang='en') or (
                        f"**{i}. {tpl['pair']} {tpl['margin_type']} {tpl['leverage']}X**\n"
                        f"➡️Direction: {tpl['pair_side']}\n"
                        f"🎯Entry Price: ${tpl['entry_price']}\n"
                        f"📊Current Price: ${tpl['current_price']}\n"
                        f"🚀ROI: {tpl['roi']}%"
                    )

                    # 附加 TP/SL 行
                    if tpl["tp_price"]:
                        tp_line = render_template("holding.summary.tp_line", lang, tpl, fallback_lang='en') or f"✅TP Price: ${tpl['tp_price']}"
                        item_text += "\n" + tp_line
                    if tpl["sl_price"]:
                        sl_line = render_template("holding.summary.sl_line", lang, tpl, fallback_lang='en') or f"🛑SL Price: ${tpl['sl_price']}"
                        item_text += "\n" + sl_line

                    parts.append(item_text)
                    parts.append("")

                text = "\n".join(p for p in parts if p is not None)

                # 追加鏈接
                if include_link:
                    more = render_template("copy.open.more", lang, {
                        "trader_name": trader_name,
                        "detail_url": trader.get('trader_detail_url', '')
                    }, fallback_lang='en') or f"[About {trader_name}, more actions>>]({trader.get('trader_detail_url', '')})"
                    text += f"\n\n{more}"
                    logger.info(f"[持倉報告] 已附加 more 連結: {more}")

                all_tasks.append(
                    send_telegram_message(
                        bot=bot,
                        chat_id=chat_id,
                        topic_id=topic_id,
                        text=text,
                        parse_mode="Markdown",
                        trader_uid=trader_uid
                    )
                )
        
        # logger.info(f"[持倉報告] 跳過 {skipped_count} 個無推送目標的 trader")
        logger.info(f"[持倉報告] 開始推送 Telegram, 頻道數: {len(all_tasks)}")
        
        if all_tasks:
            results = await asyncio.gather(*all_tasks, return_exceptions=True)
            success_count = 0
            for idx, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(f"[持倉報告] Telegram 發送異常 (index={idx}): {r}")
                elif r is True:
                    success_count += 1
                else:
                    logger.error(f"[持倉報告] Telegram 發送失敗 (index={idx}): {r}")
            
            logger.info(f"[持倉報告] Telegram 推送完成: {success_count}/{len(all_tasks)} 成功")
        else:
            logger.warning("[持倉報告] 沒有可推送的任務")

        # 推送 Discord Bot，保持原始结构，分批每10个trader一批
        if DISCORD_BOT_HOLDING and data_raw is not None:
            batch_size = 10
            total = len(data_raw)
            logger.info(f"[持倉報告] 準備分批推送到 Discord Bot, 批次大小: {batch_size}, 總數: {total}")
            # 決定 Discord 使用的語言：取第一個推送目標語言，或 API fallback
            try:
                first_chat_id, _, _, first_group_lang = push_targets[0]
                first_api_lang = await get_preferred_language(user_id=None, chat_id=str(first_chat_id))
                discord_lang = first_group_lang or first_api_lang or 'en'
            except Exception:
                discord_lang = 'en'
            for i in range(0, total, batch_size):
                batch = data_raw[i:i+batch_size]
                logger.info(f"[持倉報告] 即將發送到 Discord Bot, 批次 {i//batch_size+1}: {len(batch)} 個 trader")
                try:
                    payload = {"data": batch, "lang": discord_lang}
                    await send_discord_message(DISCORD_BOT_HOLDING, payload)
                    logger.info(f"[持倉報告] Discord 批次 {i//batch_size+1} 發送完成")
                except Exception as e:
                    logger.error(f"[持倉報告] 發送到 Discord Bot 失敗（批次 {i//batch_size+1}）: {e}")

    except Exception as e:
        logger.error(f"推送持倉報告列表失敗: {e}")
        import traceback
        logger.error(f"詳細錯誤: {traceback.format_exc()}")

async def process_single_holding_report(data: dict, bot: Bot) -> None:
    """處理單個持倉報告項目"""
    try:
        trader_uid = str(data["trader_uid"])

        # 獲取推送目標
        push_targets = await get_push_targets(trader_uid)

        if not push_targets:
            # logger.warning(f"未找到符合條件的持倉報告推送頻道: {trader_uid}")
            return

        # 準備發送任務
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
            logger.info(f"[i18n] holding(single) chat_id={chat_id}, topic_id={topic_id}, group_lang={group_lang}, api_lang={api_lang}, resolved={lang}")

            # 文案映射
            margin_type_map = {"1": "Cross", "2": "Isolated", 1: "Cross", 2: "Isolated"}
            pair_side = localize_pair_side(lang, data.get("pair_side", ""))
            margin_type = margin_type_map.get(str(data.get("pair_margin_type", "")), str(data.get("pair_margin_type", "")))

            tpl = {
                "trader_name": data.get('trader_name', 'Trader'),
                "pair": data.get('pair', ''),
                "pair_side": pair_side,
                "margin_type": margin_type,
                "entry_price": str(data.get('entry_price', 0)),
                "current_price": str(data.get('current_price', 0)),
                "roi": format_float(float(data.get('unrealized_pnl_percentage', 0)) * 100),
                "leverage": format_float(data.get('pair_leverage', 0)),
                "tp_price": str(data.get('tp_price', 0)) if data.get('tp_price') not in (None, "", "None") else "",
                "sl_price": str(data.get('sl_price', 0)) if data.get('sl_price') not in (None, "", "None") else "",
                "trader_detail_url": data.get('trader_detail_url', ''),
            }

            # 主體
            text = render_template("holding.summary.body", lang, tpl, fallback_lang='en') or (
                f"⚡️{tpl['trader_name']} Trading Summary (Updated every 12 hours)\n\n"
                f"📢{tpl['pair']} {tpl['margin_type']} {tpl['leverage']}X\n"
                f"➡️Direction: {tpl['pair_side']}\n"
                f"🎯Entry Price: ${tpl['entry_price']}\n"
                f"📊Current Price: ${tpl['current_price']}\n"
                f"🚀ROI: {tpl['roi']}%"
            )

            if tpl["tp_price"]:
                tp_line = render_template("holding.summary.tp_line", lang, tpl, fallback_lang='en') or f"✅TP Price: ${tpl['tp_price']}"
                text += "\n" + tp_line
            if tpl["sl_price"]:
                sl_line = render_template("holding.summary.sl_line", lang, tpl, fallback_lang='en') or f"🛑SL Price: ${tpl['sl_price']}"
                text += "\n" + sl_line

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
        if DISCORD_BOT_HOLDING:
            logger.info(f"[持倉報告] 即將發送到 Discord Bot: {data}")
            try:
                # 取語言：使用第一個目標語言或 API fallback
                first_chat_id, _, _, first_group_lang = push_targets[0]
                first_api_lang = await get_preferred_language(user_id=None, chat_id=str(first_chat_id))
                discord_lang = first_group_lang or first_api_lang or 'en'
            except Exception:
                discord_lang = 'en'
            payload = dict(data)
            payload["lang"] = discord_lang
            await send_discord_message(DISCORD_BOT_HOLDING, payload)

    except Exception as e:
        logger.error(f"推送單個持倉報告失敗: {e}")

def format_holding_report_text(data: dict, include_link: bool = False) -> str:
    """格式化持倉報告文本"""
    # 文案映射
    pair_side_map = {"1": "Long", "2": "Short", 1: "Long", 2: "Short"}
    margin_type_map = {"1": "Cross", "2": "Isolated", 1: "Cross", 2: "Isolated"}
    
    pair_side = pair_side_map.get(str(data.get("pair_side", "")), str(data.get("pair_side", "")))
    margin_type = margin_type_map.get(str(data.get("pair_margin_type", "")), str(data.get("pair_margin_type", "")))
    
    # 格式化數值
    entry_price = str(data.get("entry_price", 0))
    current_price = str(data.get("current_price", 0))
    roi = format_float(float(data.get("unrealized_pnl_percentage", 0)) * 100)
    leverage = format_float(data.get("pair_leverage", 0))
    
    # 判斷是否有設置止盈止損
    has_tp = bool(data.get("tp_price"))
    has_sl = bool(data.get("sl_price"))
    
    text = (
        f"⚡️{data.get('trader_name', 'Trader')} Trading Summary (Updated every 12 hours)\n\n"
        f"📢{data.get('pair', '')} {margin_type} {leverage}X\n"
        f"➡️Direction: {pair_side}\n"
        f"🎯Entry Price: ${entry_price}\n"
        f"📊Current Price: ${current_price}\n"
        f"🚀ROI: {roi}%"
    )
    
    # 如果有設置止盈止損，添加相關信息
    tp_sl_lines = []
    if has_tp:
        tp_price = str(data.get("tp_price", 0))
        tp_sl_lines.append(f"✅TP Price: ${tp_price}")
    if has_sl:
        sl_price = str(data.get("sl_price", 0))
        tp_sl_lines.append(f"🛑SL Price: ${sl_price}")
    
    if tp_sl_lines:
        text += "\n" + "\n".join(tp_sl_lines)
    
    if include_link:
        # 使用 Markdown 格式創建可點擊的超連結
        trader_name = data.get('trader_name', 'Trader')
        detail_url = data.get('trader_detail_url', '')
        text += f"\n\n[About {trader_name}, more actions>>]({detail_url})"
    
    return text

def format_holding_report_list_text(infos: list, trader: dict, include_link: bool = True) -> str:
    if not infos:
        return ""
    trader_name = trader.get('trader_name', 'Trader')
    text = f"⚡️{trader_name} Trading Summary (Updated every 12 hours)\n\n"
    for i, data in enumerate(infos, 1):
        pair_side_map = {"1": "Long", "2": "Short", 1: "Long", 2: "Short"}
        margin_type_map = {"1": "Cross", "2": "Isolated", 1: "Cross", 2: "Isolated"}
        pair_side = pair_side_map.get(str(data.get("pair_side", "")), str(data.get("pair_side", "")))
        margin_type = margin_type_map.get(str(data.get("pair_margin_type", "")), str(data.get("pair_margin_type", "")))
        entry_price = str(data.get("entry_price", 0))
        current_price = str(data.get("current_price", 0))
        roi = format_float(float(data.get("unrealized_pnl_percentage", 0)) * 100)
        leverage = format_float(data.get("pair_leverage", 0))
        text += (
            f"**{i}. {data.get('pair', '')} {margin_type} {leverage}X**\n"
            f"➡️Direction: {pair_side}\n"
            f"🎯Entry Price: ${entry_price}\n"
            f"📊Current Price: ${current_price}\n"
            f"🚀ROI: {roi}%"
        )
        tp_sl_lines = []
        tp_price = data.get("tp_price")
        if tp_price not in (None, "", "None"):
            tp_price = str(tp_price)
            tp_sl_lines.append(f"✅TP Price: ${tp_price}")
        sl_price = data.get("sl_price")
        if sl_price not in (None, "", "None"):
            sl_price = str(sl_price)
            tp_sl_lines.append(f"🛑SL Price: ${sl_price}")
        if tp_sl_lines:
            text += "\n" + "\n".join(tp_sl_lines)
        text += "\n\n"
    text = text.rstrip('\n')
    if include_link:
        detail_url = trader.get('trader_detail_url', '')
        text += f"\n\n[About {trader_name}, more actions>>]({detail_url})"
    return text

 