# import base64
import os
import asyncio
import aiohttp
import logging
import base64
import tempfile
import requests
from aiohttp import web
from aiogram import Bot, Dispatcher, types, Router
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, FSInputFile
from dotenv import load_dotenv
from api_handler import periodic_api_check  # 引入 API 檢查模組

# 導入 Group 相關函數
from db_handler_aio import insert_or_update_group, deactivate_group, get_active_groups

# 更詳細的日誌配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())
router = Router()

group_chat_ids = set()
group_member_counts = {}

async def load_active_groups():
    global group_chat_ids
    active_groups = await get_active_groups()
    group_chat_ids.update(active_groups)
    logger.info(f"從資料庫載入 {len(active_groups)} 個活躍群組")

@router.my_chat_member()
async def handle_my_chat_member(event: ChatMemberUpdated):
    """處理 Bot 的群組成員狀態變化"""
    try:
        chat = event.chat
        old_status = event.old_chat_member.status if event.old_chat_member else None
        new_status = event.new_chat_member.status if event.new_chat_member else None
        
        logger.info(f"群組事件詳情:")
        logger.info(f"Chat ID: {chat.id}")
        logger.info(f"Chat Title: {chat.title or 'N/A'}")
        logger.info(f"Chat Type: {chat.type}")
        logger.info(f"Old Status: {old_status}")
        logger.info(f"New Status: {new_status}")

        if new_status in ['kicked', 'left']:
            group_chat_ids.discard(str(chat.id))
            await deactivate_group(chat.id)
            logger.warning(f"Bot 被移除或離開群組: {chat.id}")
        
        elif new_status == 'member':
            await insert_or_update_group(
                chat_id=chat.id, 
                title=chat.title, 
                group_type=chat.type, 
                username=chat.username
            )
            group_chat_ids.add(str(chat.id))
            logger.info(f"Bot 加入新群組: {chat.id}")
        
        logger.info(f"目前追蹤的群組數量: {len(group_chat_ids)}")
    
    except Exception as e:
        logger.error(f"處理群組事件時發生錯誤: {e}")

@router.message(Command("groups"))
async def list_groups(message: types.Message):
    """列出目前追蹤的群組"""
    groups_list = "\n".join([str(group_id) for group_id in group_chat_ids])
    logger.info(f"目前追蹤的群組ID:\n{groups_list or '無群組'}")
    await message.reply(f"目前追蹤的群組數量: {len(group_chat_ids)}")

@router.message(Command("verify"))
async def handle_verify_command(message: types.Message):
    """处理 /verify 开头的指令，并验证用户输入的 UID"""
    command_parts = message.text.split()
    if len(command_parts) < 2:
        await message.reply("请提供验证码，例如: /verify 123456")
        return

    verify_code = command_parts[1]  # 获取用户输入的验证码
    chat_id = str(message.chat.id)  # 当前群组 ID，转换为字符串
    user_mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name

    # API 配置
    socials_url = "http://127.0.0.1:5002/admin/telegram/social/socials"
    verify_url = "http://127.0.0.1:5002/admin/telegram/social/verify"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        # 第一步：获取所有群组列表
        async with aiohttp.ClientSession() as session:
            async with session.post(socials_url, headers=headers) as socials_response:
                if socials_response.status == 200:
                    socials_data = await socials_response.json()
                    
                    # 确保 socials_data["data"] 是列表
                    groups = socials_data.get("data", [])
                    if not isinstance(groups, list):
                        raise ValueError("返回数据格式不正确：data 应为列表")

                    # 查找与当前群组 ID 匹配的信息
                    target_group = None
                    for group in groups:
                        for chat in group.get("chats", []):
                            if str(chat.get("chatId")) == chat_id:
                                target_group = group
                                break
                        if target_group:
                            break

                    if target_group:
                        verify_group = target_group.get("verifyGroup")
                        if not verify_group:
                            raise ValueError("未找到 verifyGroup")

                        # 第二步：根据 verifyGroup 和用户输入的 UID 调用 verify 接口
                        verify_payload = {"verifyGroup": verify_group, "code": verify_code}
                        async with session.post(verify_url, headers=headers, data=verify_payload) as verify_response:
                            if verify_response.status == 200:
                                verify_data = await verify_response.json()
                                if verify_data.get("code") == 200:
                                    # 替换 {username} 和 {admin} 参数
                                    success_message = verify_data.get("data", "").replace(
                                        "{username}", user_mention
                                    ).replace(
                                        "{admin}", "admin"  # 替换为实际管理员用户名
                                    )
                                    await message.reply(success_message, parse_mode="HTML")
                                else:
                                    await message.reply(
                                        verify_data.get("data", "验证失败"), parse_mode="HTML"
                                    )
                            else:
                                raise ValueError("调用 verify 接口失败")
                    else:
                        await message.reply("未找到与当前群组匹配的信息")
                else:
                    raise ValueError("获取群组列表失败")
    except Exception as e:
        logger.error(f"处理 API 请求失败: {e}")
        await message.reply("验证失败，可能是服务器或网络问题，请稍后再试")

