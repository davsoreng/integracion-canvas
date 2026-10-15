from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


class CanvasCourse(BaseModel):
    id: int
    name: str
    course_code: Optional[str] = None
    enrollment_state: Optional[str] = None


class CanvasFile(BaseModel):
    id: int
    display_name: str
    filename: str
    url: str
    size: int
    updated_at: Optional[str] = None
    content_type: Optional[str] = None
    module_name: Optional[str] = None


class CanvasAssignment(BaseModel):
    id: int
    course_id: int
    name: str
    description: Optional[str] = ""
    due_at: Optional[datetime] = None
    html_url: Optional[str] = None
    points_possible: Optional[float] = None
    submission_status: Optional[str] = "unsubmitted"  # "submitted", "graded", "unsubmitted"
    is_submitted: bool = False
    grade: Optional[str] = None
    score: Optional[float] = None
    submitted_at: Optional[datetime] = None
    summary_ai: Optional[str] = None


class SyncStats(BaseModel):
    courses_processed: int = 0
    files_uploaded: int = 0
    files_skipped: int = 0
    calendar_events_created: int = 0
    calendar_events_updated: int = 0
    calendar_events_skipped: int = 0
    errors: List[str] = Field(default_factory=list)


class SyncResponse(BaseModel):
    success: bool
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    stats: SyncStats
