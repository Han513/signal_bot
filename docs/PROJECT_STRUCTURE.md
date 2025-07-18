# 項目結構說明

## 目錄結構

```
signal_bot_TG/
├── docs/                          # 文檔目錄
│   ├── API_REFERENCE.md          # API 接口參考文檔
│   ├── PROJECT_STRUCTURE.md      # 本文件
│   └── multilingual_guide.md     # 多語言指南
├── logs/                         # 日誌文件目錄
├── pics/                         # 圖片資源目錄
│   └── copy_trade.png           # 交易員統計圖片背景
├── run/                          # 運行配置目錄
│   └── signalbot.conf           # Bot 配置文件
├── src/                          # 源代碼目錄
│   ├── handlers/                 # 處理器模塊目錄
│   │   ├── __init__.py          # 模塊初始化文件
│   │   ├── common.py            # 共享邏輯和工具函數
│   │   ├── copy_signal_handler.py      # 開/平倉信號處理器
│   │   ├── trade_summary_handler.py    # 交易總結處理器
│   │   ├── scalp_update_handler.py     # 止盈止損更新處理器
│   │   ├── holding_report_handler.py   # 持倉報告處理器
│   │   └── weekly_report_handler.py    # 週報處理器
│   ├── main.py                  # 主程序入口
│   ├── db_handler_aio.py        # 數據庫異步處理器
│   ├── api_handler.py           # API 處理器
│   ├── unpublished_posts_handler.py    # 未發布文章處理器
│   └── multilingual_utils.py    # 多語言工具
├── text/                         # 字體文件目錄
│   ├── BRHendrix-Bold-BF6556d1b5459d3.otf
│   ├── BRHendrix-Medium-BF6556d1b4e12b2.otf
│   └── NotoSansSC-Bold.ttf
├── tests/                        # 測試目錄
│   └── test.py                  # 測試文件
├── venv/                         # Python 虛擬環境
├── .env                         # 環境變量配置
├── README.md                    # 項目說明
└── requirements.txt             # Python 依賴包
```

## 模塊說明

### 核心模塊

#### `src/main.py`
- **功能**: 主程序入口，負責啟動 Telegram Bot 和 HTTP API 服務器
- **主要組件**:
  - Telegram Bot 初始化和配置
  - HTTP API 路由註冊
  - 群組管理功能
  - 用戶驗證功能
  - 公告發送功能

#### `src/handlers/common.py`
- **功能**: 共享邏輯和工具函數
- **主要功能**:
  - 獲取推送目標 (`get_push_targets`)
  - 發送 Telegram 消息 (`send_telegram_message`)
  - 發送 Discord 消息 (`send_discord_message`)
  - 圖片生成 (`generate_trader_summary_image`)
  - 數據格式化 (`format_float`, `format_timestamp_ms_to_utc`)
  - 異步任務處理 (`create_async_response`)

### 信號處理器模塊

#### `src/handlers/copy_signal_handler.py`
- **功能**: 處理開/平倉信號推送
- **API 端點**: `POST /api/send_copy_signal`
- **特點**: 生成帶有交易員頭像的統計圖片

#### `src/handlers/trade_summary_handler.py`
- **功能**: 處理已完成交易的總結推送
- **API 端點**: `POST /api/signal/completed_trade`
- **特點**: 顯示交易盈虧、持續時間等信息

#### `src/handlers/scalp_update_handler.py`
- **功能**: 處理止盈止損設置和更新通知
- **API 端點**: `POST /api/signal/scalp_update`
- **特點**: 支持新設置和更新兩種模式

#### `src/handlers/holding_report_handler.py`
- **功能**: 處理持倉報告推送
- **API 端點**: `POST /api/report/holdings`
- **特點**: 支持多個持倉的批量報告

#### `src/handlers/weekly_report_handler.py`
- **功能**: 處理週報推送
- **API 端點**: `POST /api/report/weekly`
- **特點**: 顯示週度績效統計

### 輔助模塊

#### `src/db_handler_aio.py`
- **功能**: 數據庫異步操作
- **主要功能**:
  - 群組管理 (增刪改查)
  - 用戶驗證狀態管理
  - 異步數據庫連接池

#### `src/unpublished_posts_handler.py`
- **功能**: 處理未發布文章的定時檢查和發布
- **主要功能**:
  - 定期檢查未發布文章
  - 自動發布到 Telegram 群組

#### `src/api_handler.py`
- **功能**: 通用 API 處理邏輯
- **主要功能**:
  - 獲取群組成員數量
  - 通用錯誤處理

#### `src/multilingual_utils.py`
- **功能**: 多語言支持工具
- **主要功能**:
  - 文本翻譯
  - 語言檢測
  - 多語言格式化

## 設計模式

### 模塊化設計
- 每個信號類型都有獨立的處理器模塊
- 共享邏輯抽離到 `common.py`
- 高內聚，低耦合的設計原則

### 異步處理
- 所有 API 接口都支持異步處理
- 立即響應 HTTP 請求，背景處理推送任務
- 使用 `asyncio` 進行並發處理

### 錯誤處理
- 統一的錯誤響應格式
- 詳細的參數驗證
- 完善的日誌記錄

### 配置管理
- 使用環境變量進行配置
- 支持多環境部署
- 靈活的 Discord webhook 配置

## API 路由結構

```
/api/
├── get_member_count          # 獲取群組成員數量
├── send_announcement         # 發送公告
├── send_copy_signal          # 開/平倉信號
├── signal/
│   ├── completed_trade       # 交易總結
│   └── scalp_update          # 止盈止損更新
└── report/
    ├── holdings              # 持倉報告
    └── weekly                # 週報
```

## 擴展指南

### 添加新的信號類型

1. 在 `src/handlers/` 目錄下創建新的處理器文件
2. 實現以下函數:
   - `handle_xxx()`: API 處理函數
   - `validate_xxx()`: 數據驗證函數
   - `process_xxx()`: 背景處理函數
   - `format_xxx_text()`: 文本格式化函數 (可選)
   - `generate_xxx_image()`: 圖片生成函數 (可選)

3. 在 `src/main.py` 中:
   - 導入新的處理器
   - 添加 API 路由

4. 更新文檔

### 添加新的共享功能

1. 在 `src/handlers/common.py` 中添加新的工具函數
2. 確保函數有適當的錯誤處理和日誌記錄
3. 更新相關的處理器模塊

### 配置新的 Discord webhook

1. 在 `.env` 文件中添加新的環境變量
2. 在對應的處理器中添加 Discord 發送邏輯
3. 測試 Discord 集成功能

## 部署說明

### 環境要求
- Python 3.8+
- PostgreSQL 數據庫
- 足夠的磁盤空間用於圖片生成

### 環境變量
參考 `.env` 文件中的配置項，主要包括:
- Telegram Bot Token
- 數據庫連接信息
- API 端點配置
- Discord webhook URLs

### 啟動命令
```bash
cd src
python main.py
```

### 監控和日誌
- 日誌文件保存在 `logs/` 目錄
- 使用 `logging` 模塊進行日誌記錄
- 支持不同級別的日誌輸出 