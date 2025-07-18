# API 參考文檔

## 接口列表

### 1. Copy Signal 推送
- **端點**: `POST /api/send_copy_signal`
- **描述**: 推送交易員的開倉/平倉信號
- **Content-Type**: `application/json`

#### 請求參數
```json
{
  "trader_uid": "string",
  "trader_name": "string", 
  "trader_pnl": "number",
  "trader_pnlpercentage": "number",
  "trader_detail_url": "string",
  "pair": "string",
  "base_coin": "string",
  "quote_coin": "string", 
  "pair_leverage": "number",
  "pair_type": "buy|sell",
  "price": "number",
  "amount": "number",
  "time": "number (毫秒時間戳)",
  "trader_url": "string",
  "pair_side": "1|2",
  "pair_margin_type": "1|2"
}
```

#### 響應格式
```json
{
  "status": "200",
  "results": "接收成功，稍後發送"
}
```

### 2. 交易總結推送
- **端點**: `POST /api/signal/completed_trade`
- **描述**: 推送已完成交易的總結信息
- **Content-Type**: `application/json`

#### 請求參數
```json
{
  "trader_uid": "string",
  "trader_name": "string",
  "trader_detail_url": "string", 
  "pair": "string",
  "pair_side": "1|2",
  "pair_margin_type": "1|2",
  "pair_leverage": "number",
  "entry_price": "number",
  "exit_price": "number",
  "realized_pnl": "number",
  "realized_pnl_percentage": "number",
  "duration_days": "number",
  "duration_hours": "number",
  "close_time": "number (毫秒時間戳)"
}
```

#### 響應格式
```json
{
  "status": "200", 
  "results": "接收成功，稍後發送"
}
```

### 3. 止盈止損更新推送
- **端點**: `POST /api/signal/scalp_update`
- **描述**: 推送止盈止損設置或更新信息
- **Content-Type**: `application/json`

#### 請求參數
```json
{
  "trader_uid": "string",
  "trader_name": "string",
  "trader_detail_url": "string",
  "pair": "string", 
  "pair_side": "1|2",
  "order_time": "number (毫秒時間戳)",
  "tp_price": "number (可選)",
  "sl_price": "number (可選)",
  "previous_tp_price": "number (可選，更新時使用)",
  "previous_sl_price": "number (可選，更新時使用)"
}
```

#### 參數說明
- `tp_price`: 止盈價格，設置時必填
- `sl_price`: 止損價格，設置時必填  
- `previous_tp_price`: 之前的止盈價格，更新時使用
- `previous_sl_price`: 之前的止損價格，更新時使用
- 至少需要提供 `tp_price` 或 `sl_price` 其中之一

#### 響應格式
```json
{
  "status": "200",
  "results": "接收成功，稍後發送"
}
```

### 4. 持倉報告推送
- **端點**: `POST /api/report/holdings`
- **描述**: 推送持倉報告信息（每2小時更新）
- **Content-Type**: `application/json`

#### 請求參數
```json
{
  "trader_uid": "string",
  "trader_name": "string",
  "trader_detail_url": "string",
  "pair": "string",
  "pair_side": "1|2", 
  "pair_margin_type": "1|2",
  "pair_leverage": "number",
  "entry_price": "number",
  "current_price": "number",
  "unrealized_pnl_percentage": "number",
  "tp_price": "number (可選)",
  "sl_price": "number (可選)"
}
```

#### 參數說明
- `tp_price`: 止盈價格（可選）
- `sl_price`: 止損價格（可選）
- 如果有設置止盈止損，會在文案中顯示

#### 響應格式
```json
{
  "status": "200",
  "results": "接收成功，稍後發送"
}
```

### 5. 週報推送
- **端點**: `POST /api/report/weekly`
- **描述**: 推送週度績效報告信息
- **Content-Type**: `application/json`

#### 請求參數
```json
{
  "trader_uid": "string",
  "trader_name": "string",
  "trader_url": "string",
  "trader_detail_url": "string",
  "start_date": "string (YYYY-MM-DD)",
  "end_date": "string (YYYY-MM-DD)",
  "total_roi": "number",
  "total_pnl": "number",
  "total_trades": "number",
  "win_trades": "number",
  "loss_trades": "number",
  "win_rate": "number"
}
```

#### 參數說明
- `trader_url`: 交易員頭像 URL，用於生成圖片
- `total_roi`: 總回報率百分比
- `total_pnl`: 總盈虧金額
- `win_trades`: 盈利交易筆數
- `loss_trades`: 虧損交易筆數（可選，會自動計算）
- `win_rate`: 勝率百分比

#### 響應格式
```json
{
  "status": "200",
  "results": "接收成功，稍後發送"
}
```

## 通用說明

### 錯誤響應格式
```json
{
  "status": "400",
  "message": "錯誤描述"
}
```

### 參數說明
- `pair_side`: 1=Long, 2=Short
- `pair_margin_type`: 1=Cross, 2=Isolated
- 所有時間戳均為毫秒級 (13位數字)
- 所有價格和數值均為數字格式

### 推送邏輯
- 所有接口均為異步處理，立即返回接收確認
- 實際推送在背景進行，不阻塞 API 響應
- 根據 `trader_uid` 查詢對應的推送頻道
- 支援 Telegram 和 Discord 雙平台推送

## 文案格式示例

### Copy Signal 文案
```
⚡️{Agentname} New Trade Open
📢{Pair}USDT {Positionmode}{Leverage}
⏰Time: 
➡️Direction: 
🎯Entry Price: 
About {Agentname}, more actions>> (link)
```

### 交易總結文案
```
⚡️{Agentname} Close Position
📢{Pair}USDT {Positionmode}{Leverage}
⏰Time: 
➡️Direction: 
🙌🏻ROI
🎯Entry Price: 
💰Exit Price:
About {Agentname}, more actions>> (link)
```

### 止盈止損設置文案
```
⚡️{Agentname} TP/SL Setting
📢{Pair}USDT {Direction}
⏰Time: 
✅TP Price: 
🛑SL Price: 
About {Agentname}, more actions>> (link)
```

### 止盈止損更新文案
```
⚡️{Agentname} TP/SL Update 
📢{Pair}USDT {Direction}
⏰Time: 
✅TP Price: {Price} change to {Price}
🛑SL Price: {Price} change to {Price}
About {Agentname}, more actions>> (link)
```

### 持倉報告文案（無止盈止損）
```
⚡️{Agentname} Trading Summary (Updated every 2 hours)
📢{Pair}USDT {Positionmode}{Leverage}
➡️Direction: 
🎯Entry Price: 
📊Current Price:
🚀ROI 
About {Agentname}, more actions>> (link)
```

### 持倉報告文案（有止盈止損）
```
⚡️{Agentname} Trading Summary (Updated every 2 hours)
📢{Pair}USDT {Positionmode}{Leverage}
➡️Direction: 
🎯Entry Price: 
📊Current Price:
🚀ROI 
✅TP Price: 
🛑SL Price: 
About {Agentname}, more actions>> (link)
```

### 週報文案
```
⚡️{Agentname} Weekly Performance Report
🔥 TOTAL R: 
📈 Total Trades:
✅ Wins:
❌ Losses: 
🏆 Win Rate:
About {Agentname}, more actions>> (link)
``` 