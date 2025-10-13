import os
import asyncio
import aiohttp
import logging
import base64
import tempfile
import time
import aiofiles
import re
from aiohttp import web
from typing import Optional
from functools import partial
from aiogram import Bot, Dispatcher, types, Router
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, FSInputFile
from aiogram.types import ForceReply
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
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
from multilingual_utils import apply_rtl_if_needed
from bot_manager import BotManager

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
BOT_REGISTER_API_KEY = os.getenv("BOT_REGISTER_API_KEY")
DEFAULT_BRAND = os.getenv("DEFAULT_BRAND", "BYD")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# 停止信号事件
stop_event = asyncio.Event()
router = Router()
bot_manager = BotManager(shared_router=router)
group_chat_ids = set()
verified_users = {}

ALLOWED_ADMIN_IDS = [7067100466, 7257190337, 7182693065]

_VERIFY_PROMPT_MARKER = "[VERIFY_PROMPT]"
_PENDING_VERIFY_GID = {}

_BOT_NAME_CACHE = {}

async def get_bot_display_name(bot: Bot) -> str:
    """取得 Bot 顯示名稱（@username 或 first_name），帶快取以降低 API 次數。"""
    try:
        bid = bot.id
    except Exception:
        return "unknown"
    name = _BOT_NAME_CACHE.get(bid)
    if name:
        return name
    try:
        me = await bot.get_me()
        name = (getattr(me, "username", None) or getattr(me, "first_name", None) or str(bid))
    except Exception:
        name = str(bid)
    _BOT_NAME_CACHE[bid] = name
    return name

# -------------------- 動態 Bot 持久化（重啟自動恢復） --------------------
_AGENTS_STORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "run", "bots.json")

def _load_agents_store() -> list:
    try:
        path = os.path.abspath(_AGENTS_STORE_PATH)
        if not os.path.exists(path):
            return []
        import json
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except Exception as e:
        logger.error(f"load agents store failed: {e}")
        return []

def _save_agents_store(items: list) -> None:
    try:
        path = os.path.abspath(_AGENTS_STORE_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"save agents store failed: {e}")

def _persist_agent(token: str, brand: str, proxy: Optional[str]) -> None:
    items = _load_agents_store()
    # 去重（以 token 為鍵）
    exists = False
    for it in items:
        if it.get("token") == token:
            it["brand"] = brand
            it["proxy"] = proxy
            it["enabled"] = True
            exists = True
            break
    if not exists:
        items.append({
            "token": token,
            "brand": brand,
            "proxy": proxy,
            "enabled": True,
        })
    _save_agents_store(items)

def _build_agent_router() -> Router:
    r = Router()
    # group/private verify
    r.message.register(handle_verify_command, Command("verify"))
    r.message.register(handle_private_verify_command, Command("pverify"))
    r.message.register(handle_verify_shortcut, Command("verify"))
    # start + free text（menu 已停用）
    r.message.register(handle_start, Command("start"))
    r.message.register(show_menu, Command("menu"))
    r.message.register(handle_private_free_text)
    # admin utils
    r.message.register(unban_user, Command("unban"))
    r.message.register(get_user_id, Command("getid"))
    # chat member & callbacks
    r.chat_member.register(handle_chat_member_event)
    r.my_chat_member.register(handle_my_chat_member)
    r.callback_query.register(handle_inline_callbacks)
    return r

async def start_persisted_agents(manager: BotManager):
    items = _load_agents_store()
    if not items:
        logger.info("No persisted agents to restore")
        return
    logger.info(f"Restoring {len(items)} persisted agents...")
    for it in items:
        try:
            token = it.get("token")
            brand = it.get("brand") or DEFAULT_BRAND
            proxy = it.get("proxy")
            enabled = bool(it.get("enabled", True))
            if not enabled or not token or token == TOKEN:
                continue
            await bot_manager.register_and_start_bot(
                token=token,
                brand=brand,
                proxy=proxy,
                heartbeat_coro_factory=lambda b: heartbeat(b, interval=600),
                periodic_coro_factory=None,
                max_idle_seconds=None,
                idle_check_interval=3600,
                router_factory=_build_agent_router,
            )
            logger.info(f"Restored agent bot for brand={brand}")
        except Exception as e:
            logger.error(f"Restore agent failed: {e}")