@router.message(Command("send_to_topic"))
async def send_to_specific_topic(message: types.Message):
    """測試從本地文件夾發送圖片"""
    command_parts = message.text.split()
    if len(command_parts) < 4:
        await message.reply("用法：/send_local_image <群組ID> <Topic ID> <圖片文件名> <文字內容>")
        return

    try:
        # 提取參數
        group_chat_id = int(command_parts[1])  # 群組 ID
        topic_id = int(command_parts[2])  # Topic ID
        image_filename = command_parts[3]  # 本地圖片文件名
        content = " ".join(command_parts[4:])  # 消息內容

        # 獲取當前文件的絕對路徑
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # 確定圖片路徑（相對於項目根目錄的 images 文件夾）
        image_path = os.path.join(current_dir, "..", "images", image_filename)

        # 確保文件存在
        if not os.path.exists(image_path):
            await message.reply(f"找不到圖片文件: {image_path}")
            return

        # 使用 FSInputFile 打包圖片文件
        image_file = FSInputFile(image_path)

        # 發送圖片
        await bot.send_photo(
            chat_id=group_chat_id,
            photo=image_file,
            caption=content,  # 圖片的文字說明
            message_thread_id=topic_id
        )

        # 回應用戶
        response_message = (
            f"成功發送圖片到:\n"
            f"群組 ID: {group_chat_id}\n"
            f"Topic ID: {topic_id}\n"
            f"文字內容: {content}\n"
            f"圖片文件: {image_filename}"
        )
        await message.reply(response_message)
        logger.info(f"成功發送本地圖片 {image_filename} 到群組 {group_chat_id}, Topic ID {topic_id}")

    except Exception as e:
        logger.error(f"發送本地圖片時發生錯誤: {e}")
        await message.reply(f"發送失敗: {e}")
        await message.reply(f"發送失敗: {e}")

# @router.chat_member()
# async def handle_user_joined(event: ChatMemberUpdated):
#     """監聽用戶加入群組的事件並發送自定義文案"""
#     old_status = event.old_chat_member.status
#     new_status = event.new_chat_member.status

#     # 如果新狀態是 'member' 且舊狀態不是 'member'，表示用戶剛加入群組
#     if old_status != "member" and new_status == "member":
#         user = event.from_user  # 獲取用戶信息
#         chat = event.chat  # 獲取群組信息

#         # 獲取用戶名稱和用戶名
#         user_name = user.full_name  # 用戶全名
#         user_mention = f"@{user.username}" if user.username else user.full_name  # 如果用戶名存在則用 @username

#         # 自定義文案
#         welcome_message = (
#             f"📣 Dear {user_mention}, here’s the verification process:\n\n"
#             f"Step 1: Register a BYDFi account using this referral link: \n"
#             f"<a>https://partner.bydtms.com/register?vipCode=cVrA2h</a>\n\n"
#             f"Step 2: Deposit at least 20 USDT and transfer it to your Futures Account.\n\n"
#             f"Step 3: Locate your BYDFi UID and copy it.\n\n"
#             f"Step 4: Verify your account by sending the following command: /verify &lt;UID&gt; (e.g., /verify 123456789)\n\n"
#             f"CTA: Get Started! 🚀"
#         )

#         # 發送歡迎消息到群組
#         await event.bot.send_message(
#             chat_id=chat.id,
#             text=welcome_message,
#             parse_mode="HTML"  # 使用 HTML 格式化消息
#         )
#         logger.info(f"用戶 {user_name}（ID: {user.id}）加入了群組 {chat.title}（ID: {chat.id}）")

@router.chat_member()
async def handle_user_joined(event: ChatMemberUpdated):
    """監聽用戶加入群組的事件並處理邏輯"""
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    # 如果新狀態是 'member' 且舊狀態不是 'member'，表示用戶剛加入群組
    if old_status != "member" and new_status == "member":
        user = event.from_user  # 獲取用戶信息
        chat = event.chat  # 獲取群組信息

        user_name = user.full_name  # 用戶全名
        user_mention = f"@{user.username}" if user.username else user.full_name  # 如果用戶名存在則用 @username
        group_id = chat.id  # 當前群組 ID

        # API 配置
        socials_url = "http://127.0.0.1:5002/admin/telegram/social/socials"
        welcome_msg_url = "http://127.0.0.1:5002/admin/telegram/social/welcome_msg"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            # 第一步：獲取所有群組列表
            async with aiohttp.ClientSession() as session:
                async with session.post(socials_url, headers=headers) as socials_response:
                    if socials_response.status == 200:
                        socials_data = await socials_response.json()

                        # 查找與當前群組 ID 匹配的群組信息
                        target_group = next(
                            (group for group in socials_data.get("data", {}).get("chats", [])
                             if str(group.get("chatId")) == str(group_id)),
                            None
                        )

                        if target_group:
                            verify_group = target_group.get("verifyGroup")
                            if not verify_group:
                                raise ValueError("未找到 verifyGroup")

                            # 第二步：根據 verifyGroup 獲取歡迎語
                            welcome_msg_payload = {"verifyGroup": verify_group}
                            async with session.post(welcome_msg_url, headers=headers, data=welcome_msg_payload) as welcome_msg_response:
                                if welcome_msg_response.status == 200:
                                    welcome_msg_data = await welcome_msg_response.json()
                                    raw_message = welcome_msg_data.get("data", "Welcome to the group!")
                                    # 替換 {username} 為當前用戶名
                                    welcome_message = raw_message.replace("{username}", user_mention)
                                else:
                                    raise ValueError("獲取歡迎語失敗")
                        else:
                            welcome_message = "未找到與當前群組匹配的群組信息"
                    else:
                        raise ValueError("獲取群組列表失敗")
        except Exception as e:
            logger.error(f"處理 API 請求失敗: {e}")
            welcome_message = "Telegram social not found"

        # 發送歡迎消息到群組
        try:
            await event.bot.send_message(
                chat_id=group_id,
                text=welcome_message,
                parse_mode="HTML"
            )
            logger.info(f"已向用戶 {user_mention} 發送歡迎消息: {welcome_message}")
        except Exception as e:
            logger.error(f"發送歡迎消息失敗: {e}")


