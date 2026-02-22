from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    employer_id: Mapped[int] = mapped_column(ForeignKey("employers.id"), index=True, nullable=False)

    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    employment_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    is_new_grad: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_undergrad: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    employer: Mapped["Employer"] = relationship(back_populates="jobs")