@router.callback_query()
async def handle_inline_callbacks(callback: types.CallbackQuery):
    try:
        data = callback.data or ""
        bot_name = await get_bot_display_name(callback.bot)
        src_text = getattr(callback.message, "text", None) or getattr(callback.message, "caption", "")
        logger.info(f"[callback] bot={bot_name}({callback.bot.id}) user={callback.from_user.id} data={data} msg_text={src_text!r}")
        if data.startswith("verify|"):
            _, verify_group_id = data.split("|", 1)
            if not verify_group_id:
                await callback.message.answer("Please provide verify group id with /pverify <verify_group_id> <code>.")
                await callback.answer()
                return
            _PENDING_VERIFY_GID[str(callback.from_user.id)] = verify_group_id
            logger.info(f"[callback] bot={bot_name} set pending verify_group_id={verify_group_id} for user={callback.from_user.id}")
            await callback.message.bot.send_message(
                chat_id=callback.message.chat.id,
                text="Please enter your UID.",
                reply_markup=ForceReply(selective=True)
            )
            await callback.answer()
        else:
            await callback.answer()
    except Exception as e:
        logger.error(f"handle_inline_callbacks error: {e}")

async def _perform_private_verify_flow(message: types.Message, verify_group_id: Optional[str], verify_code: str, current_brand: str):
    """執行私聊驗證流程（PRIVATE 模式）。
    - 若無 verify_group_id，僅以 botId/botName 與後端溝通，由後端映射到對應群組
    """
    try:
        user_id = str(message.from_user.id)
        user_mention = f'<a href="tg://user?id={user_id}">{message.from_user.full_name}</a>'

        verification_status = await is_user_verified(user_id, str(verify_group_id), str(verify_code))
        if verification_status == "warning":
            await message.bot.send_message(
                chat_id=message.chat.id,
                text="<b>This UID has already been verified</b>",
                parse_mode="HTML"
            )
            return

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        admin_mention = "admin"
        bot_name_for_api = await get_bot_display_name(message.bot)
        verify_payload = {
            "code": verify_code,
            "brand": current_brand,
            "type": "TELEGRAM",
            "botId": message.bot.id,
            "botName": bot_name_for_api,
            "mode": "PRIVATE",
        }
        if verify_group_id:
            verify_payload["verifyGroup"] = verify_group_id
        async with aiohttp.ClientSession() as session_http:
            async with session_http.post(VERIFY_API, headers=headers, data=verify_payload) as response:
                response_data = await response.json()
                if response.status == 200 and "verification successful" in response_data.get("data", ""):
                    detail_payload = {
                        "brand": current_brand,
                        "type": "TELEGRAM",
                        "botId": message.bot.id,
                        "botName": bot_name_for_api,
                        "mode": "PRIVATE",
                    }
                    if verify_group_id:
                        detail_payload["verifyGroup"] = verify_group_id
                    async with session_http.post(DETAIL_API, headers=headers, data=detail_payload) as detail_response:
                        detail_data = await detail_response.json()
                        verify_group_chat_id = detail_data.get("data", {}).get("verifyGroup")
                        info_group_chat_id = detail_data.get("data", {}).get("socialGroup")

                    try:
                        invite_link = await message.bot.create_chat_invite_link(
                            chat_id=info_group_chat_id,
                            name=f"Invite for {message.from_user.full_name}",
                        )
                        await add_verified_user(user_id, str(verify_group_chat_id), str(info_group_chat_id), str(verify_code))
                        response_data["data"] = response_data["data"].replace("{Approval Link}", invite_link.invite_link)
                        response_data["data"] = response_data["data"].replace("@{username}", user_mention)
                        # 移除 @{admin} 替換
                        await message.bot.send_message(chat_id=message.chat.id, text=response_data["data"], parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"[pverify] 生成邀请链接失败: {e}")
                        await message.bot.send_message(chat_id=message.chat.id, text="Verification successful, but an error occurred while generating the invitation link. Please try again later.")
                else:
                    error_message = response_data.get("data", "Verification failed. Please check the verification code and try again.")
                    # 移除 @{admin} 替換
                    await message.bot.send_message(chat_id=message.chat.id, text=error_message, parse_mode="HTML")
    except Exception as e:
        logger.error(f"_perform_private_verify_flow error: {e}")

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

