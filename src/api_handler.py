import asyncio
import aiohttp
import logging

logger = logging.getLogger(__name__)

async def fetch_pending_messages(api_url):
    """
    模擬調用 API 檢查是否有待處理消息。
    """
    try:
        # 模擬請求 API 的返回數據
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()  # 假設返回 JSON 數據
                    return data.get("messages", [])  # 假設返回字段為 'messages'
                else:
                    logger.warning(f"API 調用失敗，狀態碼: {response.status}")
                    return []
    except Exception as e:
        logger.error(f"調用 API 時發生錯誤: {e}")
        return []

async def periodic_api_check(api_url, bot, target_group_id, interval=30):
    """
    定時檢查 API，並向指定群組發送消息。
    """
    while True:
        logger.info("開始調用 API...")
        pending_messages = await fetch_pending_messages(api_url)
        
        if pending_messages:  # 如果有待處理消息
            for message in pending_messages:
                try:
                    await bot.send_message(chat_id=target_group_id, text=message)
                    logger.info(f"成功發送消息到群組 {target_group_id}: {message}")
                except Exception as e:
                    logger.error(f"無法發送消息到群組 {target_group_id}: {e}")
        else:
            logger.info("沒有待處理消息")

        # 等待指定時間後重複
        await asyncio.sleep(interval)
