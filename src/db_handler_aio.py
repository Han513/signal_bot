import os
import logging
from sqlalchemy import Column, Integer, String, DateTime, select, Boolean, delete, update
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

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