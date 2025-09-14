import os
import asyncio
import aiohttp
import logging
import base64
import tempfile
import time
import aiofiles
from aiohttp import web
from functools import partial
from aiogram import Bot, Dispatcher, types, Router
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, FSInputFile
from dotenv import load_dotenv
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest
# from unpublished_posts_handler import periodic_api_check  # 引入 API 檢查模組

# 導入 Group 相關函數
from db_handler_aio import *
from unpublished_posts_handler import fetch_unpublished_posts, publish_posts
from handlers.copy_signal_handler import handle_send_copy_signal
from handlers.weekly_report_handler import handle_weekly_report
from handlers.scalp_update_handler import handle_scalp_update
from handlers.holding_report_handler import handle_holding_report
from handlers.trade_summary_handler import handle_trade_summary
from handlers.common import cleanup_dedup_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

PRODUCT_IP = os.getenv("PRODUCT_IP")
WELCOME_API = os.getenv("WELCOME_API")
VERIFY_API = os.getenv("VERIFY_API")
DETAIL_API = os.getenv("DETAIL_API")
SOCIAL_API = os.getenv("SOCIAL_API")
MESSAGE_API_URL = os.getenv("MESSAGE_API_URL")
UPDATE_MESSAGE_API_URL = os.getenv("UPDATE_MESSAGE_API_URL")
DISCORD_BOT = os.getenv("DISCORD_BOT")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# 停止信号事件
stop_event = asyncio.Event()
router = Router()
group_chat_ids = set()
verified_users = {}

ALLOWED_ADMIN_IDS = [7067100466, 7257190337, 7182693065]

async def heartbeat(bot: Bot, interval: int = 60):
    """定期向 Telegram 服务器发送心跳请求"""
    while True:
        try:
            # 调用 get_me() 测试连接状态
            me = await bot.get_me()
        except Exception as e:
            logging.error(f"Heartbeat failed: {e}")

        # 等待指定的心跳间隔
        await asyncio.sleep(interval)

def handle_stop_signal():
    """处理 SIGINT 和 SIGTERM 信号"""
    logger.info("收到停止信号，设置 stop_event...")
    stop_event.set()

async def load_active_groups():

    global group_chat_ids

    # 使用 set 来避免重复元素
    group_chat_ids.clear()

    try:
        # 添加超時處理
        active_groups = await asyncio.wait_for(get_active_groups(), timeout=10.0)
        group_chat_ids.update(active_groups)
        logger.info(f"从数据库加载了{len(active_groups)}个活跃群组")

    except asyncio.TimeoutError:
        logger.error("加载活跃群组超时，使用空列表")
        group_chat_ids.update([])
    except Exception as e:
        logger.error(f"加载活跃群组异常：{e}，使用空列表")
        group_chat_ids.update([])

@router.my_chat_member()
async def handle_my_chat_member(event: ChatMemberUpdated):
    """處理 Bot 的群組成員狀態變化"""
    try:
        chat = event.chat
        old_status = event.old_chat_member.status if event.old_chat_member else None
        new_status = event.new_chat_member.status if event.new_chat_member else None

        await event.bot.send_message(
            chat_id=chat.id,
            text=f"chat ID: {chat.id}",
            parse_mode="HTML"
        )

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

        elif new_status == 'member' or new_status == "administrator":
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

async def generate_invite_link(bot: Bot, chat_id: int) -> str:
    """
    通过 chat_id 生成群组的永久邀请链接
    """
    try:
        # 调用 Telegram API 生成邀请链接
        invite_link = await bot.export_chat_invite_link(chat_id)
        logging.info(f"生成的邀请链接: {invite_link}")
        return invite_link
    except Exception as e:
        logging.error(f"生成邀请链接失败: {e}")
        return None

