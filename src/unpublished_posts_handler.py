# import asyncio
# import aiohttp
# import logging

# logger = logging.getLogger(__name__)

# async def fetch_pending_messages(api_url):
#     """
#     模擬調用 API 檢查是否有待處理消息。
#     """
#     try:
#         # 模擬請求 API 的返回數據
#         async with aiohttp.ClientSession() as session:
#             async with session.get(api_url) as response:
#                 if response.status == 200:
#                     data = await response.json()  # 假設返回 JSON 數據
#                     return data.get("messages", [])  # 假設返回字段為 'messages'
#                 else:
#                     logger.warning(f"API 調用失敗，狀態碼: {response.status}")
#                     return []
#     except Exception as e:
#         logger.error(f"調用 API 時發生錯誤: {e}")
#         return []

# async def periodic_api_check(api_url, bot, target_group_id, interval=30):
#     """
#     定時檢查 API，並向指定群組發送消息。
#     """
#     while True:
#         logger.info("開始調用 API...")
#         pending_messages = await fetch_pending_messages(api_url)
        
#         if pending_messages:  # 如果有待處理消息
#             for message in pending_messages:
#                 try:
#                     await bot.send_message(chat_id=target_group_id, text=message)
#                     logger.info(f"成功發送消息到群組 {target_group_id}: {message}")
#                 except Exception as e:
#                     logger.error(f"無法發送消息到群組 {target_group_id}: {e}")
#         else:
#             logger.info("沒有待處理消息")

#         # 等待指定時間後重複
#         await asyncio.sleep(interval)
import json
import logging
import aiohttp
from aiogram import Bot

logger = logging.getLogger(__name__)

async def fetch_unpublished_posts(posts_url, headers, payload):
    """
    调用 /posts/list 接口，检查未发布的文章
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(posts_url, headers=headers, data=json.dumps(payload)) as response:
                if response.status == 200:
                    posts_data = await response.json()
                    return posts_data.get("items", [])  # 返回 items 列表
                else:
                    logger.error(f"获取文章列表失败，状态码: {response.status}")
                    return []
    except Exception as e:
        logger.error(f"调用 /posts/list 接口失败: {e}")
        return []

async def publish_posts(bot: Bot, posts_list, update_url, headers):
    """
    发布文章到目标用户或群组，并更新文章状态
    """
    for post in posts_list:
        chat_id = post.get("topic")  # 替换为文章的目标 chat_id 或 topic
        content = post.get("content")  # 替换为文章的内容
        image = post.get("image")  # 获取文章的图片 URL

        if not chat_id or not content:
            logger.warning(f"文章数据不完整: {post}")
            continue

        try:
            # 如果有图片，发送带图片的消息；否则只发送文本
            if image:
                await bot.send_photo(chat_id='-1002409349001', photo=image, caption=content, parse_mode="HTML")
            else:
                await bot.send_message(chat_id='-1002409349001', text=content, parse_mode="HTML")

            logger.info(f"成功发送文章到 Chat ID: {chat_id}，内容: {content}")

            # 更新文章状态为已发布
            await update_post_status(update_url, headers, post.get("id"))
        except Exception as e:
            logger.error(f"发送文章失败: {e}")

async def update_post_status(update_url, headers, post_id):
    """
    调用 /posts/update 接口，更新文章状态为已发布
    """
    payload = {"id": post_id, "status": 1}  # 更新文章状态为已发布，假设 status=1 表示已发布

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(update_url, headers=headers, data=json.dumps(payload)) as response:
                if response.status == 200:
                    logger.info(f"成功更新文章状态: {post_id}")
                else:
                    logger.error(f"更新文章状态失败，状态码: {response.status}")
    except Exception as e:
        logger.error(f"调用 /posts/update 接口失败: {e}")
