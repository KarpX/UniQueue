from datetime import datetime, timezone
import enum

import sqlalchemy as sa
from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import BaseModel


def get_utc_now():
    return datetime.now(timezone.utc)


class UserRole(enum.Enum):
    ADMIN = "admin"
    MEMBER = "member"


class UserModel(BaseModel):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    rooms: Mapped[list["RoomMember"]] = relationship(back_populates="user")


class RoomModel(BaseModel):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    invite_code: Mapped[str] = mapped_column(String, unique=True, index=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    creator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    queues: Mapped[list["QueueModel"]] = relationship(
        back_populates="room",
        cascade="all, delete-orphan"
    )
    members: Mapped[list["RoomMember"]] = relationship(
        back_populates="room", 
        cascade="all, delete-orphan" 
    )

    __table_args__ = (
        sa.UniqueConstraint('name', 'creator_id', name='_name_creator_uc'),
    )


class QueueModel(BaseModel):
    __tablename__ = "queues"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    current_speaker_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notified_next_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    room: Mapped["RoomModel"] = relationship(back_populates="queues")
    entries: Mapped[list["QueueEntry"]] = relationship(back_populates="queue", cascade="all, delete-orphan")
    swap_requests: Mapped[list["SwapRequest"]] = relationship(
        back_populates="queue", 
        cascade="all, delete-orphan"
    )


class RoomMember(BaseModel):
    __tablename__ = "room_members"
    
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[UserRole] = mapped_column(
        sa.Enum(UserRole, name="userrole", native_enum=True), 
        default=UserRole.MEMBER
    )
    
    user: Mapped["UserModel"] = relationship(back_populates="rooms")
    room: Mapped["RoomModel"] = relationship(back_populates="members")


class QueueEntry(BaseModel):
    __tablename__ = "queue_entries"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    queue_id: Mapped[int] = mapped_column(ForeignKey("queues.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now)
    
    queue: Mapped["QueueModel"] = relationship(back_populates="entries")
    user: Mapped["UserModel"] = relationship()

    __table_args__ = (
        sa.UniqueConstraint('queue_id', 'user_id', name='uq_queue_entries_queue_user'),
    )


class SwapRequest(BaseModel):
    __tablename__ = "swap_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sender_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=False
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=False
    )
    queue_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("queues.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    queue: Mapped["QueueModel"] = relationship(back_populates="swap_requests")
    sender: Mapped["UserModel"] = relationship(foreign_keys=[sender_id])
    target: Mapped["UserModel"] = relationship(foreign_keys=[target_id])