async def delete_message_after_delay(chat_id: int, message_id: int, delay: int):
    """延迟删除指定消息"""
    try:
        await asyncio.sleep(delay)  # 等待指定的时间
        await bot.delete_message(chat_id=chat_id, message_id=message_id)  # 删除消息
        logger.info(f"消息已成功删除，Chat ID: {chat_id}, Message ID: {message_id}")
    except Exception as e:
        logger.error(f"删除消息时发生错误: {e}")

@router.message(Command("verify"))
async def handle_verify_command(message: types.Message):
    """处理 /verify 指令，并调用 verify 接口"""

    try:
        # 尝试删除用户的消息以防止泄露
        # try:
        #     await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        # except Exception as e:
        #     logger.error(f"无法删除用户消息: {e}")

        # 分割指令以提取验证码
        command_parts = message.text.split()
        if len(command_parts) < 2:
            await bot.send_message(
                chat_id=message.chat.id,
                text="Please provide verification code, for example: /verify 123456"
            )
            return

        verify_code = command_parts[1]
        chat_id = message.chat.id  # 当前群组 ID

        # 使用 user_id 标记用户
        user_id = str(message.from_user.id)
        user_mention = f'<a href="tg://user?id={user_id}">{message.from_user.full_name}</a>'

        # 检查用户是否已验证
        verification_status  = await is_user_verified(user_id, str(chat_id), str(verify_code))
        if verification_status == "warning":
            # 验证码属于其他用户
            await bot.send_message(
                chat_id=message.chat.id,
                text="<b>This UID has already been verified</b>",
                parse_mode="HTML"
            )
            return        

        # 获取当前群组的 owner 信息
        try:
            admins = await bot.get_chat_administrators(chat_id)
            owner = next(
                (admin for admin in admins if admin.status == "creator"), None
            )
            admin_mention = (
                f'<a href="tg://user?id={owner.user.id}">{owner.user.full_name}</a>' if owner else "@admin"
            )
        except Exception as e:
            logger.error(f"无法获取群组 {chat_id} 的管理员信息: {e}")
            admin_mention = "@admin"

        # 调用 verify API
        # verify_url = "http://127.0.0.1:5002/admin/telegram/social/verify"
        verify_url = "http://172.31.91.67:4070/admin/telegram/social/verify"
        # verify_url = "http://172.25.183.151:4070/admin/telegram/social/verify"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        verify_payload = {"verifyGroup": chat_id, "code": verify_code, "brand": "BYD", "type": "TELEGRAM"}

        async with aiohttp.ClientSession() as session_http:
            async with session_http.post(VERIFY_API, headers=headers, data=verify_payload) as response:
                response_data = await response.json()
                logger.info(f"Verify API Response: {response_data}")

                # 判断返回的状态码和数据内容
                if response.status == 200 and "verification successful" in response_data.get("data", ""):
                    # 验证成功，生成单人邀请链接
                    info_group_chat_id = None
                    # detail_url = "http://127.0.0.1:5002/admin/telegram/social/detail"
                    detail_url = "http://172.31.91.67:4070/admin/telegram/social/detail"
                    # detail_url = "http://172.25.183.151:4070/admin/telegram/social/detail"
                    detail_payload = {"verifyGroup": chat_id, "brand": "BYD", "type": "TELEGRAM"}
                    async with session_http.post(DETAIL_API, headers=headers, data=detail_payload) as detail_response:
                        detail_data = await detail_response.json()
                        verify_group_chat_id = detail_data.get("data").get("verifyGroup")  # 替换为你的资讯群 ID
                        info_group_chat_id = detail_data.get("data").get("socialGroup")  # 替换为你的资讯群 ID
                    try:
                        invite_link = await bot.create_chat_invite_link(
                            chat_id=info_group_chat_id,
                            name=f"Invite for {message.from_user.full_name}",
                            # member_limit=1,  # 限制链接只能被1人使用
                            # expire_date=int(time.time()) + 3600  # 链接1小时后过期
                        )

                        # 添加到数据库
                        await add_verified_user(user_id, str(verify_group_chat_id), str(info_group_chat_id), str(verify_code))

                        response_data["data"] = response_data["data"].replace("{Approval Link}", invite_link.invite_link)
                        response_data["data"] = response_data["data"].replace("@{username}", user_mention)
                        response_data["data"] = response_data["data"].replace("@{admin}", admin_mention)
                        response_message  = await bot.send_message(
                            chat_id=message.chat.id,
                            text=response_data["data"],
                            parse_mode="HTML"
                        )
                        asyncio.create_task(delete_message_after_delay(response_message.chat.id, response_message.message_id, 60))
                        logger.info(f"消息已发送并将在 10 分钟后自动删除，消息 ID: {response_message.message_id}")

                    except Exception as e:
                        logger.error(f"生成邀请链接失败: {e}")
                        await bot.send_message(
                            chat_id=message.chat.id,
                            text="Verification successful, but an error occurred while generating the invitation link. Please try again later."
                        )
                else:
                    # 将接口的返回数据直接返回给用户
                    error_message = response_data.get("data", "Verification failed. Please check the verification code and try again.")
                    error_message = error_message.replace("@{admin}", admin_mention)
                    await bot.send_message(
                        chat_id=message.chat.id,
                        text=error_message,
                        parse_mode="HTML"
                    )
    except Exception as e:
        logger.error(f"调用验证 API 时出错: {e}")
        await bot.send_message(
            chat_id=message.chat.id,
            text="验证时发生错误，请稍后再试。"
        )

