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
            
            # 2. 尝试获取 pending updates 数量（使用非常短的超时，避免触发冲突）
            # 注意：这里不检查冲突，因为 get_updates 本身可能会触发冲突错误
            # 我们只记录信息，不将其视为冲突
            try:
                updates = await bot.get_updates(limit=1, timeout=0.1)
                # 如果有很多 pending updates，可能表示 bot 在其他地方运行
                if len(updates) > 0:
                    logger.info(f"Bot {bot_id} 有 {len(updates)} 个待处理更新")
            except Exception as e:
                # 不将 get_updates 错误视为冲突，因为这可能是正常的（bot 正在其他地方运行）
                # 我们会在删除 webhook 后处理这个问题
                logger.debug(f"Bot {bot_id} 获取更新时出错（这可能是正常的）: {e}")
            
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

            # 如果已存在，直接返回，避免不必要的操作（如删除 webhook）
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
                logger.info(f"Bot {bot_id} 已存在，返回已启动状态")
                return {"bot_id": bot_id, "status": "already_started", "brand": ctx.brand, "proxy": ctx.proxy, "bot_name": existing_bot_name, "username": existing_username}

            # 检测 bot 冲突（仅在 bot 不存在时检测）
            conflict_info = await self._detect_bot_conflicts(bot, bot_id)
            
            # 確保使用輪詢模式：若先前設有 webhook，需刪除
            # 实现"抢占"机制：删除 webhook 后等待更长时间，让其他实例的连接超时
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                logger.info(f"Bot {bot_id} webhook 已清除")
                
                # 等待一段时间，让 Telegram 服务器完全释放连接
                # 如果其他实例正在运行，它们的连接会在几秒后超时
                await asyncio.sleep(5)  # 增加到5秒，给其他实例更多时间释放连接
                
                # 再次检查 webhook 是否已清除
                try:
                    webhook_info = await bot.get_webhook_info()
                    if webhook_info.url and webhook_info.url.strip():
                        logger.warning(f"Bot {bot_id} webhook 仍然存在: {webhook_info.url}，再次尝试删除...")
                        await bot.delete_webhook(drop_pending_updates=True)
                        await asyncio.sleep(3)  # 再次等待3秒
                except Exception as check_e:
                    logger.warning(f"Bot {bot_id} 检查 webhook 状态时出错: {check_e}")
                
                # 尝试测试是否可以获取更新（检测是否还有其他实例在运行）
                try:
                    # 使用非常短的超时，避免触发冲突
                    test_updates = await bot.get_updates(limit=1, timeout=0.1, offset=-1)
                    logger.debug(f"Bot {bot_id} 测试获取更新成功")
                except Exception as test_e:
                    error_msg = str(test_e)
                    if "Conflict" in error_msg or "terminated by other getUpdates" in error_msg:
                        # 检测到冲突，说明其他实例仍在运行
                        logger.warning(f"Bot {bot_id} 检测到其他实例正在运行: {error_msg}")
                        conflict_info["has_conflict"] = True
                        conflict_info["conflict_type"] = "other_instance_running"
                        conflict_info["conflict_details"] = f"检测到其他实例正在使用此 bot token: {error_msg}"
                    else:
                        # 其他错误，可能是网络问题，不视为冲突
                        logger.debug(f"Bot {bot_id} 测试获取更新时出错（可能是正常的）: {test_e}")
            except Exception as e:
                logger.warning(f"delete_webhook failed for bot {bot_id}: {e}")
                # 如果删除 webhook 失败，记录冲突信息
                if not conflict_info["has_conflict"]:
                    conflict_info["has_conflict"] = True
                    conflict_info["conflict_type"] = "webhook_delete_failed"
                    conflict_info["conflict_details"] = f"无法删除 webhook: {str(e)}"

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

            # 如果检测到严重冲突（其他实例正在运行），不启动 bot，返回错误
            if conflict_info["has_conflict"] and conflict_info["conflict_type"] == "other_instance_running":
                # 关闭临时 session
                try:
                    await bot.session.close()
                except Exception:  # noqa: BLE001
                    pass
                # 抛出异常，让调用方知道有冲突
                raise RuntimeError(
                    f"Bot {bot_id} 无法启动：检测到其他实例正在运行。"
                    f"请确保同一 bot token 只在一个环境中运行。"
                    f"详情: {conflict_info['conflict_details']}"
                )
            
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
            
            # 如果有其他类型的冲突（非严重冲突），添加警告信息
            if conflict_info["has_conflict"] and conflict_info["conflict_type"] != "other_instance_running":
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

    async def stop_bot_by_token(self, token: str) -> dict:
        """
        通过 bot token 停止 bot
        返回: {"success": bool, "bot_id": int or None, "message": str}
        """
        try:
            # 创建临时 bot 实例来获取 bot_id
            temp_bot = Bot(token=token)
            try:
                me = await temp_bot.get_me()
                bot_id = me.id
            finally:
                try:
                    await temp_bot.session.close()
                except Exception:
                    pass
            
            # 使用 bot_id 停止 bot（stop_bot 内部已经有锁保护）
            stopped = await self.stop_bot(bot_id)
            if stopped:
                return {"success": True, "bot_id": bot_id, "message": f"Bot {bot_id} stopped successfully"}
            else:
                return {"success": False, "bot_id": bot_id, "message": f"Bot {bot_id} not found"}
        except Exception as e:
            logger.error(f"Failed to stop bot by token: {e}")
            return {"success": False, "bot_id": None, "message": f"Failed to stop bot: {str(e)}"}


