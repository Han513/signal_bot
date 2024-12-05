# import base64
import os
import asyncio
import logging
# from io import BytesIO
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
    """處理 /verify 開頭的指令，並測試是否能獲取 topic_id"""
    command_parts = message.text.split()
    if len(command_parts) < 2:
        await message.reply("請提供驗證碼，例如: /verify 123")
        return
    
    verify_code = command_parts[1]
    topic_id = message.message_thread_id  # 獲取 topic_id
    chat_type = message.chat.type  # 獲取群組類型
    chat_id = message.chat.id  # 獲取群組 ID
    chat_title = message.chat.title  # 群組名稱

    if topic_id is None and chat_type == "supergroup":
        response_message = (
            f"驗證碼 {verify_code} 處理成功！\n"
            f"這條消息來自群組主頁，而非特定主題。\n"
            f"Chat ID: {chat_id}\n"
            f"Chat Type: {chat_type}\n"
            f"Chat Title: {chat_title}"
        )
    elif topic_id is not None:
        response_message = (
            f"驗證碼 {verify_code} 處理成功！\n"
            f"Topic ID: {topic_id}\n"
            f"Chat ID: {chat_id}\n"
            f"Chat Type: {chat_type}\n"
            f"Chat Title: {chat_title}"
        )
    else:
        response_message = f"驗證碼 {verify_code} 處理成功！\n無法確定消息的來源。"

    try:
        await message.reply(response_message)
        logger.info(
            f"用戶: {message.from_user.username} ID: {message.from_user.id} "
            f"在 Chat ID {chat_id}, Chat Type {chat_type}, Topic ID {topic_id} 中使用驗證碼 {verify_code}"
        )
    except Exception as e:
        logger.error(f"驗證處理失敗: {e}")
        await message.reply("驗證處理遇到問題，請稍後再試。")

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

async def main():
    """啟動 Bot"""
    await load_active_groups()
    
    dp.include_router(router)

    TEST_API_URL = "https://jsonplaceholder.typicode.com/posts"  # 測試用 API URL

    # 啟動 API 檢查任務
    asyncio.create_task(periodic_api_check(TEST_API_URL, bot, -1002292197960, interval=30))
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot 已停止")
    except Exception as e:
        logger.error(f"Bot 運行時發生錯誤: {e}")