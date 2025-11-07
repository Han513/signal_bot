import os
import asyncio
import logging
from sqlalchemy import Column, Integer, String, DateTime, select, Boolean, delete, update, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError

MAX_OVERFLOW = 30

load_dotenv()
Base = declarative_base()
database_url = os.getenv('DATABASE_URI_SWAP')
engine = create_async_engine(
    database_url,
    echo=False,
    future=True,
    pool_size=30,
    max_overflow=MAX_OVERFLOW
)
Session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

class Group(Base):
    __tablename__ = 'groups'

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String(50), unique=True, nullable=False)
    title = Column(String(255), nullable=True)
    type = Column(String(50), nullable=False)
    username = Column(String(255), nullable=True)
    description = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    join_date = Column(DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False)
    leave_date = Column(DateTime(timezone=True), nullable=True)
    member_count = Column(Integer, nullable=True)

    def to_dict(self):
        """將 Group 模型轉換為字典"""
        return {
            'id': self.id,
            'chat_id': self.chat_id,
            'title': self.title,
            'type': self.type,
            'username': self.username,
            'description': self.description,
            'is_active': self.is_active,
            'join_date': self.join_date.isoformat() if self.join_date else None,
            'leave_date': self.leave_date.isoformat() if self.leave_date else None,
            'member_count': self.member_count
        }