# @router.message(Command("unban"))
# async def unban_user(message: types.Message):
#     """解除特定用户的 ban 状态"""
#     try:
#         # 检查是否为允许使用该命令的管理员
#         if message.from_user.id not in ALLOWED_ADMIN_IDS:
#             await message.reply("❌ You do not have permission to use this command.")
#             return

#         # 提取命令中的用户 ID
#         command_parts = message.text.split()
#         if len(command_parts) < 2:
#             await message.reply("❓ Please provide the user ID who needs to be unbanned. For example: /unban 123456789")
#             return

#         user_id = int(command_parts[1])  # 从命令中获取目标用户 ID
#         chat_id = message.chat.id  # 当前群组 ID

#         # 尝试解除 ban
#         await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
#         await message.reply(f"✅ User {user_id} has been successfully unbanned.")
#         logger.info(f"管理员 {message.from_user.id} 已成功解除用户 {user_id} 的 ban 状态。")

#     except TelegramBadRequest as e:
#         # 如果用户未被 ban 或其他错误
#         await message.reply(f"⚠️ {user_id} has not been banned")
#         logger.error(f"解除用户 {user_id} 的 ban 状态时发生错误：{e}")
#     except Exception as e:
#         await message.reply(f"❌ An unknown error occurred while lifting the ban, please try again later.")
#         logger.error(f"处理 /unban 命令时发生错误：{e}")

@router.message(Command("unban"))
async def unban_user(message: types.Message):
    """解除特定用户的 ban 状态"""
    try:
        # 检查是否为允许使用该命令的管理员
        if message.from_user.id not in ALLOWED_ADMIN_IDS:
            await message.reply("❌ You do not have permission to use this command.")
            return

        # 提取命令中的用户 ID
        command_parts = message.text.split()
        if len(command_parts) < 2:
            await message.reply("❓ Please provide the user ID who needs to be unbanned. For example: /unban 123456789")
            return

        user_id = int(command_parts[1])  # 从命令中获取目标用户 ID
        chat_id = message.chat.id  # 当前群组 ID

        # 检查用户是否在群组中
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status != "kicked":
                # 如果用户未被 ban
                if member.status in ["member", "administrator", "creator"]:
                    await message.reply(f"⚠️ User {user_id} is currently in the group and is not banned.")
                    return
                else:
                    # 其他状态（如已离开群组）
                    await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
                    await message.reply(f"✅ User {user_id} has been unbanned and can rejoin the group.")
                    return
        except TelegramBadRequest:
            # 如果用户不在群组或其他异常
            logger.info(f"用户 {user_id} 不在群组中或状态异常，将继续解除 ban。")

        # 尝试解除 ban
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        await message.reply(f"✅ User {user_id} has been successfully unbanned.")
        logger.info(f"管理员 {message.from_user.id} 已成功解除用户 {user_id} 的 ban 状态。")

    except TelegramBadRequest as e:
        # 如果用户未被 ban 或其他错误
        await message.reply(f"⚠️ {user_id} has not been banned or is invalid.")
        logger.error(f"解除用户 {user_id} 的 ban 状态时发生错误：{e}")
    except Exception as e:
        await message.reply(f"❌ An unknown error occurred while lifting the ban, please try again later.")
        logger.error(f"处理 /unban 命令时发生错误：{e}")

