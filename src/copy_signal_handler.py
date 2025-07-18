import os
import aiohttp
import asyncio
from aiogram import Bot
from aiohttp import web
from dotenv import load_dotenv
from aiogram.types import FSInputFile
import aiofiles
import tempfile
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
import logging
from datetime import datetime, timezone

load_dotenv()
SOCIAL_API = os.getenv("SOCIAL_API")

# Discord Webhook API
DISCORD_BOT = os.getenv("DISCORD_BOT")
DISCORD_BOT_COPY = os.getenv("DISCORD_BOT_COPY")

async def handle_send_copy_signal(request: web.Request, *, bot: Bot):
    """
    處理 /api/send_copy_signal 介面：
    1. 先同步驗證輸入資料，失敗直接回傳 400。
    2. 成功則立即回 200，並將實際推送工作交由背景協程處理。
    """
    # Content-Type 檢查
    if request.content_type != "application/json":
        return web.json_response({"status": "400", "message": "Content-Type must be application/json"}, status=400)

    # 解析 JSON
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "400", "message": "Invalid JSON body"}, status=400)

    # 資料驗證
    try:
        validate_copy_signal(data)
    except ValueError as err:
        return web.json_response({"status": "400", "message": str(err)}, status=400)

    # 背景處理，不阻塞 HTTP 回應
    asyncio.create_task(process_copy_signal(data, bot))

    return web.json_response({"status": "200", "results": "接收成功，稍後發送"}, status=200)

# ------------------------- 協助函式 -------------------------

def validate_copy_signal(data: dict) -> None:
    """驗證 copy signal 請求資料，失敗時拋出 ValueError。"""
    required_fields = {
        "trader_uid", "trader_name", "trader_pnl", "trader_pnlpercentage",
        "trader_detail_url", "pair", "base_coin", "quote_coin",
        "pair_leverage", "pair_type", "price", "amount", "time", "trader_url",
        "pair_side", "pair_margin_type"
    }

    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        raise ValueError(f"缺少欄位: {', '.join(missing)}")

    # 數值與類型檢查
    try:
        pnl = float(data["trader_pnl"])
        pnl_perc = float(data["trader_pnlpercentage"])
        float(data["pair_leverage"])
    except (TypeError, ValueError):
        raise ValueError("trader_pnlpercentage / pair_leverage / trader_pnl 必須為數字格式")

    # 正負號須一致
    if (pnl >= 0) ^ (pnl_perc >= 0):
        raise ValueError("trader_pnl 與 trader_pnlpercentage 正負號不一致")

    if data["pair_type"] not in {"buy", "sell"}:
        raise ValueError("pair_type 只能是 'buy' 或 'sell'")

    # pair_side 必須為 1 或 2（字串或數字）
    if str(data["pair_side"]) not in {"1", "2"}:
        raise ValueError("pair_side 只能是 '1'(Long) 或 '2'(Short)")

    # pair_margin_type 必須為 1 或 2（字串或數字）
    if str(data["pair_margin_type"]) not in {"1", "2"}:
        raise ValueError("pair_margin_type 只能是 '1'(Cross) 或 '2'(Isolated)")

    # time 欄位必須為毫秒級時間戳（13 位數/大於等於 1e12）
    try:
        ts_val = int(float(data["time"]))
    except (TypeError, ValueError):
        raise ValueError("time 必須為毫秒級時間戳 (數字格式)")

    # 檢查是否可能為秒級時間戳（10 位數），若是則判定錯誤
    if ts_val < 10**12:
        raise ValueError("time 必須為毫秒級時間戳 (13 位)")

