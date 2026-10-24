"""Typed schemas for ToolSafeSim.

Hidden vs. model-visible: fields declared in `HIDDEN_*` constants must never reach the
classifier input. `Sample.model_input()` is the single sanctioned projection.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

POLICY_VERSION = "1.0"
GENERATOR_VERSION = "0.1"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- enums
class Label(str, Enum):
    SAFE = "SAFE"
    UNSAFE = "UNSAFE"


class ReasonCode(str, Enum):
    PERMITTED_ACTION = "PERMITTED_ACTION"
    GOAL_SCOPE_VIOLATION = "GOAL_SCOPE_VIOLATION"
    UNAUTHORIZED_MUTATION = "UNAUTHORIZED_MUTATION"
    UNAUTHORIZED_DISCLOSURE = "UNAUTHORIZED_DISCLOSURE"
    INJECTION_COMPLIANCE = "INJECTION_COMPLIANCE"
    MISSING_APPROVAL = "MISSING_APPROVAL"
    OUT_OF_SCOPE_RECIPIENT = "OUT_OF_SCOPE_RECIPIENT"
    UNRESOLVED_REVIEW = "UNRESOLVED_REVIEW"  # internal only, never a dataset label


class SideEffect(str, Enum):
    NONE = "none"
    INTERNAL = "internal"
    EXTERNAL = "external"
    DESTRUCTIVE = "destructive"


class TrustOrigin(str, Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


# --------------------------------------------------------------------------- entities
class Email(Strict):
    email_id: str
    sender_name: str
    sender_address: str
    to: list[str]
    cc: list[str] = []
    subject: str
    body: str
    received_at: str
    folder: str = "inbox"
    attachment_ids: list[str] = []
    read_status: bool = False
    trust_origin: TrustOrigin = TrustOrigin.UNKNOWN  # HIDDEN simulator metadata

    def agent_view(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"trust_origin"})


class Draft(Strict):
    draft_id: str
    to: list[str]
    cc: list[str] = []
    subject: str
    body: str
    attachment_ids: list[str] = []
    created_at: str = ""
    status: Literal["draft", "sent", "blocked"] = "draft"


class SentMessage(Strict):
    sent_id: str
    draft_id: str
    to: list[str]
    cc: list[str] = []
    subject: str
    body: str
    sent_at: str = ""


class CalendarEvent(Strict):
    event_id: str
    title: str
    description: str = ""
    start: str
    end: str
    organizer: str = ""
    attendees: list[str] = []
    visibility: str = "default"
    source: str = "fixture"
    status: str = "confirmed"


class Contact(Strict):
    contact_id: str
    name: str
    email: str
    organization: str = ""
    allowed_for_external_communication: bool = False


class Document(Strict):
    document_id: str
    path: str
    title: str
    content: str
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    owner: str = ""
    allowed_principals: list[str] = []
    version: int = 1


class WebPage(Strict):
    url: str
    title: str
    content: str
    page_id: str = ""
    domain: str = ""
    author: str = ""
    published_at: str = ""
    content_type: str = "article"
    trust_provenance: str = "publisher"  # publisher|organization|anonymous|user_generated
    sensitivity: Sensitivity = Sensitivity.PUBLIC
    trust_origin: TrustOrigin = TrustOrigin.EXTERNAL  # HIDDEN
    contains_untrusted_content: bool = False  # HIDDEN


class SearchEntry(Strict):
    """Fixture-defined ranking: query terms -> ordered results."""
    terms: list[str]
    url: str
    title: str
    snippet: str
    provenance: str = "publisher"
    published_at: str = ""


class DomainPolicy(Strict):
    domain: str
    trust_zone: Literal["public", "trusted", "restricted", "blocked"] = "public"
    note: str = ""


class VFile(Strict):
    path: str
    content: str
    file_id: str = ""
    file_type: str = "text"
    owner: str = "user"
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    readable_by: list[str] = []
    writable_by: list[str] = []
    shareable_with: list[str] = []
    immutable: bool = False
    version: int = 1
    protected: bool = False  # HIDDEN
    contains_untrusted_content: bool = False  # HIDDEN
    synthetic_secret_markers: list[str] = []  # HIDDEN


class Principal(Strict):
    principal_id: str
    principal_type: Literal["user", "internal", "external", "service"]
    organization: str = ""
    clearance_level: Sensitivity = Sensitivity.INTERNAL
    active: bool = True
    allowed_actions: list[str] = []


class Destination(Strict):
    destination_id: str  # e.g. workspace://team/project-alpha
    owner: str
    trust_zone: Literal["private", "team", "external", "public"]
    accepted_sensitivity: list[Sensitivity] = []
    write_policy: str = "owner_only"


class Commit(Strict):
    commit_id: str
    message: str
    paths: list[str]
    author: str = "agent"
    timestamp: str = ""


class ProtectedAsset(Strict):
    asset_id: str  # e.g. "invoice:INV-204", "file:/repo/.env"
    resource_id: str = ""
    protected_fields: list[str] = []
    allowed_readers: list[str] = []
    allowed_mutators: list[str] = []
    allowed_recipients: list[str] = []
    immutable: bool = False
    rules: list[str] = []


class Permission(Strict):
    resource_id: str
    principal: str
    permission: str


# --------------------------------------------------------------------------- scenario
class AuthorizationPolicy(Strict):
    allowed_actions: list[str] = []
    forbidden_actions: list[str] = []
    allowed_resources: list[str] = []
    allowed_recipients: list[str] = []
    approval_required: list[str] = []
    approvals_granted: list[str] = []
    disclosure_rules: list[str] = []


class InitialState(Strict):
    fixture_pack: str
    overrides: dict[str, Any] = {}


class UntrustedContent(Strict):
    location: str  # e.g. "email:em_vendor_04"
    kind: Literal["benign", "injection"] = "injection"
    text: str = ""


class AttackMeta(Strict):  # HIDDEN: evaluation only
    type: str = "none"
    location: Optional[str] = None


class Scenario(Strict):
    scenario_id: str
    scenario_family_id: str
    category: Literal["productivity", "database", "codebase", "web", "files_security"]
    seed: int
    user_goal: str
    initial_state: InitialState
    authorization: AuthorizationPolicy
    protected_assets: list[ProtectedAsset] = []
    untrusted_content: list[UntrustedContent] = []
    success_criteria: list[str] = []
    attack: AttackMeta = AttackMeta()
    max_steps: int = Field(default=12, ge=1, le=50)
    policy_version: str = POLICY_VERSION
    generator_version: str = GENERATOR_VERSION


# --------------------------------------------------------------------------- calls/decisions
class ToolCall(Strict):
    name: str
    arguments: dict[str, Any] = {}


class Decision(Strict):
    label: Label
    reason_code: ReasonCode
    evidence: list[str] = []
    rule_ids: list[str] = []
    policy_version: str = POLICY_VERSION


EventType = Literal[
    "episode_start", "proposal", "decision", "tool_result", "blocked",
    "malformed", "state_transition", "final_answer", "episode_end",
]


class TraceEvent(Strict):
    event_id: str
    episode_id: str
    scenario_id: str
    seq: int
    step: int
    type: EventType
    payload: dict[str, Any] = {}
    state_version: int = 0
    state_hash: str = ""


# --------------------------------------------------------------------------- dataset row
class ContextItem(Strict):
    role: Literal["assistant_tool_call", "tool"]
    tool: Optional[str] = None
    arguments: Optional[dict[str, Any]] = None
    observation: Optional[str] = None


class Sample(Strict):
    sample_id: str
    scenario_id: str
    scenario_family_id: str
    episode_id: str
    step: int
    user_goal: str
    context: list[ContextItem]
    proposed_tool_call: ToolCall
    label: Label
    reason_code: ReasonCode
    evidence: list[str] = []
    scenario_tags: list[str] = []  # HIDDEN metadata
    context_policy: str = "full_history"
    policy_version: str = POLICY_VERSION
    generator_version: str = GENERATOR_VERSION

    def model_input(self) -> dict[str, Any]:
        """The ONLY fields a runtime classifier may see."""
        return {
            "user_goal": self.user_goal,
            "context": [c.model_dump(mode="json", exclude_none=True) for c in self.context],
            "proposed_tool_call": self.proposed_tool_call.model_dump(mode="json"),
        }


MODEL_VISIBLE_KEYS = frozenset({"user_goal", "context", "proposed_tool_call"})
