import aiohttp
import json
import logging
import asyncio

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def update_post_status():
    payload = {"id": 2, "status": 1}  # 更新文章状态为已发布
    headers = {"Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post("http://127.0.0.1:5003/posts/edit", headers=headers, data=json.dumps(payload)) as response:
                print(await response.json())
                if response.status == 200:
                    logger.info(f"成功更新文章状态: 2")
                else:
                    logger.error(f"更新文章状态失败，状态码: {response.status}")
        except Exception as e:
            logger.error(f"调用 /posts/update 接口失败: {e}")

# @router.message(Command("send_base64_image"))
# async def send_base64_image(message: types.Message):
#     """處理帶前綴的 Base64 圖片並發送到 Telegram"""
#     chat_id = message.chat.id

#     # Google 提供的 Base64 數據（包含前綴）
#     base64_data = (
#             "data:image/jpeg;base64,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")
#     try:
#         # 去除前綴 'data:image/jpeg;base64,'
#         base64_data_clean = base64_data.split(",")[1]

#         # 解碼 Base64 數據為二進制
#         image_binary = base64.b64decode(base64_data_clean)

#         # 保存為臨時文件
#         with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
#             temp_file.write(image_binary)
#             temp_file_path = temp_file.name

#         # 使用 FSInputFile 發送圖片
#         photo = FSInputFile(temp_file_path)
#         await bot.send_photo(
#             chat_id=chat_id,
#             photo=photo,
#             caption="這是從 Base64 數據發送的圖片"
#         )
#         logger.info(f"成功發送 Base64 圖片到 Chat ID: {chat_id}")

#     except Exception as e:
#         logger.error(f"發送 Base64 圖片失敗: {e}")
#         await message.reply("發送 Base64 圖片時出現問題，請稍後再試。")

#     finally:
#         # 刪除臨時文件
#         if 'temp_file_path' in locals() and temp_file_path and os.path.exists(temp_file_path):
#             os.unlink(temp_file_path)

# 运行测试
if __name__ == "__main__":
    asyncio.run(update_post_status())
