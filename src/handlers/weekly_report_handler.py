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

def validate_weekly_report(data: dict) -> None:
    """驗證週報請求資料，失敗時拋出 ValueError。"""
    required_fields = {
        "trader_uid", "trader_name", "trader_url", "trader_detail_url",
        "total_roi", "total_pnl", "total_trades",
        "win_trades", "loss_trades", "win_rate"
    }

    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        raise ValueError(f"缺少欄位: {', '.join(missing)}")

    # 數值檢查
    try:
        float(data["total_roi"])
        float(data["total_pnl"])
        int(data["total_trades"])
        int(data["win_trades"])
        int(data["loss_trades"])
        float(data["win_rate"])
    except (TypeError, ValueError):
        raise ValueError("數值欄位必須為正確的數字格式")

    # 驗證勝率範圍
    win_rate = float(data["win_rate"])
    if not (0 <= win_rate <= 100):
        raise ValueError("勝率必須在 0-100 之間")

async def process_weekly_report(data: dict, bot: Bot) -> None:
    """背景協程：處理週報推送"""
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
        logger.error(f"推送週報失敗: {e}")

def format_weekly_report_text(data: dict, include_link: bool = True) -> str:
    """格式化週報文本"""
    # 計算虧損筆數
    total_trades = int(data.get("total_trades", 0))
    win_trades = int(data.get("win_trades", 0))
    loss_trades = total_trades - win_trades
    
    # 格式化數值 - total_roi 需要乘上100以匹配圖片顯示
    total_roi = format_float(float(data.get("total_roi", 0)) * 100)
    win_rate = format_float(data.get("win_rate", 0))
    
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