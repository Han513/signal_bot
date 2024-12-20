# import base64
import os
import asyncio
import aiohttp
import logging
import base64
import tempfile
import time
import signal
from aiohttp import web
from aiogram import Bot, Dispatcher, types, Router
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, FSInputFile
from dotenv import load_dotenv
# from unpublished_posts_handler import periodic_api_check  # 引入 API 檢查模組

# 導入 Group 相關函數
from db_handler_aio import *
from unpublished_posts_handler import fetch_unpublished_posts, publish_posts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# 停止信号事件
stop_event = asyncio.Event()
router = Router()
group_chat_ids = set()
verified_users = {}

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
        active_groups = (await get_active_groups())
        group_chat_ids.update(active_groups)
        logger.info(f"从数据库加载了{len(active_groups)}个活跃群组")

    except Exception as e:
        logger.error(f"异常：{e}")

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

# @router.message(Command("verify"))
# async def handle_verify_command(message: types.Message):
#     """处理 /verify 指令，并调用 verify 接口"""

#     try:
#         # 尝试删除用户的消息以防止泄露
#         try:
#             await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
#         except Exception as e:
#             logger.error(f"无法删除用户消息: {e}")

#         # 分割指令以提取验证码
#         command_parts = message.text.split()
#         if len(command_parts) < 2:
#             await bot.send_message(
#                 chat_id=message.chat.id,
#                 text="请提供验证码，例如: /verify 123456"
#             )
#             return

#         verify_code = command_parts[1]
#         chat_id = message.chat.id  # 当前群组 ID

#         # 使用 user_id 标记用户
#         user_id = str(message.from_user.id)
#         user_mention = f'<a href="tg://user?id={user_id}">{message.from_user.full_name}</a>'

#         # 获取当前群组的 owner 信息
#         try:
#             admins = await bot.get_chat_administrators(chat_id)
#             owner = next(
#                 (admin for admin in admins if admin.status == "creator"), None
#             )
#             admin_mention = (
#                 f'<a href="tg://user?id={owner.user.id}">{owner.user.full_name}</a>' if owner else "@admin"
#             )
#         except Exception as e:
#             logger.error(f"无法获取群组 {chat_id} 的管理员信息: {e}")
#             admin_mention = "@admin"

#         # 调用 verify API
#         # verify_url = "http://127.0.0.1:5002/admin/telegram/social/verify"
#         verify_url = "http://172.25.183.151:4070/admin/telegram/social/verify"
#         headers = {"Content-Type": "application/x-www-form-urlencoded"}
#         verify_payload = {"verifyGroup": chat_id, "code": verify_code, "brand": "BYD", "type": "TELEGRAM"}

#         async with aiohttp.ClientSession() as session_http:
#             async with session_http.post(verify_url, headers=headers, data=verify_payload) as response:
#                 response_data = await response.json()
#                 logger.info(f"Verify API Response: {response_data}")

#                 # 判断返回的状态码和数据内容
#                 if response.status == 200 and "verification successful" in response_data.get("data", ""):
#                     # 验证成功，生成单人邀请链接
#                     info_group_chat_id=None
#                     # detail_url = "http://127.0.0.1:5002/admin/telegram/social/detail"
#                     detail_url = "http://172.25.183.151:4070/admin/telegram/social/detail"
#                     detail_payload = {"verifyGroup": chat_id, "brand": "BYD", "type": "TELEGRAM"}
#                     async with session_http.post(detail_url, headers=headers, data=detail_payload) as detail_response:
#                         detail_data = await detail_response.json()
#                         info_group_chat_id = detail_data.get("data").get("socialGroup")  # 替换为你的资讯群 ID
#                     try:
#                         invite_link = await bot.create_chat_invite_link(
#                             chat_id=info_group_chat_id,
#                             name=f"Invite for {message.from_user.full_name}",
#                             member_limit=1,  # 限制链接只能被1人使用
#                             expire_date=int(time.time()) + 3600  # 链接1小时后过期
#                         )

#                         # 添加到数据库
#                         await add_verified_user(user_id, str(info_group_chat_id))

#                         response_data["data"] = response_data["data"].replace("{Approval Link}", invite_link.invite_link)
#                         response_data["data"] = response_data["data"].replace("@{username}", user_mention)
#                         response_data["data"] = response_data["data"].replace("@{admin}", admin_mention)
#                         await bot.send_message(
#                             chat_id=message.chat.id,
#                             text=response_data["data"],
#                             parse_mode="HTML"
#                         )
#                         logger.info(f"生成的受限制邀请链接：{invite_link.invite_link}")

