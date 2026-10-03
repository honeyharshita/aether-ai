import datetime as dt
from typing import Optional, Any
from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ---- auth ----
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str
    role: str = "VIEWER"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    role: str

    class Config:
        from_attributes = True


# ---- projects ----
class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    is_demo: bool = False


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    is_demo: bool
    created_at: dt.datetime

    class Config:
        from_attributes = True


# ---- repositories ----
class RepositoryConnect(BaseModel):
    project_id: str
    provider: str = "github"
    full_name: str  # accepts "owner/repo" OR a full GitHub URL; normalized in the router


class RepositoryOut(BaseModel):
    id: str
    project_id: str
    provider: str
    full_name: str
    last_synced_at: Optional[dt.datetime]

    class Config:
        from_attributes = True


# ---- issues / tasks ----
class IssueCreate(BaseModel):
    project_id: str
    title: str
    issue_type: str = "bug"
    severity: str = "medium"
    related_module_path: Optional[str] = None


class TaskCreate(BaseModel):
    project_id: str
    title: str
    story_points: float = 0
    dependency_count: int = 0
    blocking_count: int = 0
    effort_hours: float = 0
    deadline: Optional[dt.datetime] = None


class IssueOut(BaseModel):
    id: str
    project_id: str
    title: str
    issue_type: str
    severity: str
    status: str
    related_module_path: Optional[str] = None
    reopen_count: int
    created_at: dt.datetime
    resolved_at: Optional[dt.datetime] = None

    class Config:
        from_attributes = True


class TaskOut(BaseModel):
    id: str
    project_id: str
    title: str
    status: str
    story_points: float
    dependency_count: int
    blocking_count: int
    effort_hours: float
    deadline: Optional[dt.datetime] = None
    created_at: dt.datetime
    completed_at: Optional[dt.datetime] = None

    class Config:
        from_attributes = True


# ---- predictions / explanations ----
class PredictionOut(BaseModel):
    id: str
    project_id: str
    target_type: str
    target_ref: str
    probability: Optional[float]
    score: Optional[float]
    confidence: Optional[float]
    model_name: str
    model_version: str
    predicted_at: dt.datetime

    class Config:
        from_attributes = True
        protected_namespaces = ()


class ExplanationOut(BaseModel):
    prediction_id: str
    top_positive_factors: Any
    top_negative_factors: Any
    explanation_text: str

    class Config:
        from_attributes = True


# ---- recommendations ----
class RecommendationOut(BaseModel):
    id: str
    project_id: str
    recommended_action: str
    reason: str
    priority: str
    responsible_role: str
    status: str
    related_ref: Optional[str]
    created_at: dt.datetime

    class Config:
        from_attributes = True


class RecommendationActionUpdate(BaseModel):
    status: str  # accepted / rejected / completed