async def delete_message_after_delay(bot: Bot, chat_id: int, message_id: int, delay: int):
    """延迟删除指定消息（使用傳入的 Bot 實例）"""
    try:
        await asyncio.sleep(delay)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"消息已成功删除，Chat ID: {chat_id}, Message ID: {message_id}")
    except Exception as e:
        logger.error(f"删除消息时发生错误: {e}")

@router.message(Command("verify"))
async def handle_verify_command(message: types.Message):
    """处理 /verify 指令，并调用 verify 接口"""

    try:
        # 記錄活動
        try:
            bot_manager.record_activity(message.bot.id)
        except Exception:
            pass
        # 尝试删除用户的消息以防止泄露
        # try:
        #     await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        # except Exception as e:
        #     logger.error(f"无法删除用户消息: {e}")

        # 分割指令以提取验证码
        command_parts = message.text.split()
        if len(command_parts) < 2:
            await message.bot.send_message(
                chat_id=message.chat.id,
                text="Please provide verification code, for example: /verify 123456"
            )
            return

        verify_code = command_parts[1]
        chat_id = message.chat.id  # 当前群组 ID
        current_brand = bot_manager.get_brand_by_bot_id(message.bot.id, DEFAULT_BRAND)

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
            admins = await message.bot.get_chat_administrators(chat_id)
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
        verify_payload = {"verifyGroup": chat_id, "code": verify_code, "brand": current_brand, "type": "TELEGRAM"}

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
                    detail_payload = {"verifyGroup": chat_id, "brand": current_brand, "type": "TELEGRAM"}
                    async with session_http.post(DETAIL_API, headers=headers, data=detail_payload) as detail_response:
                        detail_data = await detail_response.json()
                        verify_group_chat_id = detail_data.get("data").get("verifyGroup")  # 替换为你的资讯群 ID
                        info_group_chat_id = detail_data.get("data").get("socialGroup")  # 替换为你的资讯群 ID
                    try:
                        invite_link = await message.bot.create_chat_invite_link(
                            chat_id=info_group_chat_id,
                            name=f"Invite for {message.from_user.full_name}",
                            # member_limit=1,  # 限制链接只能被1人使用
                            # expire_date=int(time.time()) + 3600  # 链接1小时后过期
                        )

                        # 添加到数据库
                        await add_verified_user(user_id, str(verify_group_chat_id), str(info_group_chat_id), str(verify_code))

                        response_data["data"] = response_data["data"].replace("{Approval Link}", invite_link.invite_link)
                        response_data["data"] = response_data["data"].replace("@{username}", user_mention)
                        # 移除 @{admin} 替換
                        response_message  = await message.bot.send_message(
                            chat_id=message.chat.id,
                            text=response_data["data"],
                            parse_mode="HTML"
                        )
                        asyncio.create_task(delete_message_after_delay(message.bot, response_message.chat.id, response_message.message_id, 60))
                        logger.info(f"消息已发送并将在 10 分钟后自动删除，消息 ID: {response_message.message_id}")

                    except Exception as e:
                        logger.error(f"生成邀请链接失败: {e}")
                        await message.bot.send_message(
                            chat_id=message.chat.id,
                            text="Verification successful, but an error occurred while generating the invitation link. Please try again later."
                        )
                else:
                    # 将接口的返回数据直接返回给用户
                    error_message = response_data.get("data", "Verification failed. Please check the verification code and try again.")
                    # 移除 @{admin} 替換
                    await message.bot.send_message(
                        chat_id=message.chat.id,
                        text=error_message,
                        parse_mode="HTML"
                    )
    except Exception as e:
        logger.error(f"调用验证 API 时出错: {e}")
        await message.bot.send_message(
            chat_id=message.chat.id,
            text="验证时发生错误，请稍后再试。"
        )