async def process_copy_signal(data: dict, bot: Bot) -> None:
    """背景協程：查詢推送目標、產圖並發送訊息。"""
    try:
        # 取得所有 socials 設定
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        payload = {"brand": "BYD", "type": "TELEGRAM"}
        async with aiohttp.ClientSession() as session:
            async with session.post(SOCIAL_API, headers=headers, data=payload) as resp:
                social_data = await resp.json()

        trader_uid = str(data["trader_uid"])

        # 選出符合條件的推送目標 (chat_id, topic_id, jump)
        push_targets = []
        for social in social_data.get("data", []):
            chat_id = social.get("socialGroup")
            for chat in social.get("chats", []):
                if (
                    chat.get("type") == "copy"
                    and chat.get("enable")
                    and str(chat.get("traderUid")) == trader_uid
                ):
                    topic_id = chat.get("chatId")
                    jump = str(chat.get("jump", "1"))
                    if chat_id and topic_id:
                        push_targets.append((chat_id, int(topic_id), jump))

        if not push_targets:
            logging.warning(f"未找到符合條件的推送頻道: {trader_uid}")
            return

        # 產生交易員統計圖片
        img_path = generate_trader_summary_image(
            data["trader_url"],
            data["trader_name"],
            data["trader_pnlpercentage"],
            data["trader_pnl"],
        )
        if not img_path:
            logging.warning("圖片生成失敗，取消推送")
            return

        photo = FSInputFile(img_path)

        # 將毫秒級時間戳轉為 UTC+0 可讀格式
        formatted_time = format_timestamp_ms_to_utc(data.get('time'))

        tasks = []
        # 文案映射
        pair_type_map = {"buy": "Open", "sell": "Close"}
        pair_side_map = {"1": "Long", "2": "Short", 1: "Long", 2: "Short"}
        margin_type_map = {"1": "Cross", "2": "Isolated", 1: "Cross", 2: "Isolated"}

        for chat_id, topic_id, jump in push_targets:
            # 取得映射值
            pair_type_str = pair_type_map.get(str(data.get("pair_type", "")).lower(), str(data.get("pair_type", "")))
            pair_side_str = pair_side_map.get(str(data.get("pair_side", "")), str(data.get("pair_side", "")))
            margin_type_str = margin_type_map.get(str(data.get("pair_margin_type", "")), str(data.get("pair_margin_type", "")))

            if jump == "1":
                caption = (
                    f"#CopySignals\n\n"
                    f"⚡️[{data['trader_name']}] Trading Alert\n\n"
                    f"{data['pair']} {margin_type_str} {data['pair_leverage']}X\n\n"
                    f"Time: {formatted_time} (UTC+0)\n"
                    f"Direction: {pair_type_str} {pair_side_str}\n"
                    f"Avg. Price: {data['price']}\n\n"
                    f"[About {data['trader_name']}, more actions>>]({data['trader_detail_url']})"
                )
            else:
                caption = (
                    f"#CopySignals\n\n"
                    f"⚡️[{data['trader_name']}] Trading Alert\n\n"
                    f"{data['pair']} {margin_type_str} {data['pair_leverage']}X\n\n"
                    f"Time: {formatted_time} (UTC+0)\n"
                    f"Direction: {pair_type_str} {pair_side_str}\n"
                    f"Avg. Price: {data['price']}\n"
                )
            tasks.append(
                bot.send_photo(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    photo=photo,
                    caption=caption,
                    parse_mode="Markdown",
                )
            )

        # 等待 Telegram 發送結果
        await asyncio.gather(*tasks, return_exceptions=True)

        # ----------------  同步發送至 Discord Bot ----------------
        if DISCORD_BOT_COPY:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(DISCORD_BOT_COPY, json=data) as dc_resp:
                        dc_resp_json = await dc_resp.json()
                        logging.info(f"[COPY] Discord 發送結果: {dc_resp.status} - {dc_resp_json}")
            except Exception as e:
                logging.error(f"[COPY] 呼叫 Discord 發送 copy signal 時出錯: {e}")

    except Exception as e:
        logging.error(f"推送 copy signal 失敗: {e}")

def format_float(value):
    """
    將數字格式化為最多兩位小數，去除多餘的0（如1050.00顯示為1050，12.50顯示為12.5），四捨五入。
    """
    try:
        f = round(float(value), 2)
        if f == int(f):
            return str(int(f))
        elif (f * 10) == int(f * 10):
            return f"{f:.1f}"
        else:
            return f"{f:.2f}"
    except Exception:
        return str(value)