#                     except Exception as e:
#                         logger.error(f"生成邀请链接失败: {e}")
#                         await bot.send_message(
#                             chat_id=message.chat.id,
#                             text="Verification successful, but an error occurred while generating the invitation link. Please try again later."
#                         )
#                 else:
#                     # 将接口的返回数据直接返回给用户
#                     error_message = response_data.get("data", "Verification failed. Please check the verification code and try again.")
#                     error_message = error_message.replace("@{admin}", admin_mention)
#                     await bot.send_message(
#                         chat_id=message.chat.id,
#                         text=error_message,
#                         parse_mode="HTML"
#                     )
#     except Exception as e:
#         logger.error(f"调用验证 API 时出错: {e}")
#         await bot.send_message(
#             chat_id=message.chat.id,
#             text="验证时发生错误，请稍后再试。"
#         )
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
                text="请提供验证码，例如: /verify 123456"
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
        verify_url = "http://172.25.183.151:4070/admin/telegram/social/verify"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        verify_payload = {"verifyGroup": chat_id, "code": verify_code, "brand": "BYD", "type": "TELEGRAM"}

        async with aiohttp.ClientSession() as session_http:
            async with session_http.post(verify_url, headers=headers, data=verify_payload) as response:
                response_data = await response.json()
                logger.info(f"Verify API Response: {response_data}")

                # 判断返回的状态码和数据内容
                if response.status == 200 and "verification successful" in response_data.get("data", ""):
                    # 验证成功，生成单人邀请链接
                    info_group_chat_id = None
                    # detail_url = "http://127.0.0.1:5002/admin/telegram/social/detail"
                    detail_url = "http://172.25.183.151:4070/admin/telegram/social/detail"
                    detail_payload = {"verifyGroup": chat_id, "brand": "BYD", "type": "TELEGRAM"}
                    async with session_http.post(detail_url, headers=headers, data=detail_payload) as detail_response:
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
                        asyncio.create_task(delete_message_after_delay(response_message.chat.id, response_message.message_id, 10))
                        logger.info(f"消息已发送并将在 30 分钟后自动删除，消息 ID: {response_message.message_id}")

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

@router.chat_member()
async def handle_chat_member_event(event: ChatMemberUpdated):
    """统一处理 chat_member 事件"""

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
        welcome_msg_url = "http://172.25.183.151:4070/admin/telegram/social/welcome_msg"
        # social_url = "http://127.0.0.1:5002/admin/telegram/social/socials"
        social_url = "http://172.25.183.151:4070/admin/telegram/social/socials"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        payload = {"verifyGroup": str(chat_id), "brand": "BYD", "type": "TELEGRAM"}

        is_verification_group = False
        welcome_message = None

        # 获取所有资讯群 ID
        social_groups = set()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(social_url, headers=headers) as response:
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
                    async with session.post(welcome_msg_url, headers=headers, data=payload) as response:
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
            if is_verification_group:
                user_mention = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
                welcome_message = welcome_message.replace("@{username}", user_mention)
                await event.bot.send_message(chat_id=chat_id, text=welcome_message, parse_mode="HTML")

            # 如果是资讯群，检查是否为验证通过的用户
            elif str(chat_id) in social_groups:
                # verified_user = await is_user_verified(user_id)  # 检查是否已验证
                verified_user = await get_verified_user(user_id, chat_id)
                print(verified_user)
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

async def start_aiohttp_server(bot: Bot):
    """启动 HTTP API 服务器"""
    app = web.Application()
    app.router.add_get("/api/get_member_count", lambda request: handle_api_request(request, bot))

    runner = web.AppRunner(app)
    await runner.setup()

    # 使用 eth0 的 IP 地址绑定接口
    target_host = "172.25.183.177"  # 绑定到服务器的实际 IP 地址
    target_port = 5010             # 可自定义的端口
    site = web.TCPSite(runner, host=target_host, port=target_port)
    await site.start()

    logger.info(f"HTTP API 服务器已启动，监听地址：http://{target_host}:{target_port}")
    return runner, app

async def periodic_task(bot: Bot):
    """周期性任务，每30秒检查未发布文章并发布"""
    posts_url = "http://172.25.183.139:5003/bot/posts/list?status=0"
    update_url = "http://172.25.183.139:5003/bot/posts/edit"
    # posts_url = "http://127.0.0.1:5003/bot/posts/list?status=0"
    # update_url = "http://127.0.0.1:5003/bot/posts/edit"
    headers = {"Content-Type": "application/json"}
    # payload = {"status": 0}  # 未发布文章的状态

    try:
        while True:
            posts_list = await fetch_unpublished_posts(posts_url, headers)

            if posts_list:
                await publish_posts(bot, posts_list, update_url, headers)

            # 将 sleep 逻辑分解为更小的间隔，响应性更好
            for _ in range(30):  # 分解成 30 次 1 秒的 sleep
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("周期性任务被取消，正在退出...")
        raise

async def main():
    """主函数"""
    try:
        await load_active_groups()
        dp.include_router(router)

        heartbeat_task = asyncio.create_task(heartbeat(bot, interval=600))

        # 启动周期性任务
        periodic_task_instance = asyncio.create_task(periodic_task(bot))

        # 启动 HTTP API 服务器
        # http_server_runner, _ = await start_aiohttp_server(bot)

        # 启动 Telegram bot 轮询
        polling_task = asyncio.create_task(dp.start_polling(bot))

        # 等待任务完成（不再显式等待信号）
        await asyncio.gather(heartbeat_task, periodic_task_instance, polling_task)
        # await asyncio.gather(polling_task)

    except Exception as e:
        logger.error(f"主任务执行过程中出错: {e}")
    finally:
        # 取消所有未完成的任务
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        logger.info(f"正在取消未完成的任务: {tasks}")
        for task in tasks:
            task.cancel()
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