@router.message(Command("pverify"))
async def handle_private_verify_command(message: types.Message):
    """私聊驗證：/pverify <verify_group_id> <code>，僅允許在私聊使用。"""
    try:
        # 記錄活動
        try:
            bot_manager.record_activity(message.bot.id)
        except Exception:
            pass
        if message.chat.type != "private":
            await message.reply("This command can only be used in private chat.")
            return

        parts = message.text.split()
        if len(parts) < 3:
            await message.reply("Usage: /pverify <verify_group_id> <code>")
            return

        verify_group_id = parts[1]
        verify_code = parts[2]
        current_brand = bot_manager.get_brand_by_bot_id(message.bot.id, DEFAULT_BRAND)

        user_id = str(message.from_user.id)
        user_mention = f'<a href="tg://user?id={user_id}">{message.from_user.full_name}</a>'

        # 檢查 UID 是否已被其他人使用
        verification_status = await is_user_verified(user_id, str(verify_group_id), str(verify_code))
        if verification_status == "warning":
            await message.bot.send_message(
                chat_id=message.chat.id,
                text="<b>This UID has already been verified</b>",
                parse_mode="HTML"
            )
            return

        admin_mention = "@admin"  # 私聊情境無法取得群擁有者

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        bot_name_for_api = await get_bot_display_name(message.bot)
        verify_payload = {
            "code": verify_code,
            "brand": current_brand,
            "type": "TELEGRAM",
            "botId": message.bot.id,
            "botName": bot_name_for_api,
            "mode": "PRIVATE",
        }
        if verify_group_id:
            verify_payload["verifyGroup"] = verify_group_id

        async with aiohttp.ClientSession() as session_http:
            async with session_http.post(VERIFY_API, headers=headers, data=verify_payload) as response:
                response_data = await response.json()
                logger.info(f"[pverify] Verify API Response: {response_data}")

                if response.status == 200 and "verification successful" in response_data.get("data", ""):
                    detail_payload = {
                        "brand": current_brand,
                        "type": "TELEGRAM",
                        "botId": message.bot.id,
                        "botName": bot_name_for_api,
                        "mode": "PRIVATE",
                    }
                    if verify_group_id:
                        detail_payload["verifyGroup"] = verify_group_id
                    async with session_http.post(DETAIL_API, headers=headers, data=detail_payload) as detail_response:
                        detail_data = await detail_response.json()
                        verify_group_chat_id = detail_data.get("data").get("verifyGroup")
                        info_group_chat_id = detail_data.get("data").get("socialGroup")

                    try:
                        invite_link = await message.bot.create_chat_invite_link(
                            chat_id=info_group_chat_id,
                            name=f"Invite for {message.from_user.full_name}",
                        )

                        await add_verified_user(user_id, str(verify_group_chat_id), str(info_group_chat_id), str(verify_code))

                        response_data["data"] = response_data["data"].replace("{Approval Link}", invite_link.invite_link)
                        response_data["data"] = response_data["data"].replace("@{username}", user_mention)
                        # 移除 @{admin} 替換

                        await message.bot.send_message(
                            chat_id=message.chat.id,
                            text=response_data["data"],
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"[pverify] 生成邀请链接失败: {e}")
                        await message.bot.send_message(
                            chat_id=message.chat.id,
                            text="Verification successful, but an error occurred while generating the invitation link. Please try again later."
                        )
                else:
                    error_message = response_data.get("data", "Verification failed. Please check the verification code and try again.")
                    # 移除 @{admin} 替換
                    await message.bot.send_message(
                        chat_id=message.chat.id,
                        text=error_message,
                        parse_mode="HTML"
                    )
    except Exception as e:
        logger.error(f"[pverify] 調用驗證 API 時出錯: {e}")
        await message.bot.send_message(
            chat_id=message.chat.id,
            text="验证时发生错误，请稍后再试。"
        )

