from datetime import datetime
import enum

import sqlalchemy as sa
from sqlalchemy import BigInteger, Enum, ForeignKey, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import BaseModel


class UserRole(enum.Enum):
    ADMIN = "admin"
    MEMBER = "member"


class UserModel(BaseModel):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=True)

    rooms: Mapped[list["RoomMember"]] = relationship(back_populates="user")


class RoomModel(BaseModel):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    invite_code: Mapped[str] = mapped_column(String, unique=True, index=True)
    creator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    queues: Mapped[list["QueueModel"]] = relationship(back_populates="room")
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
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    name: Mapped[str] = mapped_column(String)

    room: Mapped["RoomModel"] = relationship(back_populates="queues")
    entries: Mapped[list["QueueEntry"]] = relationship(back_populates="queue", cascade="all, delete-orphan")


class RoomMember(BaseModel):
    __tablename__ = "room_members"
    
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[UserRole] = mapped_column(
        sa.Enum(UserRole, name="userrole", native_enum=True), 
        default=UserRole.MEMBER)
    
    user: Mapped["UserModel"] = relationship(back_populates="rooms")
    room: Mapped["RoomModel"] = relationship(back_populates="members")


class QueueEntry(BaseModel):
    __tablename__ = "queue_entries"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    queue_id: Mapped[int] = mapped_column(ForeignKey("queues.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    position: Mapped[int] = mapped_column(Integer)
    joined_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    
    queue: Mapped["QueueModel"] = relationship(back_populates="entries")
    user: Mapped["UserModel"] = relationship()