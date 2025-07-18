# 多语言文章发布功能使用指南

## 概述

这个多语言功能允许你为不同的Telegram群组设置不同的默认语言，并在发布文章时提供语言切换按钮，让用户可以实时切换文章的语言版本。

## 功能特性

- ✅ 支持多种语言（中文、英文、日文、韩文）
- ✅ 为每个群组设置默认语言
- ✅ 内联键盘按钮进行语言切换
- ✅ 实时编辑消息内容（无需发送新消息）
- ✅ 支持图片和文本消息
- ✅ 智能回退机制
- ✅ 可扩展的语言配置

## 文件结构

```
src/
├── multilingual_handler.py      # 核心多语言处理类
├── multilingual_config.py       # 多语言配置文件
├── callback_handler.py          # 回调处理器
├── multilingual_example.py      # 使用示例
└── unpublished_posts_handler.py # 修改后的文章发布函数
```

## 快速开始

### 1. 基本使用

```python
from src.multilingual_handler import multilingual_handler
from aiogram import Bot

# 发送多语言文章
await multilingual_handler.send_multilingual_post(
    bot=bot,
    chat_id=-1001234567890,
    topic_id=123,
    post_data={
        "id": "post_001",
        "content": "中文内容",
        "content_en": "English content",
        "content_ja": "日本語の内容",
        "content_ko": "한국어 내용",
        "image": "/images/example.jpg"
    },
    target_language="zh"  # 默认语言
)
```

### 2. 文章数据结构

你的文章数据应该包含以下字段：

```python
post_data = {
    "id": "unique_post_id",           # 文章唯一ID
    "topic_name": "产品发布",         # 主题名称
    "content": "中文内容",            # 中文内容（默认）
    "content_en": "English content",  # 英文内容
    "content_ja": "日本語の内容",     # 日文内容
    "content_ko": "한국어 내용",      # 韩文内容
    "image": "/images/example.jpg"    # 图片路径（可选）
}
```

### 3. 群组配置

在获取群组配置时，确保包含语言设置：

```python
# 从API获取的群组配置示例
social_chats = [
    {
        "socialGroup": "-1001234567890",
        "chats": [
            {
                "chatId": 123,
                "name": "产品发布",
                "enable": True,
                "language": "zh"  # 群组默认语言
            },
            {
                "chatId": 456,
                "name": "产品发布",
                "enable": True,
                "language": "en"  # 英文群组
            }
        ]
    }
]
```

## 配置选项

### 支持的语言

在 `multilingual_config.py` 中可以配置支持的语言：

```python
supported_languages = {
    "zh": {"name": "中文", "flag": "🇨🇳", "code": "zh-CN"},
    "en": {"name": "English", "flag": "🇺🇸", "code": "en-US"},
    "ja": {"name": "日本語", "flag": "🇯🇵", "code": "ja-JP"},
    "ko": {"name": "한국어", "flag": "🇰🇷", "code": "ko-KR"}
}
```

### 键盘布局

```python
keyboard_layout = {
    "buttons_per_row": 2,  # 每行按钮数
    "max_rows": 3          # 最大行数
}
```

### 翻译配置

```python
translation_config = {
    "fallback_language": "en",     # 回退语言
    "auto_translate": False,       # 自动翻译
    "preserve_formatting": True    # 保持格式
}
```

## 集成到现有系统

### 1. 修改 publish_posts 函数

你的 `publish_posts` 函数已经更新为使用多语言功能。确保在群组配置中包含 `language` 字段。

### 2. 注册回调处理器

在你的主机器人文件中注册回调处理器：

```python
from src.callback_handler import router as callback_router

# 注册回调处理器
dp.include_router(callback_router)
```

### 3. 实现数据获取函数

在 `callback_handler.py` 中实现 `get_post_data_by_id` 函数：

```python
async def get_post_data_by_id(post_id: str) -> dict:
    """
    根据文章ID获取文章数据
    """
    # 从数据库或API获取文章数据
    # 返回包含多语言内容的字典
    pass
```

## 语言切换按钮

用户点击语言切换按钮时，会触发以下流程：

1. 解析回调数据获取目标语言
2. 获取文章数据
3. 获取对应语言的翻译内容
4. 编辑原消息的内容和键盘
5. 显示切换成功的提示

## 错误处理

系统包含完善的错误处理机制：

- 图片下载失败时自动降级为纯文本
- 翻译内容不存在时使用回退语言
- 网络错误时显示友好的错误提示
- 详细的日志记录

## 扩展功能

### 添加新语言

1. 在 `multilingual_config.py` 中添加新语言配置
2. 更新 `LANGUAGE_TEMPLATES` 和 `COMMON_TRANSLATIONS`
3. 确保文章数据包含新语言的翻译

### 自定义翻译逻辑

你可以重写 `get_translated_content` 方法来实现自定义的翻译逻辑，比如：

- 调用外部翻译API
- 使用机器学习模型
- 实现缓存机制

### 添加更多按钮类型

除了语言切换，你还可以添加其他类型的按钮：

- 分享按钮
- 收藏按钮
- 反馈按钮

## 注意事项

1. **Markdown转义**：所有文本内容都会自动进行MarkdownV2转义
2. **图片处理**：图片会临时下载到本地，发送后自动清理
3. **消息编辑限制**：Telegram对消息编辑有一些限制，建议在发送前确保内容正确
4. **回调数据长度**：回调数据有长度限制，确保post_id不要太长

## 故障排除

### 常见问题

1. **按钮不响应**：检查是否正确注册了回调处理器
2. **翻译不显示**：检查文章数据是否包含对应语言的翻译
3. **图片显示失败**：检查图片URL是否可访问
4. **消息编辑失败**：可能是内容格式问题或权限不足

### 调试技巧

- 启用详细日志记录
- 检查回调数据格式
- 验证API响应数据
- 测试单个功能模块

## 示例代码

完整的使用示例请参考 `src/multilingual_example.py` 文件。 