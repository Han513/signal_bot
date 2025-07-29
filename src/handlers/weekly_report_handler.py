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
from collections import defaultdict

load_dotenv()
DISCORD_BOT_WEEKLY = os.getenv("DISCORD_BOT_WEEKLY")

logger = logging.getLogger(__name__)

async def handle_weekly_report(request: web.Request, *, bot: Bot):
    """
    處理 /api/report/weekly 介面：發送每週績效報告
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
        validate_weekly_report(data)
    except ValueError as err:
        return web.json_response({"status": "400", "message": str(err)}, status=400)

    # 背景處理，不阻塞 HTTP 回應
    return create_async_response(process_weekly_report, data, bot)

def validate_weekly_report(data) -> None:
    """驗證週報請求資料，失敗時拋出 ValueError。支持列表和字典格式。"""
    # 檢查 data 是否為列表或字典
    if isinstance(data, list):
        # 如果是列表，驗證每個項目
        if not data:
            raise ValueError("列表不能為空")
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"列表項目 {i} 必須為字典格式，收到: {type(item)}")
            validate_single_weekly_report(item, f"項目 {i}")
    elif isinstance(data, dict):
        # 如果是字典，驗證單個項目
        validate_single_weekly_report(data)
    else:
        raise ValueError(f"請求資料必須為字典或列表格式，收到: {type(data)}")

def validate_single_weekly_report(data: dict, prefix: str = "") -> None:
    """驗證單個週報項目"""
    required_fields = {
        "trader_uid", "trader_name", "trader_url", "trader_detail_url",
        "total_roi", "total_pnl", "total_trades",
        "win_trades", "loss_trades", "win_rate"
    }

    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        error_msg = f"缺少欄位: {', '.join(missing)}"
        if prefix:
            error_msg = f"{prefix} - {error_msg}"
        raise ValueError(error_msg)

    # 數值檢查
    try:
        float(data["total_roi"])
        float(data["total_pnl"])
        int(data["total_trades"])
        int(data["win_trades"])
        int(data["loss_trades"])
        float(data["win_rate"])
    except (TypeError, ValueError):
        error_msg = "數值欄位必須為正確的數字格式"
        if prefix:
            error_msg = f"{prefix} - {error_msg}"
        raise ValueError(error_msg)

    # 驗證勝率範圍
    win_rate = float(data["win_rate"])
    if not (0 <= win_rate <= 100):
        error_msg = "勝率必須在 0-100 之間"
        if prefix:
            error_msg = f"{prefix} - {error_msg}"
        raise ValueError(error_msg)

async def process_weekly_report(data, bot: Bot) -> None:
    """背景協程：處理週報推送，支持列表和字典格式，自動分組"""
    try:
        # 如果是列表，自動根據 trader_uid 分組
        if isinstance(data, list):
            # 分組
            groups = defaultdict(list)
            for item in data:
                trader_uid = item.get("trader_uid", "__unknown__")
                groups[trader_uid].append(item)
            # 針對每個分組分別推送
            for group in groups.values():
                await process_weekly_report_list(group, bot)
        else:
            # 如果是字典，處理單個項目
            await process_single_weekly_report(data, bot)

    except Exception as e:
        logger.error(f"推送週報失敗: {e}")

async def process_weekly_report_list(data_list: list, bot: Bot) -> None:
    """處理週報列表，將所有項目合併為一條消息"""
    try:
        if not data_list:
            logger.warning("週報列表為空")
            return

        # 使用第一個項目的 trader_uid 來獲取推送目標
        trader_uid = str(data_list[0]["trader_uid"])
        push_targets = await get_push_targets(trader_uid)

        if not push_targets:
            logger.warning(f"未找到符合條件的週報推送頻道: {trader_uid}")
            return

        # 生成合併的週報圖片
        img_path = generate_weekly_report_list_image(data_list)
        if not img_path:
            logger.warning("週報圖片生成失敗，取消推送")
            return

        # 準備發送任務
        tasks = []
        for chat_id, topic_id, jump in push_targets:
            caption = format_weekly_report_list_text(data_list, jump == "1")
            
            tasks.append(
                send_telegram_message(
                    bot=bot,
                    chat_id=chat_id,
                    topic_id=topic_id,
                    text=caption,
                    photo_path=img_path,
                    parse_mode="Markdown"
                )
            )

        # 等待 Telegram 發送結果
        await asyncio.gather(*tasks, return_exceptions=True)

        # 同步發送至 Discord Bot（發送第一個項目作為代表）
        if DISCORD_BOT_WEEKLY:
            await send_discord_message(DISCORD_BOT_WEEKLY, data_list[0])

    except Exception as e:
        logger.error(f"推送週報列表失敗: {e}")

async def process_single_weekly_report(data: dict, bot: Bot) -> None:
    """處理單個週報項目"""
    try:
        trader_uid = str(data["trader_uid"])

        # 獲取推送目標
        push_targets = await get_push_targets(trader_uid)

        if not push_targets:
            logger.warning(f"未找到符合條件的週報推送頻道: {trader_uid}")
            return

        # 生成週報圖片
        img_path = generate_weekly_report_image(data)
        if not img_path:
            logger.warning("週報圖片生成失敗，取消推送")
            return

        # 準備發送任務
        tasks = []
        for chat_id, topic_id, jump in push_targets:
            caption = format_weekly_report_text(data, jump == "1")
            
            tasks.append(
                send_telegram_message(
                    bot=bot,
                    chat_id=chat_id,
                    topic_id=topic_id,
                    text=caption,
                    photo_path=img_path,
                    parse_mode="Markdown"
                )
            )

        # 等待 Telegram 發送結果
        await asyncio.gather(*tasks, return_exceptions=True)

        # 同步發送至 Discord Bot
        if DISCORD_BOT_WEEKLY:
            await send_discord_message(DISCORD_BOT_WEEKLY, data)

    except Exception as e:
        logger.error(f"推送單個週報失敗: {e}")

def format_weekly_report_text(data: dict, include_link: bool = True) -> str:
    """格式化週報文本"""
    # 計算虧損筆數
    total_trades = int(data.get("total_trades", 0))
    win_trades = int(data.get("win_trades", 0))
    loss_trades = total_trades - win_trades
    
    # 格式化數值 - total_roi 需要乘上100以匹配圖片顯示
    total_roi = format_float(float(data.get("total_roi", 0)) * 100)
    win_rate = format_float(float(data.get("win_rate", 0)) * 100)
    
    # 判斷盈虧顏色
    is_positive = float(data.get("total_roi", 0)) >= 0
    roi_emoji = "🔥" if is_positive else "📉"
    
    text = (
        f"⚡️{data.get('trader_name', 'Trader')} Weekly Performance Report\n\n"
        f"{roi_emoji} TOTAL R: {total_roi}%\n\n"
        f"📈 Total Trades: {total_trades}\n"
        f"✅ Wins: {win_trades}\n"
        f"❌ Losses: {loss_trades}\n"
        f"🏆 Win Rate: {win_rate}%"
    )
    
    if include_link:
        # 使用 Markdown 格式創建可點擊的超連結
        trader_name = data.get('trader_name', 'Trader')
        detail_url = data.get('trader_detail_url', '')
        text += f"\n\n[About {trader_name}, more actions>>]({detail_url})"
    
    return text

def format_weekly_report_list_text(data_list: list, include_link: bool = True) -> str:
    """格式化週報列表文本，將所有項目合併為一條消息"""
    if not data_list:
        return ""
    
    # 使用第一個項目的 trader_name 作為標題
    trader_name = data_list[0].get('trader_name', 'Trader')
    
    text = f"⚡️{trader_name} Weekly Performance Report\n\n"
    
    # 添加每個項目的信息
    for i, data in enumerate(data_list, 1):
        # 計算虧損筆數
        total_trades = int(data.get("total_trades", 0))
        win_trades = int(data.get("win_trades", 0))
        loss_trades = total_trades - win_trades
        
        # 格式化數值 - total_roi 需要乘上100以匹配圖片顯示
        total_roi = format_float(float(data.get("total_roi", 0)) * 100)
        win_rate = format_float(float(data.get("win_rate", 0)) * 100)
        
        # 判斷盈虧顏色
        is_positive = float(data.get("total_roi", 0)) >= 0
        roi_emoji = "🔥" if is_positive else "📉"
        
        # 添加項目信息
        text += (
            f"**{i}. {data.get('trader_name', 'Trader')}**\n"
            f"{roi_emoji} TOTAL R: {total_roi}%\n"
            f"📈 Total Trades: {total_trades}\n"
            f"✅ Wins: {win_trades}\n"
            f"❌ Losses: {loss_trades}\n"
            f"🏆 Win Rate: {win_rate}%\n\n"
        )
    
    # 移除最後的換行
    text = text.rstrip('\n')
    
    if include_link:
        # 使用 Markdown 格式創建可點擊的超連結
        detail_url = data_list[0].get('trader_detail_url', '')
        text += f"\n\n[About {trader_name}, more actions>>]({detail_url})"
    
    return text

def generate_weekly_report_image(data: dict) -> str:
    """生成週報圖片 - 使用 generate_trader_summary_image 函數"""
    try:
        # 使用 common.py 中的 generate_trader_summary_image 函數
        from .common import generate_trader_summary_image
        
        # 調用 generate_trader_summary_image 函數
        img_path = generate_trader_summary_image(
            trader_url=data.get("trader_url", ""),
            trader_name=data.get("trader_name", "Unknown"),
            pnl_percentage=data.get("total_roi", 0),
            pnl=data.get("total_pnl", 0)
        )
        
        if img_path:
            # 複製圖片到週報專用的臨時文件
            import shutil
            weekly_img_path = "/tmp/weekly_report.png"
            shutil.copy2(img_path, weekly_img_path)
            return weekly_img_path
        else:
            logger.error("generate_trader_summary_image 返回空路徑")
            return None
            
    except Exception as e:
        logger.error(f"生成週報圖片失敗: {e}")
        return None 

def generate_weekly_report_list_image(data_list: list) -> str:
    """生成週報列表圖片 - 合併多個交易員的統計信息"""
    try:
        if not data_list:
            return None
        
        # 使用第一個項目生成圖片作為代表
        # 或者可以考慮生成一個包含多個交易員信息的合成圖片
        from .common import generate_trader_summary_image
        
        first_data = data_list[0]
        img_path = generate_trader_summary_image(
            trader_url=first_data.get("trader_url", ""),
            trader_name=first_data.get("trader_name", "Unknown"),
            pnl_percentage=first_data.get("total_roi", 0),
            pnl=first_data.get("total_pnl", 0)
        )
        
        if img_path:
            # 複製圖片到週報專用的臨時文件
            import shutil
            weekly_img_path = "/tmp/weekly_report_list.png"
            shutil.copy2(img_path, weekly_img_path)
            return weekly_img_path
        else:
            logger.error("generate_trader_summary_image 返回空路徑")
            return None
            
    except Exception as e:
        logger.error(f"生成週報列表圖片失敗: {e}")
        return None