@router.message(Command("verify"))
async def handle_verify_shortcut(message: types.Message):
    """允許在私聊使用 /verify <code> 作為快速驗證入口（兼容需求）。"""
    try:
        try:
            bot_manager.record_activity(message.bot.id)
        except Exception:
            pass

        if message.chat.type != "private":
            return  # 保留原本群組 /verify

        parts = message.text.split()
        if len(parts) < 2:
            await message.reply("Usage: /verify <code>")
            return

        # 轉呼叫 /pverify 流程（需要 verify_group_id，若未綁定則提示）
        await message.reply("Please use /pverify <verify_group_id> <code>")
    except Exception as e:
        logger.error(f"handle_verify_shortcut error: {e}")


@router.message(Command("menu"))
async def show_menu(message: types.Message):
    """已停用：不再顯示 menu，回覆簡短提示。"""
    try:
        if message.chat.type != "private":
            return
        await message.bot.send_message(chat_id=message.chat.id, text="Please press /start to begin verification.")
    except Exception as e:
        logger.error(f"show_menu error: {e}")


@router.message(Command("start"))
async def handle_start(message: types.Message):
    """私聊點擊 /start 時給歡迎語與一鍵驗證按鈕。"""
    try:
        if message.chat.type != "private":
            return
        try:
            bot_manager.record_activity(message.bot.id)
        except Exception:
            pass

        current_brand = bot_manager.get_brand_by_bot_id(message.bot.id, DEFAULT_BRAND)

        # 先嘗試私聊模式的歡迎語：以 botId/botName 溝通
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        chosen_verify_group = None
        welcome_message = None
        try:
            bot_name_for_api = await get_bot_display_name(message.bot)
            payload_private = {
                "brand": current_brand,
                "type": "TELEGRAM",
                "botId": message.bot.id,
                "botName": bot_name_for_api,
                "mode": "PRIVATE",
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(WELCOME_API, headers=headers, data=payload_private) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # 允許後端回傳 data（歡迎語），可選回傳 verifyGroup
                        if data.get("data"):
                            welcome_message = data.get("data")
                            chosen_verify_group = (data.get("verifyGroup") or data.get("data", {}).get("verifyGroup") or None)
        except Exception:
            pass

        # 若私聊模式未取到，退回群組模式（遍歷已知 verify 群）
        if not welcome_message:
            for gid in list(group_chat_ids):
                payload = {"verifyGroup": str(gid), "brand": current_brand, "type": "TELEGRAM"}
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(WELCOME_API, headers=headers, data=payload) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if data.get("data"):
                                    chosen_verify_group = str(gid)
                                    welcome_message = data.get("data")
                                    break
                except Exception:
                    continue

        # 構建 Verify 按鈕（帶 verifyGroup 提示）
        verify_callback = f"verify|{chosen_verify_group or ''}"
        inline_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Verify", callback_data=verify_callback)]]
        )
        bot_name = await get_bot_display_name(message.bot)
        logger.info(f"[start] bot={bot_name}({message.bot.id}) built verify button with callback={verify_callback}")

        # 替換 username 並修正不合法的 <code> 標籤
        user_mention = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.full_name}</a>'
        if welcome_message:
            safe_text = welcome_message.replace("@{username}", user_mention)
            safe_text = safe_text.replace("<code>", "`").replace("</code>", "`")
            await message.bot.send_message(chat_id=message.chat.id, text=safe_text, parse_mode="HTML", reply_markup=inline_kb)
        else:
            fallback = (
                f"Welcome! You are chatting with the {current_brand} verification bot.\n\n"
                "Tap the Verify button below to start verification."
            )
            await message.bot.send_message(chat_id=message.chat.id, text=fallback, reply_markup=inline_kb)
    except Exception as e:
        logger.error(f"handle_start error: {e}")


