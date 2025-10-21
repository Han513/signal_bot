import asyncio
import logging
import time
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, Router
from aiogram.client.bot import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage


logger = logging.getLogger(__name__)


class BotContext:
    """保存單一 Bot 的運行上下文（Bot、Dispatcher、任務等）。"""

    def __init__(self, bot: Bot, dispatcher: Dispatcher, brand: str, proxy: Optional[str] = None):
        self.bot = bot
        self.dispatcher = dispatcher
        self.brand = brand
        self.proxy = proxy
        self.tasks = []  # type: list[asyncio.Task]
        self.bot_id: Optional[int] = None
        self.last_activity_ts: float = time.time()


class BotManager:
    """簡單的多 Bot 管理器，負責註冊、啟動與停止額外的 Bot。"""

    def __init__(self, shared_router: Router, max_bots: int = 10):
        self._shared_router = shared_router
        self._max_bots = max_bots
        self._lock = asyncio.Lock()
        self._contexts: Dict[int, BotContext] = {}

    def get_brand_by_bot_id(self, bot_id: int, default_brand: str) -> str:
        ctx = self._contexts.get(bot_id)
        return ctx.brand if ctx else default_brand

    def list_bots(self) -> list:
        return [
            {
                "bot_id": bot_id,
                "brand": ctx.brand,
                "proxy": ctx.proxy,
                "last_activity_ts": ctx.last_activity_ts,
            }
            for bot_id, ctx in self._contexts.items()
        ]

    def record_activity(self, bot_id: int) -> None:
        ctx = self._contexts.get(bot_id)
        if ctx:
            ctx.last_activity_ts = time.time()

    async def _detect_bot_conflicts(self, bot: Bot, bot_id: int) -> dict:
        """
        检测 bot 是否与其他实例冲突
        返回冲突信息字典
        """
        conflict_info = {
            "has_conflict": False,
            "conflict_type": None,
            "conflict_details": None
        }
        
        try:
            # 1. 检查 webhook 状态
            webhook_info = await bot.get_webhook_info()
            if webhook_info.url and webhook_info.url.strip():
                conflict_info["has_conflict"] = True
                conflict_info["conflict_type"] = "webhook_active"
                conflict_info["conflict_details"] = f"Bot 正在使用 webhook: {webhook_info.url}"
                logger.warning(f"Bot {bot_id} 检测到活跃的 webhook: {webhook_info.url}")
                return conflict_info
            
            # 2. 尝试获取 pending updates 数量
            try:
                updates = await bot.get_updates(limit=1, timeout=1)
                # 如果有很多 pending updates，可能表示 bot 在其他地方运行
                if len(updates) > 0:
                    logger.info(f"Bot {bot_id} 有 {len(updates)} 个待处理更新")
            except Exception as e:
                logger.warning(f"Bot {bot_id} 获取更新时出错: {e}")
                # 这可能是网络问题或 bot 被其他地方占用
                conflict_info["has_conflict"] = True
                conflict_info["conflict_type"] = "updates_error"
                conflict_info["conflict_details"] = f"无法获取更新: {str(e)}"
            
            # 3. 检查 bot 是否响应正常
            try:
                me = await bot.get_me()
                if me.id != bot_id:
                    conflict_info["has_conflict"] = True
                    conflict_info["conflict_type"] = "token_mismatch"
                    conflict_info["conflict_details"] = f"Token 不匹配: 期望 {bot_id}, 实际 {me.id}"
            except Exception as e:
                conflict_info["has_conflict"] = True
                conflict_info["conflict_type"] = "get_me_failed"
                conflict_info["conflict_details"] = f"无法获取 bot 信息: {str(e)}"
                
        except Exception as e:
            logger.error(f"Bot {bot_id} 冲突检测时出错: {e}")
            conflict_info["has_conflict"] = True
            conflict_info["conflict_type"] = "detection_error"
            conflict_info["conflict_details"] = f"冲突检测失败: {str(e)}"
        
        return conflict_info

    async def _idle_watchdog(self, bot_id: int, *, max_idle_seconds: int, check_interval: int) -> None:
        """定期檢查 Bot 是否長時間無活動，超過閾值則自動停止。"""
        try:
            while True:
                await asyncio.sleep(check_interval)
                ctx = self._contexts.get(bot_id)
                if not ctx:
                    return
                idle_for = time.time() - ctx.last_activity_ts
                if idle_for >= max_idle_seconds:
                    logger.info(f"Bot {bot_id} idle {idle_for:.0f}s >= {max_idle_seconds}s, stopping...")
                    try:
                        await self.stop_bot(bot_id)
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"Auto stop bot {bot_id} failed: {e}")
                    return
        except asyncio.CancelledError:  # 任務被取消，正常退出
            return

    async def register_and_start_bot(self, token: str, brand: str, *, proxy: Optional[str] = None,
                                     heartbeat_coro_factory=None, periodic_coro_factory=None,
                                     router_factory=None, max_idle_seconds: Optional[int] = 3*24*3600,
                                     idle_check_interval: int = 3600) -> dict:
        """
        建立並啟動一個新的 Bot：
        - 使用與主程式相同的 Router（共用 handlers）
        - 各自擁有獨立 Dispatcher 與任務
        回傳 bot_id。
        """
        async with self._lock:
            if len(self._contexts) >= self._max_bots:
                raise RuntimeError("Max bots limit reached")

            session = AiohttpSession(proxy=proxy) if proxy else None
            bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"), session=session)

            # 先呼叫 get_me 取得 bot_id 和 bot 信息
            me = await bot.get_me()
            bot_id = me.id
            bot_name = getattr(me, "first_name", None) or "Unknown"
            username = getattr(me, "username", None)

            # 检测 bot 冲突
            conflict_info = await self._detect_bot_conflicts(bot, bot_id)
            
            # 確保使用輪詢模式：若先前設有 webhook，需刪除
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                logger.info(f"Bot {bot_id} webhook 已清除")
            except Exception as e:
                logger.warning(f"delete_webhook failed for bot {bot_id}: {e}")
                # 如果删除 webhook 失败，记录冲突信息
                if not conflict_info["has_conflict"]:
                    conflict_info["has_conflict"] = True
                    conflict_info["conflict_type"] = "webhook_delete_failed"
                    conflict_info["conflict_details"] = f"无法删除 webhook: {str(e)}"

            # 如果已存在，關閉臨時 session 並回傳已啟動狀態
            if bot_id in self._contexts:
                try:
                    await bot.session.close()
                except Exception:  # noqa: BLE001
                    pass
                ctx = self._contexts[bot_id]
                # 获取已存在 bot 的信息
                try:
                    existing_me = await ctx.bot.get_me()
                    existing_bot_name = getattr(existing_me, "first_name", None) or "Unknown"
                    existing_username = getattr(existing_me, "username", None)
                except Exception:
                    existing_bot_name = "Unknown"
                    existing_username = None
                return {"bot_id": bot_id, "status": "already_started", "brand": ctx.brand, "proxy": ctx.proxy, "bot_name": existing_bot_name, "username": existing_username}

            # 為此 Bot 建立獨立 Dispatcher 與其路由
            dp = Dispatcher(storage=MemoryStorage())
            if router_factory is not None:
                dp.include_router(router_factory())
            else:
                # 預設：複用主 router 的邏輯（注意：不能直接附加同一個實例）
                r = Router()
                dp.include_router(r)

            context = BotContext(bot=bot, dispatcher=dp, brand=brand, proxy=proxy)
            context.bot_id = bot_id

            # 啟動任務（心跳、週期任務、polling）
            if heartbeat_coro_factory:
                context.tasks.append(asyncio.create_task(heartbeat_coro_factory(bot)))
            if periodic_coro_factory:
                context.tasks.append(asyncio.create_task(periodic_coro_factory(bot)))
            context.tasks.append(asyncio.create_task(dp.start_polling(bot)))

            # 可選：啟動閒置監視（None 表示不監視、永不自動停用）
            if max_idle_seconds is not None:
                context.tasks.append(asyncio.create_task(self._idle_watchdog(bot_id, max_idle_seconds=max_idle_seconds, check_interval=idle_check_interval)))

            self._contexts[bot_id] = context
            logger.info(f"Registered and started new bot: {bot_id} ({brand})")
            
            # 构建返回结果
            result = {
                "bot_id": bot_id, 
                "status": "started", 
                "brand": brand, 
                "proxy": proxy, 
                "bot_name": bot_name, 
                "username": username
            }
            
            # 如果有冲突，添加冲突信息
            if conflict_info["has_conflict"]:
                result["conflict_warning"] = {
                    "type": conflict_info["conflict_type"],
                    "details": conflict_info["conflict_details"]
                }
                logger.warning(f"Bot {bot_id} 注册成功但存在冲突: {conflict_info['conflict_details']}")
            
            return result

    async def stop_bot(self, bot_id: int) -> bool:
        async with self._lock:
            ctx = self._contexts.pop(bot_id, None)
            if not ctx:
                return False

            # 取消所有任務
            for task in ctx.tasks:
                try:
                    task.cancel()
                except Exception:  # noqa: BLE001 - 保守處理
                    pass

            if ctx.tasks:
                await asyncio.gather(*ctx.tasks, return_exceptions=True)

            try:
                await ctx.bot.session.close()
            except Exception:  # noqa: BLE001
                pass

            logger.info(f"Stopped bot: {bot_id}")
            return True