class VerifyUser(Base):
    __tablename__ = 'verified_users'

    utc_plus_8 = timezone(timedelta(hours=8))
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), unique=True, nullable=False)  # 用户 ID，唯一值
    verify_group_id = Column(String(50), nullable=False)  # 用户验证通过的群组 ID
    info_group_id = Column(String(50), nullable=False)  # 用户验证通过的群组 ID
    verify_code = Column(String(50), nullable=False)  # 用户验证通过的群组 ID
    verified_at = Column(DateTime(timezone=True), default=datetime.now(utc_plus_8), nullable=False)  # 验证时间
    is_active = Column(Boolean, default=True, nullable=False)  # 用户是否仍然有效

    def to_dict(self):
        """将 VerifyUser 模型转换为字典"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'verify_group_id': self.verify_group_id,
            'info_group_id': self.info_group_id,
            'verify_code': self.verify_code,
            'verified_at': self.verified_at.isoformat() if self.verified_at else None,
            'is_active': self.is_active,
        }

async def insert_or_update_group(chat_id, title, group_type, username=None, description=None, member_count=None):
    """插入或更新群組資訊"""
    utc_plus_8 = timezone(timedelta(hours=8))
    async with Session() as session:
        try:
            async with session.begin():
                # 先檢查是否已存在相同 chat_id 的群組
                existing_group = await session.execute(
                    select(Group).where(Group.chat_id == str(chat_id))
                )
                existing_group = existing_group.scalar_one_or_none()

                if existing_group:
                    # 如果群組已存在，更新資訊
                    existing_group.title = title
                    existing_group.type = group_type
                    existing_group.username = username
                    existing_group.description = description
                    existing_group.member_count = member_count
                    existing_group.is_active = True
                    existing_group.join_date = datetime.now(utc_plus_8)
                    existing_group.leave_date = None
                else:
                    # 創建新的群組記錄
                    new_group = Group(
                        chat_id=str(chat_id),
                        title=title,
                        type=group_type,
                        username=username,
                        description=description,
                        member_count=member_count
                    )
                    session.add(new_group)

            await session.commit()
            return True
        except Exception as e:
            logging.error(f"插入/更新群組時發生錯誤: {e}")
            await session.rollback()
            return False

async def deactivate_group(chat_id):
    """停用群組（當 Bot 被移除）"""
    utc_plus_8 = timezone(timedelta(hours=8))
    async with Session() as session:
        try:
            async with session.begin():
                stmt = (
                    update(Group)
                    .where(Group.chat_id == str(chat_id))
                    .values(
                        is_active=False,
                        leave_date=datetime.now(utc_plus_8)
                    )
                )
                await session.execute(stmt)
            await session.commit()
            return True
        except Exception as e:
            logging.error(f"停用群組時發生錯誤: {e}")
            await session.rollback()
            return False

async def get_active_groups():
    """獲取所有活躍的群組 ID"""
    async with Session() as session:
        try:
            result = await session.execute(
                select(Group.chat_id).where(Group.is_active == True)
            )
            active_group_ids = [str(row[0]) for row in result.fetchall()]
            return active_group_ids
        except Exception as e:
            logging.error(f"獲取活躍群組時發生錯誤: {e}")
            return []

async def add_verified_user(user_id: str, verify_group_id: str, info_group_id: str, verify_code:int):
    """将已验证用户添加到数据库中"""
    utc_plus_8 = timezone(timedelta(hours=8))
    async with Session() as session:
        try:
            async with session.begin():
                # 检查是否存在
                stmt = select(VerifyUser).where(VerifyUser.user_id == user_id)
                result = await session.execute(stmt)
                existing_user = result.scalar_one_or_none()

                if existing_user:
                    # 更新现有记录
                    stmt_update = (
                        update(VerifyUser)
                        .where(VerifyUser.user_id == user_id)
                        .values(
                            verify_group_id=verify_group_id,
                            info_group_id=info_group_id,
                            verify_code=verify_code,
                            verified_at=datetime.now(utc_plus_8),
                            is_active=True
                        )
                    )
                    await session.execute(stmt_update)
                    return False  # 用户已存在
                else:
                    # 添加新用户
                    new_user = VerifyUser(user_id=user_id, verify_group_id=verify_group_id, info_group_id=info_group_id, verify_code=verify_code, verified_at=datetime.now(utc_plus_8), is_active=True)
                    session.add(new_user)
                    return True  # 新用户插入成功
        except IntegrityError:
            await session.rollback()
            raise

async def is_user_verified(user_id: str, verify_group_id: str, verify_code: str) -> str:
    """检查用户是否已验证，带重试机制处理数据库连接问题"""
    max_retries = 3
    retry_delay = 1  # 秒
    
    for attempt in range(max_retries):
        try:
            async with Session() as session:
                # 首先检查全局UID重复（跨所有群组）- 使用 scalars().all() 避免 Multiple rows 错误
                global_uid_stmt = select(VerifyUser).where(
                    VerifyUser.verify_code == verify_code,
                    VerifyUser.is_active == True
                )
                global_result = await session.execute(global_uid_stmt)
                global_records = global_result.scalars().all()

                if global_records:
                    # 检查是否有其他用户使用了这个UID
                    for record in global_records:
                        if record.user_id != user_id:
                            logging.warning(f"UID {verify_code} already used by user {record.user_id}, current user: {user_id}")
                            return "warning"
                    
                    # 如果所有记录都是当前用户的，返回已验证
                    return "verified"
                
                # 如果没有全局重复，检查特定群组的验证状态（如果提供了verify_group_id）
                if verify_group_id and verify_group_id.strip():
                    group_stmt = select(VerifyUser).where(
                        VerifyUser.verify_group_id == verify_group_id,
                        VerifyUser.verify_code == verify_code,
                        VerifyUser.is_active == True
                    )
                    group_result = await session.execute(group_stmt)
                    group_records = group_result.scalars().all()

                    if group_records:
                        # 检查是否有其他用户使用了这个UID
                        for record in group_records:
                            if record.user_id != user_id:
                                return "warning"
                
                # 如果没有匹配的记录，返回未验证
                return "not_verified"
                
        except Exception as e:
            logging.error(f"检查用户是否已验证时发生错误 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                logging.info(f"等待 {retry_delay} 秒后重试...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # 指数退避
            else:
                logging.error(f"所有重试尝试都失败了，返回错误状态")
                return "error"
        
async def cleanup_duplicate_verify_codes():
    """清理重复的验证码记录，保留最新的记录"""
    async with Session() as session:
        try:
            async with session.begin():
                # 查找重复的verify_code
                duplicate_stmt = (
                    select(VerifyUser.verify_code)
                    .group_by(VerifyUser.verify_code)
                    .having(func.count(VerifyUser.verify_code) > 1)
                )
                duplicate_result = await session.execute(duplicate_stmt)
                duplicate_codes = duplicate_result.scalars().all()
                
                cleaned_count = 0
                for verify_code in duplicate_codes:
                    # 对每个重复的verify_code，保留最新的记录，删除其他记录
                    stmt = (
                        select(VerifyUser)
                        .where(VerifyUser.verify_code == verify_code)
                        .order_by(VerifyUser.verified_at.desc())
                    )
                    result = await session.execute(stmt)
                    records = result.scalars().all()
                    
                    # 保留第一条（最新的），删除其他
                    if len(records) > 1:
                        for record in records[1:]:
                            await session.delete(record)
                            cleaned_count += 1
                
                if cleaned_count > 0:
                    logging.info(f"清理了 {cleaned_count} 条重复的验证码记录")
                return cleaned_count
        except Exception as e:
            logging.error(f"清理重复验证码记录时发生错误: {e}")
            return 0

async def get_verified_user(user_id: str, info_group_id: str) -> bool:
    """
    检查用户是否已验证，并确认用户的 info_group_id 是否与当前群组 ID 匹配。
    """
    async with Session() as session:
        try:
            stmt = select(VerifyUser).where(
                VerifyUser.user_id == user_id,
                VerifyUser.info_group_id == info_group_id,
                VerifyUser.is_active == True
            )
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            return user is not None  # 如果找到匹配记录，则返回 True，否则返回 False
        except Exception as e:
            logging.error(f"检查用户是否已验证时发生错误: {e}")
            return False