@router.message()
async def handle_private_free_text(message: types.Message):
    """
    私聊自由輸入處理：
    1) /verify <digits> 視為驗證請求（已由 handle_verify_shortcut 引導，這裡防禦性處理）
    2) 純數字 => 視為驗證請求
    3) 無數字 => 忽略
    4) 混合文字但包含數字 => 視為驗證請求
    只在私聊觸發，群組交給群內 handler。
    """
    try:
        if message.chat.type != "private":
            return

        text = (message.text or "").strip()
        if not text:
            return

        # 忽略除 /verify,/pverify 以外的命令
        if text.startswith("/") and not text.lower().startswith("/verify") and not text.lower().startswith("/pverify"):
            return

        try:
            bot_manager.record_activity(message.bot.id)
        except Exception:
            pass

        # 若是我們用 ForceReply 彈出的提示，則更友善地解析
        is_forced_reply = message.reply_to_message and message.reply_to_message.text and _VERIFY_PROMPT_MARKER in message.reply_to_message.text

        # 如果先前按了 Verify 並記錄 verify_group_id，且此訊息是純數字，則直接驗證
        pending_gid = _PENDING_VERIFY_GID.get(str(message.from_user.id))

        # 先嘗試從文本擷取數字
        m = re.search(r"\d{4,}", text)
        if not m:
            # 無數字：忽略
            return

        code = m.group(0)
        current_brand = bot_manager.get_brand_by_bot_id(message.bot.id, DEFAULT_BRAND)
        if pending_gid:
            await _perform_private_verify_flow(message, pending_gid, code, current_brand)
            # 清除 pending
            _PENDING_VERIFY_GID.pop(str(message.from_user.id), None)
            return

        # 未知 verify_group_id：提示用戶補上
        await message.bot.send_message(chat_id=message.chat.id, text=("Detected verification code.\nPlease send: /pverify <verify_group_id> " + code))
    except Exception as e:
        logger.error(f"handle_private_free_text error: {e}")

@router.message(Command("unban"))
async def unban_user(message: types.Message):
    """解除特定用户的 ban 状态"""
    try:
        try:
            bot_manager.record_activity(message.bot.id)
        except Exception:
            pass
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
            member = await message.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status != "kicked":
                # 如果用户未被 ban
                if member.status in ["member", "administrator", "creator"]:
                    await message.reply(f"⚠️ User {user_id} is currently in the group and is not banned.")
                    return
                else:
                    # 其他状态（如已离开群组）
                    await message.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
                    await message.reply(f"✅ User {user_id} has been unbanned and can rejoin the group.")
                    return
        except TelegramBadRequest:
            # 如果用户不在群组或其他异常
            logger.info(f"用户 {user_id} 不在群组中或状态异常，将继续解除 ban。")

        # 尝试解除 ban
        await message.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
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
    try:
        bot_manager.record_activity(message.bot.id)
    except Exception:
        pass
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
        try:
            bot_manager.record_activity(event.bot.id)
        except Exception:
            pass
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
        current_brand = bot_manager.get_brand_by_bot_id(event.bot.id, DEFAULT_BRAND)
        payload = {"verifyGroup": str(chat_id), "brand": current_brand, "type": "TELEGRAM"}

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
                    await event.bot.ban_chat_member(chat_id=chat_id, user_id=int(user_id))
                    # await bot.unban_chat_member(chat_id=chat_id, user_id=int(user_id))  # 可选解禁
                else:
                    # 已验证用户
                    logger.info(f"验证通过用户 {user_id} 加入资讯群 {chat_id}")

    except Exception as e:
        logger.error(f"处理 chat_member 事件时发生错误: {e}")

@router.message(Command("send_to_topic"))
async def send_to_specific_topic(message: types.Message):
    """測試從本地文件夾發送圖片"""
    try:
        bot_manager.record_activity(message.bot.id)
    except Exception:
        pass
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
        try:
            bot_manager.record_activity(bot.id)
        except Exception:
            pass
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
        try:
            bot_manager.record_activity(bot.id)
        except Exception:
            pass
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
                # 為 RTL 語言自動加入方向控制字元（不影響可見文字）
                processed_content = apply_rtl_if_needed(processed_content)
                
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