def generate_trader_summary_image(trader_url, trader_name, pnl_percentage, pnl):
    # 字體設定與尺寸
    number_font_size = 100
    label_font_size = 45
    title_font_size = 70
    avatar_size = 180

    # 背景圖
    bg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'pics', 'copy_trade.png'))
    if os.path.exists(bg_path):
        img = Image.open(bg_path).convert('RGB')
    else:
        img = Image.new('RGB', (1200, 675), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 頭像處理
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(trader_url, timeout=5, headers=headers)
        response.raise_for_status()
        avatar = Image.open(BytesIO(response.content)).resize((avatar_size, avatar_size)).convert("RGBA")
    except Exception as e:
        logging.warning(f"頭像下載失敗: {e}, 使用預設頭像")
        avatar = Image.new('RGBA', (avatar_size, avatar_size), (200, 200, 200, 255))

    mask = Image.new('L', (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)
    avatar.putalpha(mask)
    avatar_x, avatar_y = 100, 150
    img.paste(avatar, (avatar_x, avatar_y), avatar)

    # 字體載入
    font_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'text'))
    bold_font_path = os.path.join(font_dir, 'BRHendrix-Bold-BF6556d1b5459d3.otf')
    medium_font_path = os.path.join(font_dir, 'BRHendrix-Medium-BF6556d1b4e12b2.otf')
    noto_bold_font_path = os.path.join(font_dir, 'NotoSansSC-Bold.ttf')

    def is_all_english(s):
        try:
            s.encode('ascii')
            return True
        except UnicodeEncodeError:
            return False

    try:
        number_font = ImageFont.truetype(bold_font_path, number_font_size)
        label_font = ImageFont.truetype(noto_bold_font_path, label_font_size)
        title_font = ImageFont.truetype(
            bold_font_path if is_all_english(trader_name) else noto_bold_font_path,
            title_font_size
        )
    except Exception as e:
        logging.warning(f"字體載入失敗: {e}")
        return None

    # 名稱
    name_x = avatar_x + avatar_size + 30
    if is_all_english(trader_name):
        # 英文名往下微調
        name_y = avatar_y + (avatar_size - title_font_size) // 2 + 13
    else:
        # 中文名維持原本
        name_y = avatar_y + (avatar_size - title_font_size) // 2
    draw.text((name_x, name_y), trader_name, font=title_font, fill=(255, 255, 255))

    # 數值處理
    try:
        pnl_perc_value = float(pnl_percentage) * 100  # 乘上 100 轉為百分比
    except Exception:
        pnl_perc_value = 0.0
    is_positive = pnl_perc_value >= 0
    color = (0, 191, 99) if is_positive else (237, 29, 36)

    roi_text = f"{format_float(pnl_perc_value)}%"
    try:
        pnl_val = float(pnl)
    except Exception:
        pnl_val = 0.0
    pnl_text = f"${format_float(abs(pnl_val))}"
    if not is_positive:
        pnl_text = f"-{pnl_text}"

    # ROI & PNL位置（水平對齊）
    roi_x, roi_y = 100, 415
    pnl_x, pnl_y = 550, 415

    draw.text((roi_x, roi_y), roi_text, font=number_font, fill=color)
    draw.text((pnl_x, pnl_y), pnl_text, font=number_font, fill=color)

    draw.text((roi_x, roi_y + number_font_size + 5), "7D ROI", font=label_font, fill=(200, 200, 200))
    draw.text((pnl_x, pnl_y + number_font_size + 5), "7D PNL", font=label_font, fill=(200, 200, 200))

    # 輸出圖片
    temp_path = "/tmp/trader_summary_full.png"
    img.save(temp_path, quality=95)
    return temp_path

# 新增: 毫秒時間戳轉 UTC+0 字串
def format_timestamp_ms_to_utc(ms_value):
    """將毫秒級時間戳轉為 UTC+0 的時間字串 (YYYY-MM-DD HH:MM:SS)。若轉換失敗則回傳原值字串。"""
    try:
        ts_int = int(float(ms_value))  # 允許字串或浮點數輸入
        dt = datetime.fromtimestamp(ts_int / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ms_value) 