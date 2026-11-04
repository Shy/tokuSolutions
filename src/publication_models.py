"""Small file-reference payloads for local staging, auditing and publication."""

from dataclasses import dataclass, field


@dataclass
class StagedManual:
    """One run's owned staging area and optimistic publication baseline."""

    run_dir: str
    staging_dir: str
    final_dir: str
    source_snapshot: str
    previous_sha256: str


@dataclass
class AuditPreparation:
    """An immutable public viewer package, never credentials or model inputs."""

    public_dir: str
    audit_dir: str
    manual_name: str
    json_sha256: str
    renderer_sha256: str
    page_count: int
    asset_sha256: dict[str, str] = field(default_factory=dict)


@dataclass
class PageAudit:
    """A completed page measurement reference or an explicitly unavailable page."""

    page: int
    success: bool
    metrics_path: str = ""
    browser_version: str = ""
    error: str = ""


@dataclass
class AuditSummary:
    """Aggregated advisory warnings, with full evidence outside Workflow history."""

    report_path: str
    pages_checked: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class PublishedManual:
    """Final direct viewer paths and the previous manual's retained backup."""

    output_dir: str
    json_path: str
    html_path: str
    backup_dir: str = ""
