# Telegram Bot API 參考文檔

## 概述

本 Telegram Bot 提供多種信號和報告推送功能，所有接口都支持異步處理，立即響應 HTTP 請求並在背景處理推送任務。

## 基礎信息

- **Base URL**: `http://your-server:5010`
- **Content-Type**: `application/json`
- **響應格式**: JSON

## API 接口列表

### 1. 開/平倉信號推送

**接口**: `POST /api/send_copy_signal`

**描述**: 發送交易員的開倉或平倉信號，包含交易員頭像和統計信息。

**請求參數**:
```json
{
  "trader_uid": "12345",
  "trader_name": "Trader Name",
  "trader_pnl": "1200.50",
  "trader_pnlpercentage": "0.125",
  "trader_detail_url": "https://example.com/trader/12345",
  "trader_url": "https://example.com/avatar.jpg",
  "pair": "BTCUSDT",
  "base_coin": "BTC",
  "quote_coin": "USDT",
  "pair_leverage": "20",
  "pair_type": "buy",
  "pair_side": "1",
  "pair_margin_type": "1",
  "price": "50000.00",
  "amount": "0.1",
  "time": "1640995200000"
}
```

**響應**:
```json
{
  "status": "200",
  "message": "接收成功，稍後發送"
}
```

### 2. 已完成交易總結

**接口**: `POST /api/signal/completed_trade`

**描述**: 發送已完成交易的總結報告，包含盈虧、持續時間等信息。

**請求參數**:
```json
{
  "trader_uid": "12345",
  "trader_name": "Trader Name",
  "trader_detail_url": "https://example.com/trader/12345",
  "pair": "BTCUSDT",
  "pair_side": "1",
  "pair_margin_type": "1",
  "pair_leverage": "20",
  "entry_price": "50000.00",
  "exit_price": "52000.00",
  "realized_pnl": "400.00",
  "realized_pnl_percentage": "0.04",
  "duration_days": "2",
  "duration_hours": "12",
  "close_time": "1640995200000"
}
```

### 3. 止盈止損更新

**接口**: `POST /api/signal/scalp_update`

**描述**: 發送止盈止損設置或更新通知。

**請求參數**:
```json
{
  "trader_uid": "12345",
  "trader_name": "Trader Name",
  "trader_detail_url": "https://example.com/trader/12345",
  "pair": "BTCUSDT",
  "pair_side": "1",
  "order_type": "take_profit",
  "order_time": "1640995200000",
  "price": "52000.00",
  "previous_price": "51000.00"
}
```

**注意**: `previous_price` 為可選字段，如果提供則表示更新操作，否則為新設置操作。

### 4. 持倉報告

**接口**: `POST /api/report/holdings`

**描述**: 發送交易員當前持倉的詳細報告。

**請求參數**:
```json
{
  "trader_uid": "12345",
  "trader_name": "Trader Name",
  "trader_detail_url": "https://example.com/trader/12345",
  "positions": [
    {
      "pair": "BTCUSDT",
      "pair_side": "1",
      "pair_margin_type": "1",
      "pair_leverage": "20",
      "avg_price": "50000.00",
      "current_price": "52000.00",
      "yield_rate": "0.04",
      "quantity": "0.1"
    }
  ]
}
```

### 5. 週報

**接口**: `POST /api/report/weekly`

**描述**: 發送交易員每週績效報告。

**請求參數**:
```json
{
  "trader_uid": "12345",
  "trader_name": "Trader Name",
  "trader_detail_url": "https://example.com/trader/12345",
  "start_date": "2024-01-01",
  "end_date": "2024-01-07",
  "total_roi": "0.164",
  "total_pnl": "1640.00",
  "total_trades": "9",
  "win_trades": "6",
  "loss_trades": "3",
  "win_rate": "66.67"
}
```

## 數據格式說明

### 通用字段

- `trader_uid`: 交易員唯一標識符
- `trader_name`: 交易員名稱
- `trader_detail_url`: 交易員詳情頁面鏈接
- `trader_url`: 交易員頭像圖片 URL

### 交易相關字段

- `pair`: 交易對名稱 (如 "BTCUSDT")
- `pair_side`: 交易方向 ("1" = 多單, "2" = 空單)
- `pair_margin_type`: 保證金類型 ("1" = 全倉, "2" = 逐倉)
- `pair_leverage`: 槓桿倍數
- `pair_type`: 操作類型 ("buy" = 開倉, "sell" = 平倉)

### 時間字段

所有時間字段都使用毫秒級時間戳 (13 位數字)，例如: `1640995200000`

### 數值字段

- 價格: 浮點數，最多支持 8 位小數
- 百分比: 小數形式 (如 0.04 表示 4%)
- 數量: 浮點數

## 錯誤響應

當請求參數有誤時，會返回 400 錯誤：

```json
{
  "status": "400",
  "message": "錯誤描述"
}
```

常見錯誤：
- `Content-Type must be application/json`: 請求頭 Content-Type 不正確
- `Invalid JSON body`: JSON 格式錯誤
- `缺少欄位: field1, field2`: 缺少必需字段
- `數值欄位必須為數字格式`: 數值字段格式錯誤

## 推送目標配置

所有接口都會根據 `trader_uid` 和信號類型從 `/socials` API 獲取推送目標：

- `copy`: 開/平倉信號
- `summary`: 交易總結
- `scalp`: 止盈止損更新
- `holding`: 持倉報告
- `report`: 週報

## Discord 集成

所有接口都支持同步發送到 Discord，需要在環境變量中配置對應的 Discord webhook URL：

- `DISCORD_BOT_COPY`: 開/平倉信號
- `DISCORD_BOT_SUMMARY`: 交易總結
- `DISCORD_BOT_SCALP`: 止盈止損更新
- `DISCORD_BOT_HOLDING`: 持倉報告
- `DISCORD_BOT_WEEKLY`: 週報

## 示例代碼

### Python 示例

```python
import requests
import json

# 發送開倉信號
data = {
    "trader_uid": "12345",
    "trader_name": "Test Trader",
    "trader_pnl": "1200.50",
    "trader_pnlpercentage": "0.125",
    "trader_detail_url": "https://example.com/trader/12345",
    "trader_url": "https://example.com/avatar.jpg",
    "pair": "BTCUSDT",
    "base_coin": "BTC",
    "quote_coin": "USDT",
    "pair_leverage": "20",
    "pair_type": "buy",
    "pair_side": "1",
    "pair_margin_type": "1",
    "price": "50000.00",
    "amount": "0.1",
    "time": "1640995200000"
}

response = requests.post(
    "http://your-server:5010/api/send_copy_signal",
    headers={"Content-Type": "application/json"},
    data=json.dumps(data)
)

print(response.json())
```

### cURL 示例

```bash
curl -X POST http://your-server:5010/api/send_copy_signal \
  -H "Content-Type: application/json" \
  -d '{
    "trader_uid": "12345",
    "trader_name": "Test Trader",
    "trader_pnl": "1200.50",
    "trader_pnlpercentage": "0.125",
    "trader_detail_url": "https://example.com/trader/12345",
    "trader_url": "https://example.com/avatar.jpg",
    "pair": "BTCUSDT",
    "base_coin": "BTC",
    "quote_coin": "USDT",
    "pair_leverage": "20",
    "pair_type": "buy",
    "pair_side": "1",
    "pair_margin_type": "1",
    "price": "50000.00",
    "amount": "0.1",
    "time": "1640995200000"
  }'
``` 