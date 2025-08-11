import aiofiles
import os
import json
import logging
import aiohttp
import urllib.parse
from aiogram import Bot
from aiogram.types import FSInputFile
from dotenv import load_dotenv
from multilingual_utils import get_multilingual_content

load_dotenv()

logger = logging.getLogger(__name__)
WELCOME_API = os.getenv("WELCOME_API")
VERIFY_API = os.getenv("VERIFY_API")
DETAIL_API = os.getenv("DETAIL_API")
SOCIAL_API = os.getenv("SOCIAL_API")
MESSAGE_API_URL = os.getenv("MESSAGE_API_URL")
UPDATE_MESSAGE_API_URL = os.getenv("UPDATE_MESSAGE_API_URL")

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
                    return posts_data.get("data", {}).get("items", [])  # 返回 items 列表
                else:
                    logger.error(f"获取文章列表失败，状态码: {response.status}")
                    return []
    except Exception as e:
        logger.error(f"调用 /posts/list 接口失败: {e}")
        return []

def escape_markdown_v2(text):
    escape_chars = r'_ * [ ] ( ) ~ ` > # + - = | { } . !'.split()
    for ch in escape_chars:
        text = text.replace(ch, '\\' + ch)
    return text

async def publish_posts(bot: Bot, posts_list, update_url, headers):
    """
    发布文章到目标群组的特定主题，并更新文章状态
    """
    # socials_url = "http://127.0.0.1:5002/admin/telegram/social/socials"  # 查询 socialGroup 和 chatId 的接口
    # socials_url = "http://172.31.91.67:4070/admin/telegram/social/socials"  # 查询 socialGroup 和 chatId 的接口
    socials_url = "http://172.25.183.151:4070/admin/telegram/social/socials"
    socials_payload = {"brand": "BYD", "type": "TELEGRAM"}

    try:
        # 获取所有社交平台的配置
        async with aiohttp.ClientSession() as session:
            async with session.post(SOCIAL_API, headers=headers, data=socials_payload) as response:
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
            {"chatId": social["socialGroup"], "topicId": chat["chatId"], "lang": social.get("lang", "en_US")}
            for social in social_chats
            for chat in social.get("chats", [])
            if chat["enable"] and chat["name"] == normalized_topic
        ]
        
        if not matching_chats:
            logger.warning(f"未找到匹配的主题 {topic}，跳过文章: {post}")
            continue

        # 收集發送結果
        send_results = []
        temp_file_path = None
        image_file = None
        
        # 如果有圖片，先下載一次
        if image:
            if not image.startswith("http"):
                # image = f"https://sp.signalcms.com{image}"
                image = f"http://172.25.183.139:5003{image}"
            temp_file_path = f"/tmp/image_{post_id}.jpg"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(image) as img_response:
                        if img_response.status == 200:
                            async with aiofiles.open(temp_file_path, "wb") as f:
                                await f.write(await img_response.read())
                            logger.info(f"Image downloaded successfully: {temp_file_path}")
                            image_file = FSInputFile(temp_file_path)
                        else:
                            logger.error(f"Failed to download image: {image}")
                            # 如果圖片下載失敗，記錄錯誤但繼續處理文字消息
                            send_results.append({"success": False, "error": f"Image download failed: {image}"})
            except Exception as e:
                logger.error(f"Error downloading image: {e}")
                send_results.append({"success": False, "error": f"Image download error: {e}"})
        
        logger.info(f"matching_chats: {matching_chats}")
        # 遍历所有匹配的 chatId 和 topic_id，发送消息
        for chat in matching_chats:
            chat_id = chat.get("chatId")
            topic_id = chat.get("topicId")
            lang = chat.get("lang", "en_US")

            logger.info(f"chat_id: {chat_id}, topic_id: {topic_id}, lang: {lang}")
            
            if not chat_id or not topic_id:
                logger.warning(f"未找到 chatId 或 topicId，跳过: {chat}")
                continue
                
            try:
                content = get_multilingual_content(post, lang)
                
                if image and image_file:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=image_file,
                        caption=content,
                        message_thread_id=topic_id,
                        parse_mode="HTML"
                    )
                else:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=content,
                        message_thread_id=topic_id,
                        parse_mode="HTML"
                    )
                    
                logger.info(f"成功发送文章到 Chat ID: {chat_id} 的主题 ID: {topic_id}")
                send_results.append({"success": True, "chat_id": chat_id, "topic_id": topic_id})
                
            except Exception as e:
                logger.error(f"发送文章到 Chat ID {chat_id} 的主题 ID {topic_id} 失败: {e}")
                send_results.append({"success": False, "error": str(e), "chat_id": chat_id, "topic_id": topic_id})

        # 清理臨時文件
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
            logger.info(f"已清理臨時文件: {temp_file_path}")

        successful_sends = [r for r in send_results if r["success"]]
        failed_sends = [r for r in send_results if not r["success"]]
        
        if successful_sends:
            logger.info(f"文章 {post_id} 成功發送到 {len(successful_sends)} 個社群")
            if failed_sends:
                logger.warning(f"文章 {post_id} 有 {len(failed_sends)} 個社群發送失敗: {failed_sends}")
            
            # 只要有成功發送，就更新文章狀態為已發布
            await update_post_status(update_url, headers, post_id)
        else:
            logger.error(f"文章 {post_id} 所有社群發送都失敗，不更新狀態")

async def update_post_status(update_url, headers, post_id):
    payload = {"id": post_id, "is_sent_tg": 1}  # 更新文章状态为已发布
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(update_url, headers=headers, data=json.dumps(payload)) as response:
                if response.status == 200:
                    logger.info(f"成功更新文章状态: {post_id}")
                else:
                    logger.error(f"更新文章状态失败，状态码: {response.status}")
        except Exception as e:
            logger.error(f"调用 /posts/update 接口失败: {e}")
