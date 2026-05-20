from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlatformConfig:
    gateway_base_url: str = ""
    gateway_rate_limit_per_key: int = 100
    gateway_rate_limit_global: int = 1000
    discovery_rate_limit_per_key: int = 100
    discovery_rate_limit_global: int = 1000
    per_agent_rate_limit_window_seconds: int = 3600
    max_upload_size_bytes: int = 25 * 1024 * 1024
    allowed_mime_types: tuple[str, ...] = field(
        default_factory=lambda: (
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "image/gif",
            "image/jpeg",
            "image/png",
            "text/plain",
            "video/mp4",
            "video/webm",
        )
    )
    presigned_url_ttl_seconds: int = 3600
