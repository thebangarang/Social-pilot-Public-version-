"""Pydantic schemas for request/response validation."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- health ---


class HealthOut(BaseModel):
    status: str
    version: str


# --- auth (operators) ---


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class TwoFAChallengeIn(BaseModel):
    challenge: str
    code: str = Field(min_length=6, max_length=8)


class LoginResponse(BaseModel):
    """First step result. If requires_2fa, the client must submit a code."""

    requires_2fa: bool = False
    challenge: str | None = None


class OperatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: str  # admin | operator
    can_publish: bool
    language: str = "en"
    totp_enabled: bool
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class MeUpdateIn(BaseModel):
    """Self-service profile update (name / language)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    language: str | None = Field(default=None, pattern=r"^(fr|en)$")


class OperatorCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="operator", pattern=r"^(admin|operator)$")
    can_publish: bool = False
    password: str = Field(min_length=8, max_length=200)


class OperatorUpdate(BaseModel):
    """Admin edit. Any field omitted = unchanged. password set = reset."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, pattern=r"^(admin|operator)$")
    can_publish: bool | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class TwoFASetupOut(BaseModel):
    secret: str
    otpauth_uri: str
    qr_data_uri: str


class TwoFAEnableIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class TwoFADisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


# --- theme ---


class ThemeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    bg: str
    surface: str
    border: str
    fg: str
    muted: str
    accent: str
    accent_2: str
    success: str
    warning: str
    danger: str
    is_active: bool


# --- social connections ---


class ConnectedAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    platform: str
    handle: str
    account_meta: dict
    is_active: bool
    is_default: bool = False
    brand_id: uuid.UUID | None = None
    token_expires_at: datetime | None = None
    created_at: datetime


class BrandIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class BrandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    logo_path: str | None = None
    created_at: datetime


class BrandUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class ConnectedAccountBrandIn(BaseModel):
    brand_id: uuid.UUID | None = None


class BlueskyConnectIn(BaseModel):
    """Real connect payload for Bluesky (handle + App Password, BYOK)."""

    handle: str = Field(..., min_length=1, max_length=200)
    app_password: str = Field(..., min_length=1, max_length=300)


class ValidateProposalIn(BaseModel):
    """Optional brand to tag the validated post with (multi-brand)."""

    brand_id: uuid.UUID | None = None


# --- LLM settings / budget ---


class LLMSettingOut(BaseModel):
    provider: str
    base_url: str
    model: str
    image_model: str
    video_model: str
    monthly_limit: float | None = None
    topup_mode: str
    is_active: bool
    has_api_key: bool
    updated_at: datetime


class LLMSettingIn(BaseModel):
    provider: str = Field(pattern=r"^(openrouter|openai_compatible|ollama|none)$")
    base_url: str = Field(default="", max_length=255)
    model: str = Field(default="", max_length=120)
    image_model: str = Field(default="", max_length=120)
    video_model: str = Field(default="", max_length=120)
    monthly_limit: float | None = Field(default=None, ge=0)
    topup_mode: str = Field(default="manual", pattern=r"^(manual|auto)$")
    api_key: str | None = Field(default=None, max_length=500)  # set-only, never returned


class LLMTestIn(BaseModel):
    provider: str = Field(pattern=r"^(openrouter|openai_compatible|ollama|none)$")
    base_url: str = Field(default="", max_length=255)
    api_key: str | None = Field(default=None, max_length=500)


class LLMTestOut(BaseModel):
    ok: bool
    detail: str


class ModelsOut(BaseModel):
    models: list["ModelOption"]


class ModelOption(BaseModel):
    id: str
    name: str = ""


class UsageRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    provider: str
    model: str | None = None
    tokens: int | None = None
    cost: float
    created_at: datetime


class UsageSummaryOut(BaseModel):
    month: str  # YYYY-MM
    provider: str
    total_cost: float
    llm_cost: float
    media_cost: float
    total_all_time: float = 0.0  # sum of all usage records (ever)
    monthly_limit: float | None
    remaining: float | None
    topup_mode: str
    record_count: int


# --- generation (chat / media / branding / presets / proposals) ---


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    media_ids: list
    media_brief: str | None = None
    platforms: list | None = None
    created_at: datetime


class MediaItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    original_name: str
    mime: str | None
    width: int | None
    height: int | None
    duration_s: float | None
    thumb_path: str | None = None
    usage_count: int = 0
    size_bytes: int
    brand_id: uuid.UUID | None = None
    created_at: datetime


class BrandAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    filename: str
    notes: str
    brand_id: uuid.UUID | None = None
    created_at: datetime


class BrandAssetIn(BaseModel):
    kind: str = Field(pattern=r"^(logo|color|font|visual|guideline|condition|other)$")
    filename: str = Field(min_length=1, max_length=255)
    notes: str = Field(default="", max_length=1000)
    media_id: uuid.UUID | None = None
    brand_id: uuid.UUID | None = None


class StylePresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    directives: str
    is_builtin: bool
    brand_id: uuid.UUID | None = None
    created_at: datetime


class StylePresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    directives: str = Field(default="", max_length=5000)
    brand_id: uuid.UUID | None = None


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: int
    content: dict
    status: str
    created_at: datetime


class SendMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    media_ids: list[uuid.UUID] = Field(default_factory=list)
    preset_id: str | None = None
    branding_usage: dict | None = None  # {use_logo, logo_placement, use_colors, use_font}
    media_type: str | None = None  # "image" | "video" | None (auto)
    on_media_text: dict | None = None  # {mode: auto|no|yes, text}
    media_brief: str | None = None  # user's media-dedicated prompt
    platforms: list[str] = Field(default_factory=list)  # target platforms (e.g. ["tiktok","instagram"])


class SendMessageOut(BaseModel):
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut
    proposals: list[ProposalOut]


class AdjustProposalIn(BaseModel):
    proposal_id: uuid.UUID
    feedback: str = Field(min_length=1, max_length=8000)


class AdjustProposalOut(BaseModel):
    assistant_message: ChatMessageOut
    proposals: list[ProposalOut]


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    session_id: uuid.UUID
    proposal_id: uuid.UUID | None
    brand_id: uuid.UUID | None = None
    title: str
    content: dict
    media_ids: list
    media_variants: dict | None = None
    source_media_ids: list | None = None
    branding_usage: dict | None = None
    media_type: str | None = None  # image | video | text | None
    on_media_text: dict | None = None  # {mode: auto|no|yes, text}
    media_brief: str | None = None  # user's media-dedicated prompt
    compliance: dict | None
    status: str
    created_at: datetime
    updated_at: datetime


class PublicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    post_id: uuid.UUID
    platform: str
    account_id: uuid.UUID | None
    caption: str
    media_ids: list
    status: str
    scheduled_at: datetime | None
    published_at: datetime | None
    remote_post_id: str | None
    remote_url: str | None
    idempotency_key: str
    attempt_count: int = 0
    last_error: str | None = None
    created_at: datetime


class PublicationCreateIn(BaseModel):
    platforms: list[str] = Field(min_length=1)


class PublishIn(BaseModel):
    confirm: bool = False
    scheduled_at: datetime | None = None
    caption: str | None = None  # optional final caption override (manual edit)


class RegenerateMediaIn(BaseModel):
    feedback: str = Field(min_length=1, max_length=4000)


class ValidateProposalOut(BaseModel):
    proposal: ProposalOut
    post: "PostOut | None" = None


# --- performance / analytics ---


class PostMetricOut(BaseModel):
    publication_id: uuid.UUID
    post_id: uuid.UUID
    platform: str
    caption: str
    published_at: datetime | None = None
    remote_url: str | None = None
    metrics: dict  # {metric_key: value}
    engagement_rate: float | None = None


class PerformanceOverviewOut(BaseModel):
    totals: dict  # aggregated metric totals (None = metric not available)
    engagement_rate: float | None = None
    post_count: int
    posts: list[PostMetricOut]
    trend: list[dict]  # [{t, views, likes, comments}]


class PostDetailOut(BaseModel):
    publication_id: uuid.UUID
    platform: str
    caption: str
    published_at: datetime | None = None
    remote_url: str | None = None
    metrics: dict
    series: list[dict]  # [{t, views, likes, comments, ...}]


class CalendarEntryOut(BaseModel):
    publication_id: uuid.UUID
    post_id: uuid.UUID
    platform: str
    caption: str
    status: str  # scheduled | published
    date: str  # YYYY-MM-DD (the day it sits on in the calendar)
    time: str | None = None  # HH:MM
    scheduled_at: datetime | None = None
    published_at: datetime | None = None
    remote_url: str | None = None
    media: dict | None = None  # {id, kind} of the first media item


class LeaderboardEntryOut(BaseModel):
    publication_id: uuid.UUID
    platform: str
    caption: str
    published_at: datetime | None = None
    media_type: str | None = None
    views: int
    engagement_rate: float | None = None


class FormatPerfOut(BaseModel):
    format: str
    count: int
    avg_engagement_rate: float | None = None


class AnomalyOut(BaseModel):
    publication_id: uuid.UUID
    platform: str
    caption: str
    published_at: datetime | None = None
    media_type: str | None = None
    views: int
    engagement_rate: float | None = None
    kind: str  # high | low


class InsightsOut(BaseModel):
    leaderboard: list[LeaderboardEntryOut]
    formats: list[FormatPerfOut]
    anomalies: list[AnomalyOut]
    median_engagement_rate: float | None = None


class BestTimeCellOut(BaseModel):
    day: int  # 0=Monday … 6=Sunday
    hour: int
    count: int
    avg_rate: float | None = None
    score: float | None = None  # relative to the median (None = untested)


class BestTimeOut(BaseModel):
    median_engagement_rate: float | None = None
    top_slots: list[BestTimeCellOut]
    grid: list[BestTimeCellOut]


# --- community manager ---


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    publication_id: uuid.UUID
    platform: str
    author_name: str
    text: str
    level: str  # green | orange | red
    status: str  # new | replied | skipped
    reply_draft: str | None = None
    replied_at: datetime | None = None
    created_at: datetime


class ReplyIn(BaseModel):
    text: str | None = None  # optional override of the generated draft


class BatchGenerateIn(BaseModel):
    comment_ids: list[uuid.UUID]


class BatchReplyItem(BaseModel):
    comment_id: uuid.UUID
    text: str | None = None


class BatchReplyIn(BaseModel):
    items: list[BatchReplyItem]