@router.message(Command("getid"))
async def get_user_id(message: types.Message):
    """返回用户的 Telegram ID"""
    user_id = message.from_user.id  # 获取发送者的用户 ID
    full_name = message.from_user.full_name  # 获取发送者的全名
    username = message.from_user.username  # 获取发送者的用户名（如果有）

    response = (
        f"✅ User ID：<code>{user_id}</code>\n"
        f"👤 Name：{full_name}\n"
    )
    if username:
        response += f"🔗 username：@{username}\n"

    await message.reply(response, parse_mode="HTML")

@router.chat_member()
async def handle_chat_member_event(event: ChatMemberUpdated):
    try:
        # 获取事件相关信息
        chat_id = event.chat.id
        user = event.new_chat_member.user  # 获取变更状态的用户信息
        user_id = str(user.id)  # 转换为字符串
        chat = event.chat

        old_status = event.old_chat_member.status if event.old_chat_member else None
        new_status = event.new_chat_member.status if event.new_chat_member else None

        logger.info(f"Chat ID: {chat_id}, User ID: {user_id}, Old Status: {old_status}, New Status: {new_status}")

        # 定义 API URLs
        # welcome_msg_url = "http://127.0.0.1:5002/admin/telegram/social/welcome_msg"
        welcome_msg_url = "http://172.31.91.67:4070/admin/telegram/social/welcome_msg"
        # social_url = "http://127.0.0.1:5002/admin/telegram/social/socials"
        social_url = "http://172.31.91.67:4070/admin/telegram/social/socials"
        # welcome_msg_url = "http://172.25.183.151:4070/admin/telegram/social/welcome_msg"
        # social_url = "http://172.25.183.151:4070/admin/telegram/social/socials"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        payload = {"verifyGroup": str(chat_id), "brand": "BYD", "type": "TELEGRAM"}

        is_verification_group = False
        welcome_message = None

        # 获取所有资讯群 ID
        social_groups = set()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(SOCIAL_API, headers=headers) as response:
                    if response.status == 200:
                        social_data = await response.json()
                        social_groups = {
                            item["socialGroup"]
                            for item in social_data.get("data", [])
                            if "socialGroup" in item
                        }
                    else:
                        logger.error(f"获取资讯群数据失败，状态码: {response.status}")
        except Exception as e:
            logger.error(f"调用 /socials 接口失败: {e}")

        if old_status != "member" and new_status == "member":
            # 如果是验证群，调用 welcome_msg_url 检查
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(WELCOME_API, headers=headers, data=payload) as response:
                        logger.info(f"监测到用户加入群组，返回数据: {await response.json()}")
                        if response.status == 200:
                            # 判断是否为验证群
                            response_data = await response.json()
                            if "data" in response_data and response_data["data"]:
                                is_verification_group = True
                                welcome_message = response_data["data"]
                            else:
                                logger.info(f"群组 {chat_id} 不是验证群")
                        else:
                            logger.error(f"验证群接口返回失败 {await response.json()}，状态码: {response.status}")
            except Exception as e:
                logger.error(f"调用验证群接口时出错: {e}")
            # 如果是验证群，发送欢迎消息
            # if is_verification_group:
            #     user_mention = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
            #     welcome_message = welcome_message.replace("@{username}", user_mention)
            #     await event.bot.send_message(chat_id=chat_id, text=welcome_message, parse_mode="HTML")
            if is_verification_group:
                user_mention = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
                # 替换 @{username} 占位符
                welcome_message = welcome_message.replace("@{username}", user_mention)

                # 提取 referral link
                referral_start = welcome_message.find("https://")
                referral_end = welcome_message.find("Step 2", referral_start) if referral_start != -1 else -1
                referral_link = None
                if referral_start != -1:
                    referral_link = (
                        welcome_message[referral_start:referral_end].strip() if referral_end != -1 else welcome_message[referral_start:]
                    )
                    referral_link = referral_link.replace("</a>", "").replace("\n", "").strip()
                if not referral_link:
                    logger.error("Referral link 提取失败，跳过欢迎消息发送")
                    return

                # 构建按钮
                # button = InlineKeyboardButton(text="Register Now", url=referral_link)
                # button_markup = InlineKeyboardMarkup(inline_keyboard=[[button]])  # 确保 inline_keyboard 是二维数组
                reply_markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Get Started!", url=referral_link)]
                    ]
                )

                # 图片路径
                current_dir = os.path.dirname(os.path.abspath(__file__))
                image_path = os.path.join(current_dir, "..", "pics", "FindUID.jpg")
                image_file = FSInputFile(image_path)
                try:
                    # 发送图片和按钮
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=image_file,
                        caption=welcome_message,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                    )
                    logger.info(f"发送欢迎图片和按钮给用户 {user_mention}")
                except Exception as e:
                    logger.error(f"发送图片失败: {e}")

            # 如果是资讯群，检查是否为验证通过的用户
            elif str(chat_id) in social_groups:
                
                if user.is_bot:
                    logger.info(f"檢測到 bot {user_id} 加入资讯群 {chat_id}")
                    return

                verified_user = await get_verified_user(user_id, chat_id)
                if not verified_user:
                    logger.warning(f"未验证用户 {user_id} 试图加入资讯群 {chat_id}，踢出...")
                    await bot.ban_chat_member(chat_id=chat_id, user_id=int(user_id))
                    # await bot.unban_chat_member(chat_id=chat_id, user_id=int(user_id))  # 可选解禁
                else:
                    # 已验证用户
                    logger.info(f"验证通过用户 {user_id} 加入资讯群 {chat_id}")

    except Exception as e:
        logger.error(f"处理 chat_member 事件时发生错误: {e}")

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
            message_thread_id=topic_id,
            parse_mode="HTML"
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

