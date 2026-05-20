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
            "application/json",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/xml",
            "application/zip",
            "audio/mp4",
            "audio/mpeg",
            "audio/wav",
            "audio/webm",
            "image/gif",
            "image/jpeg",
            "image/png",
            "image/webp",
            "text/csv",
            "text/html",
            "text/markdown",
            "text/plain",
            "video/mp4",
            "video/webm",
        )
    )
    presigned_url_ttl_seconds: int = 3600
