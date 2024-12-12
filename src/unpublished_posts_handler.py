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
import os
import json
import logging
import aiohttp
from aiogram import Bot
import urllib.parse

logger = logging.getLogger(__name__)

async def fetch_unpublished_posts(posts_url, headers, payload):
    """
    调用 /posts/list 接口，检查未发布的文章
    """
    try:
        # 将 payload 转换为查询字符串参数
        params = urllib.parse.urlencode(payload)

        async with aiohttp.ClientSession() as session:
            # 使用 params 参数将 payload 传递给 GET 请求
            async with session.get(posts_url, headers=headers, params=params) as response:
                if response.status == 200:
                    posts_data = await response.json()
                    return posts_data.get("data", {}).get("items", [])  # 返回 items 列表
                else:
                    logger.error(f"获取文章列表失败，状态码: {response.status}")
                    return []
    except Exception as e:
        logger.error(f"调用 /posts/list 接口失败: {e}")
        return []

async def publish_posts(bot, posts_list, update_url, headers):
    """
    发布文章到目标群组的特定主题，并更新文章状态
    """
    base_url = "http://127.0.0.1:5003"  # 补全图片 URL 的基地址
    socials_url = "http://127.0.0.1:5002/admin/telegram/social/socials"  # 查询 socialGroup 和 chatId 的接口

    try:
        # 获取所有社交平台的配置
        async with aiohttp.ClientSession() as session:
            async with session.post(socials_url, headers=headers) as response:
                if response.status == 200:
                    socials_data = await response.json()
                    social_chats = socials_data.get("data", [])
                else:
                    logger.error(f"获取社交群组配置失败，状态码: {response.status}")
                    return
    except Exception as e:
        logger.error(f"调用 /socials 接口失败: {e}")
        return

    # 遍历每篇文章
    for post in posts_list:
        topic = post.get("topic")  # 获取文章的主题
        content = post.get("content")  # 获取文章的内容
        image = post.get("image")  # 获取文章的图片 URL
        post_id = post.get("id")  # 获取文章的 ID

        if not topic or not content:
            logger.warning(f"文章数据不完整，跳过: {post}")
            continue

        normalized_topic = topic.strip()
        # for i in social_chats:
        #     for j in i.get("chats"):
        #         print(j.get("name"))
        #         print(normalized_topic)
        #         print(j.get("name") ==  normalized_topic)

        # 补全图片 URL
        if image and not image.startswith("http"):
            image = f"{base_url}{image}"

        # 查找匹配的群组和主题
        matching_chats = [
            {"chatId": social["socialGroup"], "topicId": chat["chatId"]}
            for social in social_chats
            for chat in social.get("chats", [])
            if chat["enable"] and chat["name"] == normalized_topic
        ]

        if not matching_chats:
            # logger.warning(f"未找到匹配的主题 {topic}，跳过文章: {post}")
            continue

        # 遍历所有匹配的 chatId 和 topic_id，发送消息
        for chat in matching_chats:
            chat_id = chat.get("chatId")
            topic_id = chat.get("topicId")  # 替换为你的主题 ID 字段名

            if not chat_id or not topic_id:
                logger.warning(f"未找到 chatId 或 topicId，跳过: {chat}")
                continue

            # 准备发送图片
            temp_file_path = None
            if image:
                temp_file_path = f"/tmp/image_{post_id}.jpg"  # 临时文件路径
                temp_file_path = await download_image(image, temp_file_path)

            try:
                # 如果有图片，发送带图片的消息；否则只发送文本
                if temp_file_path:
                    with open(temp_file_path, "rb") as f:
                        await bot.send_photo(
                            chat_id=chat_id,
                            photo=f,
                            caption=content,
                            message_thread_id=topic_id,  # 主题 ID
                            parse_mode="HTML"
                        )
                else:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=content,
                        message_thread_id=topic_id,  # 主题 ID
                        parse_mode="HTML"
                    )

                logger.info(f"成功发送文章到 Chat ID: {chat_id} 的主题 ID: {topic_id}，内容: {content}")

                # 更新文章状态为已发布
                # await update_post_status(update_url, headers, post_id)
            except Exception as e:
                logger.error(f"发送文章到 Chat ID {chat_id} 的主题 ID {topic_id} 失败: {e}")
            finally:
                # 删除临时文件
                if temp_file_path and os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                    logger.info(f"已删除临时图片文件: {temp_file_path}")

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

async def download_image(url, file_path):
    """
    下载图片到本地
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    with open(file_path, 'wb') as f:
                        f.write(await response.read())
                    logger.info(f"Image downloaded successfully: {file_path}")
                    return file_path
                else:
                    logger.error(f"Failed to download image: {url}, Status: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error downloading image: {e}")
        return None