async def handle_api_request(request, bot: Bot):
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

        # 获取成员数量
        try:
            member_count = await bot.get_chat_member_count(chat_id)
            return web.json_response(
                {"status": "success", "chat_id": chat_id, "member_count": member_count},
                status=200,
            )
        except Exception as e:
            logger.error(f"获取成员数量失败: {e}")
            return web.json_response(
                {"status": "error", "message": "Failed to fetch member count."},
                status=500,
            )
    except Exception as e:
        logger.error(f"API 请求处理失败: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def handle_send_announcement(request: web.Request, *, bot: Bot):
    try:
        data = await request.json()
        content = data.get("content")
        image_url = data.get("image")

        if not content:
            return web.json_response({"status": "error", "message": "Missing 'content'"}, status=400)

        # 解析多语言内容
        try:
            if isinstance(content, str):
                # 如果是字符串，尝试解析为JSON
                import json
                content_dict = json.loads(content)
            else:
                # 如果已经是字典，直接使用
                content_dict = content
        except (json.JSONDecodeError, TypeError):
            return web.json_response({"status": "error", "message": "Invalid content format. Expected JSON object with language codes as keys."}, status=400)

        # 認證（可選）
        # auth = request.headers.get("Authorization", "")
        # if not auth or auth != "Bearer your_api_key":
        #     return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        payload = {"brand": "BYD", "type": "TELEGRAM"}

        async with aiohttp.ClientSession() as session:
            async with session.post(SOCIAL_API, headers=headers, data=payload) as resp:
                if resp.status != 200:
                    return web.json_response({"status": "error", "message": "Failed to fetch social group info"}, status=500)
                social_data = await resp.json()

        results = []

        async def send_to_channel(chat_id, topic_id, lang_content, lang_code):
            try:
                # 添加AI提示词到文案末尾
                from multilingual_utils import AI_TRANSLATE_HINT
                
                # 检查是否已经包含AI提示词
                def has_ai_hint(text):
                    """检查文本是否已经包含 AI 提示词"""
                    ai_hint_patterns = [
                        "~AI翻译", "~AI 自動翻譯", "~AI Translation",
                        "AI翻译", "AI 自動翻譯", "AI Translation",
                        "由AI", "by AI", "AI翻訳", "AI 자동 번역",
                        "仅供参考", "for reference", "参考用", "참고용"
                    ]
                    text_lower = text.lower()
                    return any(pattern.lower() in text_lower for pattern in ai_hint_patterns)
                
                # 如果内容已经包含AI提示词，不再添加；英文直接不添加
                if has_ai_hint(lang_content):
                    final_content = lang_content
                    logger.info(f"内容已包含AI提示词，不再添加")
                elif str(lang_code).lower().startswith("en"):
                    # 英文不附加 AI 提示詞
                    final_content = lang_content
                    logger.info(f"英文內容不添加 AI 提示詞")
                else:
                    # 非英文附加對應語言提示
                    hint = AI_TRANSLATE_HINT.get(lang_code, AI_TRANSLATE_HINT["en_US"])
                    final_content = lang_content + "\n" + hint
                
                # 处理HTML格式的内容
                def process_html_content(text):
                    """处理HTML格式的内容，确保链接和格式正确"""
                    # 替换Markdown链接为HTML链接
                    import re
                    # 处理 [text](url) 格式的链接
                    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
                    # 处理 **text** 格式的粗体
                    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
                    # 处理 *text* 格式的斜体
                    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
                    # 替换换行符
                    text = text.replace("<br>", "\n")
                    return text
                
                # 处理内容为HTML格式
                processed_content = process_html_content(final_content)
                
                logger.info(f"准备发送到频道 {chat_id}, topic {topic_id}, 语言 {lang_code}")
                logger.info(f"内容长度: {len(processed_content)} 字符")
                
                if image_url:
                    temp_file_path = f"/tmp/temp_image_{chat_id}_{topic_id}.jpg"
                    logger.info(f"开始下载图片: {image_url}")
                    async with aiohttp.ClientSession() as img_session:
                        async with img_session.get(image_url) as img_resp:
                            if img_resp.status == 200:
                                async with aiofiles.open(temp_file_path, "wb") as f:
                                    await f.write(await img_resp.read())
                                file = FSInputFile(temp_file_path)
                                logger.info(f"图片下载完成，开始发送到Telegram")
                                await asyncio.wait_for(bot.send_photo(
                                    chat_id=chat_id,
                                    photo=file,
                                    caption=processed_content,
                                    message_thread_id=topic_id,
                                    parse_mode="HTML"
                                ), timeout=15.0)  # 增加超时时间到15秒
                                os.remove(temp_file_path)
                                logger.info(f"图片消息发送成功")
                            else:
                                raise Exception(f"Image fetch error {img_resp.status}")
                else:
                    logger.info(f"开始发送文本消息到Telegram")
                    await asyncio.wait_for(bot.send_message(
                        chat_id=chat_id,
                        text=processed_content,
                        message_thread_id=topic_id,                        
                        parse_mode="HTML"
                    ), timeout=15.0)  # 增加超时时间到15秒
                    logger.info(f"文本消息发送成功")

                return {"chat_id": chat_id, "topic_id": topic_id, "lang": lang_code, "status": "sent"}

            except asyncio.TimeoutError:
                logger.error(f"发送到频道 {chat_id} 超时")
                return {"chat_id": chat_id, "topic_id": topic_id, "lang": lang_code, "status": "failed", "error": "Timeout while sending to Telegram"}
            except Exception as e:
                logger.error(f"发送到频道 {chat_id} 失败: {e}")
                return {"chat_id": chat_id, "topic_id": topic_id, "lang": lang_code, "status": "failed", "error": str(e)}

        # 準備所有待發送的任務
        tasks = []
        for item in social_data.get("data", []):
            chat_id = item.get("socialGroup")
            channel_lang = item.get("lang")
            
            # 如果频道没有设置语言或为null，使用默认语言"en_US"
            if not channel_lang or channel_lang is None:
                channel_lang = "en_US"
                logger.info(f"Channel {chat_id} has no language set, using default: {channel_lang}")
            
            # 查找对应的语言内容
            lang_content = content_dict.get(channel_lang)
            if not lang_content:
                logger.warning(f"No content found for language {channel_lang} in channel {chat_id}")
                continue
            
            for chat in item.get("chats", []):
                if chat.get("name") == "Announcements" and chat.get("enable"):
                    topic_id = chat.get("chatId")
                    tasks.append(send_to_channel(chat_id, topic_id, lang_content, channel_lang))
                    logger.info(f"Prepared announcement for channel {chat_id} (lang: {channel_lang})")

        # 立即返回响应，后台异步处理发送任务
        if tasks:
            logger.info(f"准备后台异步发送 {len(tasks)} 个公告任务")
            
            # 创建后台任务处理发送
            async def background_send_announcements():
                try:
                    results = []
                    logger.info(f"开始串行发送 {len(tasks)} 个公告任务")
                    for i, task in enumerate(tasks, 1):
                        logger.info(f"发送第 {i}/{len(tasks)} 个公告")
                        try:
                            result = await task
                            results.append(result)
                            # 在每次发送之间添加短暂延迟，避免API限流
                            if i < len(tasks):
                                await asyncio.sleep(1.0)
                        except Exception as e:
                            logger.error(f"发送第 {i} 个公告时发生异常: {e}")
                            results.append({"status": "failed", "error": str(e)})
                    
                    # 发送到 Discord 机器人
                    try:
                        async with aiohttp.ClientSession() as session:
                            # 发送所有语言内容到 Discord
                            dc_payload = {"content": content_dict, "image": image_url}
                            async with session.post(DISCORD_BOT, json=dc_payload) as dc_resp:
                                dc_resp_json = await dc_resp.json()
                                logger.info(f"[TG] Discord 發送結果: {dc_resp.status} - {dc_resp_json}")
                    except Exception as e:
                        logger.error(f"[TG] 呼叫 Discord 發送公告時出錯: {e}")

                    # 统计发送结果
                    success_count = sum(1 for r in results if r.get("status") == "sent")
                    failed_count = len(results) - success_count
                    
                    logger.info(f"[TG] 公告發送完成: 成功 {success_count}/{len(results)} 個頻道")
                    
                except Exception as e:
                    logger.error(f"后台发送公告时发生错误: {e}")
            
            # 启动后台任务
            asyncio.create_task(background_send_announcements())
            
            return web.json_response({
                "status": "success", 
                "message": f"公告信息佇列中... {len(tasks)} 個頻道將在背景中處理.", 
                "queued_count": len(tasks)
            }, status=200)
        else:
            logger.warning("No announcement tasks prepared")
            return web.json_response({
                "status": "success", 
                "message": "No announcement tasks prepared", 
                "queued_count": 0
            }, status=200)

    except Exception as e:
        logger.error(f"Error in handle_send_announcement: {e}")
        import traceback
        logger.error(f"詳細錯誤: {traceback.format_exc()}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def start_aiohttp_server(bot: Bot):
    """启动 HTTP API 服务器"""
    app = web.Application()
    app.router.add_get("/api/get_member_count", lambda request: handle_api_request(request, bot))
    app.router.add_post("/api/send_announcement", partial(handle_send_announcement, bot=bot))
    
    app.router.add_post("/api/send_copy_signal", partial(handle_send_copy_signal, bot=bot))
    app.router.add_post("/api/completed_trade", partial(handle_trade_summary, bot=bot))
    app.router.add_post("/api/scalp_update", partial(handle_scalp_update, bot=bot))
    app.router.add_post("/api/report/holdings", partial(handle_holding_report, bot=bot))
    app.router.add_post("/api/report/weekly", partial(handle_weekly_report, bot=bot))

    runner = web.AppRunner(app)
    await runner.setup()

    # 使用 eth0 的 IP 地址绑定接口
    target_host = PRODUCT_IP
    # target_host = "0.0.0.0"
    target_port = 5010
    site = web.TCPSite(runner, host=target_host, port=target_port)
    await site.start()

    logger.info(f"HTTP API 服务器已启动，监听地址：http://{target_host}:{target_port}")
    return runner, app

async def periodic_task(bot: Bot):
    """周期性任务，每30秒检查未发布文章并发布"""

    headers = {"Content-Type": "application/json"}

    try:
        while True:
            posts_list = await fetch_unpublished_posts(MESSAGE_API_URL, headers)

            if posts_list:
                await publish_posts(bot, posts_list, UPDATE_MESSAGE_API_URL, headers)

            # 将 sleep 逻辑分解为更小的间隔，响应性更好
            for _ in range(30):  # 分解成 30 次 1 秒的 sleep
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("周期性任务被取消，正在退出...")
        raise

async def cache_cleanup_task():
    """定期清理去重缓存的任务"""
    try:
        while True:
            await cleanup_dedup_cache()
            # 每1分钟清理一次缓存
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logger.info("缓存清理任务被取消，正在退出...")
        raise

async def main():
    """主函数"""
    try:
        logger.info("开始启动 Telegram Bot...")
        
        logger.info("加载活跃群组...")
        await load_active_groups()
        
        logger.info("设置路由器...")
        dp.include_router(router)
        
        logger.info("创建心跳任务...")
        heartbeat_task = asyncio.create_task(heartbeat(bot, interval=600))

        logger.info("创建周期性任务...")
        periodic_task_instance = asyncio.create_task(periodic_task(bot))

        logger.info("创建缓存清理任务...")
        cache_cleanup_task_instance = asyncio.create_task(cache_cleanup_task())

        logger.info("启动 HTTP API 服务器...")
        http_server_runner, _ = await start_aiohttp_server(bot)

        logger.info("启动 Telegram bot 轮询...")
        polling_task = asyncio.create_task(dp.start_polling(bot))

        logger.info("所有任务已启动，等待运行...")
        
        # 等待所有任务（除了 HTTP 服务器，它已经在运行）
        await asyncio.gather(
            heartbeat_task, 
            periodic_task_instance, 
            cache_cleanup_task_instance,
            polling_task,
            return_exceptions=True
        )

    except Exception as e:
        logger.error(f"主任务执行过程中出错: {e}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
    finally:
        logger.info("开始清理资源...")
        
        # 清理 HTTP 服务器
        if 'http_server_runner' in locals():
            try:
                await http_server_runner.cleanup()
                logger.info("HTTP 服务器已清理")
            except Exception as e:
                logger.error(f"清理 HTTP 服务器时出错: {e}")
        
        # 取消所有未完成的任务
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        logger.info(f"正在取消未完成的任务: {len(tasks)} 个")
        for task in tasks:
            task.cancel()
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.info("所有任务已成功取消")

if __name__ == "__main__":
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("捕获 KeyboardInterrupt，成功退出程序。")
    finally:
        loop.close()