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

# 运行测试
if __name__ == "__main__":
    asyncio.run(update_post_status())