@router.message(Command("send_test_image"))
async def send_test_image(message: types.Message):
    """使用測試 URL 發送圖片"""
    chat_id = message.chat.id
    test_image_url = "https://via.placeholder.com/300"  # 測試圖片 URL

    try:
        # 發送圖片
        await bot.send_photo(
            chat_id=chat_id,
            photo=test_image_url,
            caption="這是一張測試圖片，來自公共測試 URL"
        )
        logger.info(f"成功發送測試圖片到 Chat ID: {chat_id}")

    except Exception as e:
        logger.error(f"發送測試圖片失敗: {e}")
        await message.reply("發送測試圖片時出現問題，請稍後再試。")

@router.message(Command("send_base64_image"))
async def send_base64_image(message: types.Message):
    """處理帶前綴的 Base64 圖片並發送到 Telegram"""
    chat_id = message.chat.id

    # Google 提供的 Base64 數據（包含前綴）
    base64_data = (
            "data:image/jpeg;base64,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")
    try:
        # 去除前綴 'data:image/jpeg;base64,'
        base64_data_clean = base64_data.split(",")[1]

        # 解碼 Base64 數據為二進制
        image_binary = base64.b64decode(base64_data_clean)

        # 保存為臨時文件
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
            temp_file.write(image_binary)
            temp_file_path = temp_file.name

        # 使用 FSInputFile 發送圖片
        photo = FSInputFile(temp_file_path)
        await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption="這是從 Base64 數據發送的圖片"
        )
        logger.info(f"成功發送 Base64 圖片到 Chat ID: {chat_id}")

    except Exception as e:
        logger.error(f"發送 Base64 圖片失敗: {e}")
        await message.reply("發送 Base64 圖片時出現問題，請稍後再試。")

    finally:
        # 刪除臨時文件
        if 'temp_file_path' in locals() and temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)

async def fetch_chat_member_count(chat_id: int):
    """通过 Telegram API 获取群组成员数量"""
    try:
        count = await bot.get_chat_member_count(chat_id)
        group_member_counts[chat_id] = count  # 更新缓存
        return count
    except Exception as e:
        logger.error(f"获取成员数量失败: {e}")
        return None

async def handle_api_request(request):
    """
    HTTP API 请求处理函数
    允许传递 chat_id 参数来查询群组成员数量
    """
    try:
        params = request.query
        chat_id = params.get("chat_id")

        if not chat_id:
            return web.json_response(
                {"status": "error", "message": "Missing 'chat_id' parameter."},
                status=400,
            )

        # 将 chat_id 转为整数
        try:
            chat_id = int(chat_id)
        except ValueError:
            return web.json_response(
                {"status": "error", "message": "'chat_id' must be an integer."},
                status=400,
            )

        # 检查缓存是否有该群组数据
        if chat_id in group_member_counts:
            member_count = group_member_counts[chat_id]
        else:
            # 缓存没有数据时，调用 Telegram API 获取
            member_count = await fetch_chat_member_count(chat_id)
            if member_count is None:
                return web.json_response(
                    {"status": "error", "message": "Failed to fetch member count."},
                    status=500,
                )

        return web.json_response(
            {"status": "success", "chat_id": chat_id, "member_count": member_count},
            status=200,
        )
    except Exception as e:
        logger.error(f"API 请求处理失败: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def start_aiohttp_server():
    """启动 HTTP API 服务器"""
    app = web.Application()
    app.router.add_get("/api/get_member_count", handle_api_request)  # 注册 API 路由
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=8080)
    await site.start()
    logger.info("HTTP API 服务器已启动，监听地址：http://0.0.0.0:8080")


async def main():
    """主程序"""
    dp.include_router(router)

    # 同时启动 Telegram Bot 和 HTTP API
    await asyncio.gather(
        start_aiohttp_server(),  # 启动 HTTP API 服务
        dp.start_polling(bot)   # 启动 Telegram Bot
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"运行失败: {e}")