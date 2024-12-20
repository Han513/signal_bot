import aiofiles
import os
import json
import logging
import aiohttp
import urllib.parse
from aiogram import Bot
from aiogram.types import FSInputFile

logger = logging.getLogger(__name__)

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

async def fetch_unpublished_posts(posts_url, headers):
    """
    调用 /posts/list 接口，检查未发布的文章
    """
    try:
        # 将 payload 转换为查询字符串参数
        # params = urllib.parse.urlencode(payload)

        async with aiohttp.ClientSession() as session:
            # 使用 params 参数将 payload 传递给 GET 请求
            async with session.get(posts_url, headers=headers) as response:
                if response.status == 200:
                    posts_data = await response.json()
                    print(posts_data)
                    return posts_data.get("data", {}).get("items", [])  # 返回 items 列表
                else:
                    logger.error(f"获取文章列表失败，状态码: {response.status}")
                    return []
    except Exception as e:
        logger.error(f"调用 /posts/list 接口失败: {e}")
        return []

async def publish_posts(bot: Bot, posts_list, update_url, headers):
    """
    发布文章到目标群组的特定主题，并更新文章状态
    """
    socials_url = "http://127.0.0.1:5002/admin/telegram/social/socials"  # 查询 socialGroup 和 chatId 的接口
    # socials_url = "http://172.25.183.151:4070/admin/telegram/social/socials"  # 查询 socialGroup 和 chatId 的接口
    socials_payload = {"brand": "BYD", "type": "TELEGRAM"}

    try:
        # 获取所有社交平台的配置
        async with aiohttp.ClientSession() as session:
            async with session.post(socials_url, headers=headers, data=socials_payload) as response:
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
        topic = post.get("topic_name")
        content = post.get("content")
        image = post.get("image")
        post_id = post.get("id")

        if not topic or not content:
            logger.warning(f"文章数据不完整，跳过: {post}")
            continue
        normalized_topic = topic.strip()

        # 查找匹配的群组和主题
        matching_chats = [
            {"chatId": social["socialGroup"], "topicId": chat["chatId"]}
            for social in social_chats
            for chat in social.get("chats", [])
            if chat["enable"] and chat["name"] == normalized_topic
        ]

        if not matching_chats:
            logger.warning(f"未找到匹配的主题 {topic}，跳过文章: {post}")
            continue

        # 遍历所有匹配的 chatId 和 topic_id，发送消息
        for chat in matching_chats:
            chat_id = chat.get("chatId")
            topic_id = chat.get("topicId")

            if not chat_id or not topic_id:
                logger.warning(f"未找到 chatId 或 topicId，跳过: {chat}")
                continue

            try:
                # 如果有图片，先下载图片
                temp_file_path = None
                if image:
                    if not image.startswith("http"):  # 补全相对路径为完整 URL
                        # image = f"http://127.0.0.1:5003{image}"
                        image = f"http://172.25.183.139:5003{image}"

                    # 下载图片到本地临时文件
                    temp_file_path = f"/tmp/image_{post_id}.jpg"
                    async with aiohttp.ClientSession() as session:
                        async with session.get(image) as img_response:
                            if img_response.status == 200:
                                async with aiofiles.open(temp_file_path, "wb") as f:
                                    await f.write(await img_response.read())
                                logger.info(f"Image downloaded successfully: {temp_file_path}")
                            else:
                                logger.error(f"Failed to download image: {image}")
                                continue

                    # 使用 FSInputFile 发送图片
                    image_file = FSInputFile(temp_file_path)
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=image_file,
                        caption=content,
                        message_thread_id=topic_id,  # 主题 ID
                        parse_mode="HTML"
                    )
                else:
                    # 仅发送文本
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
                # 删除临时图片文件
                if temp_file_path and os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                    logger.info(f"已删除临时图片文件: {temp_file_path}")

async def update_post_status(update_url, headers, post_id):
    payload = {"id": post_id, "status": 1}  # 更新文章状态为已发布
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(update_url, headers=headers, data=json.dumps(payload)) as response:
                print(await response.json())
                if response.status == 200:
                    logger.info(f"成功更新文章状态: {post_id}")
                else:
                    logger.error(f"更新文章状态失败，状态码: {response.status}")
        except Exception as e:
            logger.error(f"调用 /posts/update 接口失败: {e}")