async def start_aiohttp_server(bot: Bot, manager: BotManager):
    """启动 HTTP API 服务器"""
    app = web.Application()
    app.router.add_get("/api/get_member_count", lambda request: handle_api_request(request, bot))
    app.router.add_post("/api/send_announcement", partial(handle_send_announcement, bot=bot))
    
    app.router.add_post("/api/send_copy_signal", partial(handle_send_copy_signal, bot=bot))
    app.router.add_post("/api/completed_trade", partial(handle_trade_summary, bot=bot))
    app.router.add_post("/api/scalp_update", partial(handle_scalp_update, bot=bot))
    app.router.add_post("/api/report/holdings", partial(handle_holding_report, bot=bot))
    app.router.add_post("/api/report/weekly", partial(handle_weekly_report, bot=bot))

    # 多 Bot 管理端點
    async def _require_auth(request: web.Request):
        auth = request.headers.get("Authorization", "")
        if not BOT_REGISTER_API_KEY or not auth.startswith("Bearer ") or auth.split(" ", 1)[1] != BOT_REGISTER_API_KEY:
            raise web.HTTPUnauthorized()

    async def handle_register_bot(request: web.Request):
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)

        # 僅需 token 與 brand，brand 必須為 BYD
        token = payload.get("token")
        brand = (payload.get("brand") or DEFAULT_BRAND).strip()
        if not token or not brand:
            return web.json_response({"status": "error", "message": "Missing token or brand"}, status=400)
        if brand != "BYD":
            return web.json_response({"status": "error", "message": "Invalid brand."}, status=400)

        try:
            def _router_factory():
                # 為動態 Bot 構建新的 Router，註冊相同的 handlers（避免重複附加已存在的 router 實例）
                r = Router()
                # group/private verify
                r.message.register(handle_verify_command, Command("verify"))
                r.message.register(handle_private_verify_command, Command("pverify"))
                r.message.register(handle_verify_shortcut, Command("verify"))
                # menu + start + free text
                r.message.register(show_menu, Command("menu"))
                r.message.register(handle_start, Command("start"))
                r.message.register(handle_private_free_text)
                # admin utils
                r.message.register(unban_user, Command("unban"))
                r.message.register(get_user_id, Command("getid"))
                # chat member events
                r.chat_member.register(handle_chat_member_event)
                r.my_chat_member.register(handle_my_chat_member)
                # callback handlers
                r.callback_query.register(handle_inline_callbacks)
                return r

            result = await manager.register_and_start_bot(
                token=token,
                brand=brand,
                proxy=None,
                heartbeat_coro_factory=lambda b: heartbeat(b, interval=600),
                # 動態代理 Bot 不啟動全域排程，只保留心跳與輪詢
                periodic_coro_factory=None,
                # 低頻保活：不自動停用（max_idle_seconds=None）
                max_idle_seconds=None,
                idle_check_interval=3600,
                router_factory=_router_factory,
            )
            # 持久化這個代理 bot，方便重啟恢復
            try:
                _persist_agent(token, brand, None)
            except Exception as e:
                logger.error(f"persist agent failed: {e}")

            return web.json_response({"status": "success", **result})
        except Exception as e:
            logger.error(f"register bot failed: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def handle_list_bots(request: web.Request):
        await _require_auth(request)
        return web.json_response({"status": "success", "bots": manager.list_bots()})

    async def handle_stop_bot(request: web.Request):
        await _require_auth(request)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)
        bot_id = payload.get("bot_id")
        if not bot_id:
            return web.json_response({"status": "error", "message": "Missing bot_id"}, status=400)
        try:
            stopped = await manager.stop_bot(int(bot_id))
            return web.json_response({"status": "success" if stopped else "not_found", "bot_id": bot_id})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    app.router.add_post("/api/bots/register", handle_register_bot)
    app.router.add_get("/api/bots/list", handle_list_bots)
    app.router.add_post("/api/bots/stop", handle_stop_bot)

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
        http_server_runner, _ = await start_aiohttp_server(bot, bot_manager)

        logger.info("启动 Telegram bot 轮询...")
        polling_task = asyncio.create_task(dp.start_polling(bot))

        # 恢復上次已註冊的代理 bots
        try:
            await start_persisted_agents(bot_manager)
        except Exception as e:
            logger.error(f"restore persisted agents failed: {e}")

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