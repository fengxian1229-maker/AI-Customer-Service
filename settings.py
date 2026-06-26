import os
from typing import Dict, Optional, List

from pydantic import Field
from pydantic_settings import BaseSettings


def _resolve_env_file() -> Optional[str]:
    env_file = os.getenv("QA_ENV_FILE")
    if env_file:
        return env_file
    env_name = os.getenv("QA_ENV", "").strip().lower()
    if not env_name:
        return None
    env_map = {
        "prod": ".env.prod",
        "production": ".env.prod",
        "test": ".env.test",
        "testing": ".env.test",
        "staging": ".env.test",
    }
    return env_map.get(env_name)


class ConfigSettings(BaseSettings):
    filter_keywords: List[str] = Field(
        default=[
            "照片已傳送",
            "圖片已發送",
            "语音讯息已传送",
            "貼圖已發送",
            "คัดลอก 🆔",
            "ถอน",
            "貼圖已傳送",
        ],
        description="Keywords to filter out from messages.",
    )
    support_multimedia_formats: List[str] = Field(
        default=[
            "text",
            "image",
            "video",
            "audio",
            "pdf",
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/heic",
            "image/heif",
            "video/mp4",
            "video/mpeg",
            "video/mov",
            "video/avi",
            "video/x-flv",
            "video/mpg",
            "video/webm",
            "video/wmv",
            "video/3gpp",
            "audio/wav",
            "audio/mp3",
            "audio/aiff",
            "audio/aac",
            "audio/ogg",
            "audio/flac",
        ],
        description="Supported multimedia formats by the model.",
    )
    category_labels: Dict[str, str] = {
        "violation_scenarios": "违规场景",
        "non_violation_scenarios": "不违规场景",
        "special_scenarios": "特殊场景",
    }


class CacheSettings(BaseSettings):
    project: str = Field(
        # default="bk-gemini-1118",
        default="project-gemini-0306",
        description="GCP project for cached prompts or artifacts.",
    )
    location: str = Field(
        # default="global",
        default="us-east1",
        description="GCP region for cache.",
    )
    gemini2_location: str = Field(
        default="global",
        description="GCP region for Gemini 2.x models.",
    )
    gemini3_location: str = Field(
        default="global",
        description="GCP region for Gemini 3.x models.",
    )
    google_application_credentials: Optional[str] = Field(
        default=None,
        description="Optional path to GCP service account JSON for Vertex AI (ADC).",
    )
    cache_display_name: str = Field(
        default="quality-analyzer-prompt-v20",
        description="Display name for cache entry.",
    )
    ttl: str = Field(default="3600s", description="Default TTL for cached items.")
    cache_refresh_threshold_seconds: int = Field(
        default=600,
        ge=0,
        description="Refresh cached content when remaining TTL is below this threshold.",
    )
    local_cache_ttl_seconds: int = Field(
        default=300, ge=1, description="TTL for in-memory prompt/rule caches."
    )
    local_cache_maxsize: int = Field(
        default=2048, ge=1, description="Max entries for in-memory prompt/rule caches."
    )
    model_instance_cache_maxsize: int = Field(
        default=256, ge=1, description="Max cached model instances."
    )
    model_instance_ttl_seconds: int = Field(
        default=3600, ge=1, description="TTL for cached model instances."
    )


class ModelSettings(BaseSettings):
    aliases: Dict[str, str] = Field(
        default_factory=lambda: {
            "gemini-2.5-flash": "gemini-2.5-flash",
            "gpt-5": "gpt-5",
            "glm-4.7": "glm-4.7",
            "deepseek-r1": "deepseek-r1",
            "translate_model": "gpt-4.1-mini",
            "qwen-max": "qwen-max",
            "gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite-preview",
            "gemini-3-flash-preview": "gemini-3-flash-preview"
        },
        description="Allowed model aliases mapping to provider model ids.",
    )
    default_model: str = Field(
        default="glm-4.7",
        description="Default model name when none is provided.",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Default OpenAI-compatible base URL.",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API key for OpenAI-compatible providers.",
    )
    openai_timeout_seconds: int = Field(
        default=120,
        ge=1,
        description="Timeout for OpenAI-compatible chat completion requests.",
    )
    openai_max_retries: int = Field(
        default=2,
        ge=0,
        description="Client-level retries for OpenAI-compatible chat completion requests.",
    )
    prompt_cache_enabled: bool = Field(
        default=False,
        description=(
            "Whether to pass OpenAI prompt-cache routing params for OpenAI-compatible "
            "models when a cache_id is provided."
        ),
    )
    prompt_cache_retention: Optional[str] = Field(
        default=None,
        description="Optional OpenAI prompt_cache_retention value, for example '24h'.",
    )
    retry_model_allowlist: List[str] = Field(
        default_factory=list,
        description="Optional ordered allowlist of model aliases used for regular retry model switching.",
    )
    oversize_retry_model_allowlist: List[str] = Field(
        default_factory=list,
        description="Ordered allowlist of model aliases used when handling input_too_long retry.",
    )
    context_window_by_alias: Dict[str, int] = Field(
        default_factory=dict,
        description="Optional model context-window sizes keyed by alias or provider model id.",
    )
    oversize_retry_max_hops: int = Field(
        default=1,
        ge=0,
        description="Maximum model-switch retry hops allowed for input_too_long failures.",
    )
    oversize_context_headroom: int = Field(
        default=2048,
        ge=0,
        description="Additional context headroom required when choosing oversize retry models.",
    )


class GeminiBatchSettings(BaseSettings):
    enabled: bool = Field(
        default=True,
        description="Enable native Gemini batch processing for async assessment jobs.",
    )
    project: Optional[str] = Field(
        default="project-gemini-0306",
        description="GCP project for Gemini batch jobs. Falls back to cache.project when empty.",
    )
    location: Optional[str] = Field(
        default="global",
        description="GCP location for Gemini batch jobs. Falls back to cache.location when empty.",
    )
    input_bucket: str = Field(
        default="gemini-batch-test-123456",
        description="GCS bucket used for batch input JSONL uploads.",
    )
    input_prefix: str = Field(
        default="aihelper/gemini-batch/input",
        description="GCS prefix for uploaded input JSONL files.",
    )
    output_prefix: str = Field(
        default="aihelper/gemini-batch/output",
        description="GCS prefix for Gemini batch output artifacts.",
    )
    poll_interval_seconds: int = Field(
        default=20,
        ge=1,
        description="Polling interval for async Gemini batch runner.",
    )
    scan_limit: int = Field(
        default=20,
        ge=1,
        description="Maximum number of jobs scanned per polling cycle.",
    )
    create_timeout_seconds: int = Field(
        default=120,
        ge=1,
        description="Timeout for creating Gemini batch jobs.",
    )
    metadata_timeout_seconds: int = Field(
        default=60,
        ge=1,
        description="Timeout for loading Gemini batch metadata/status.",
    )
    output_read_limit: int = Field(
        default=3000,
        ge=1,
        description="Maximum output jsonl lines read for one provider batch output.",
    )
    aggregate_max_sessions_per_batch: int = Field(
        default=1000,
        ge=1,
        description="Maximum session rows merged into one provider batch JSONL.",
    )
    aggregate_max_wait_seconds: int = Field(
        default=60,
        ge=1,
        description="Maximum wait time before flushing an underfilled aggregation bucket.",
    )
    aggregate_scan_session_limit: int = Field(
        default=5000,
        ge=1,
        description="Maximum queued sessions scanned per dispatcher cycle.",
    )
    aggregate_scan_page_size: int = Field(
        default=250,
        ge=1,
        description="Page size used when scanning queued sessions from Redis.",
    )
    aggregate_candidate_pool_multiplier: int = Field(
        default=2,
        ge=1,
        description=(
            "Multiplier applied to target sessions per cycle when building candidate pool; "
            "bounded by aggregate_scan_session_limit."
        ),
    )
    aggregate_dispatch_batches_per_cycle: int = Field(
        default=2,
        ge=1,
        description="Maximum provider batches created in one dispatcher cycle.",
    )
    aggregate_max_inflight_batches: int = Field(
        default=5,
        ge=1,
        description="Maximum running/submitted provider batches processed concurrently.",
    )
    assessment_max_inflight_batches: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Optional inflight provider batch cap for async assessment runner. "
            "Falls back to aggregate_max_inflight_batches when unset."
        ),
    )
    issue_classification_max_inflight_batches: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Optional inflight provider batch cap for async issue classification runner. "
            "Falls back to aggregate_max_inflight_batches when unset."
        ),
    )
    orphan_repair_scan_limit: int = Field(
        default=200,
        ge=1,
        description="Maximum orphan provider batches scanned for repair per runner cycle.",
    )
    assessment_orphan_repair_scan_limit: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Optional orphan-repair scan cap for async assessment runner. "
            "Falls back to orphan_repair_scan_limit when unset."
        ),
    )
    issue_classification_orphan_repair_scan_limit: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Optional orphan-repair scan cap for async issue classification runner. "
            "Falls back to orphan_repair_scan_limit when unset."
        ),
    )
    provider_claim_lock_seconds: int = Field(
        default=30,
        ge=1,
        description="Lease duration for provider-batch claim locks used by claim/orphan repair.",
    )
    assessment_provider_claim_lock_seconds: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Optional provider-batch claim-lock lease for async assessment runner. "
            "Falls back to provider_claim_lock_seconds when unset."
        ),
    )
    issue_classification_provider_claim_lock_seconds: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Optional provider-batch claim-lock lease for async issue classification runner. "
            "Falls back to provider_claim_lock_seconds when unset."
        ),
    )
    direct_retry_enabled: bool = Field(
        default=True,
        description="Retry failed batch sessions directly via ModelManager instead of re-aggregating.",
    )
    direct_retry_scan_limit: int = Field(
        default=100,
        ge=1,
        description="Maximum RETRY_PENDING sessions scanned per runner cycle.",
    )
    direct_retry_concurrency: int = Field(
        default=5,
        ge=1,
        description="Maximum direct retry sessions processed concurrently.",
    )
    direct_retry_max_attempts: int = Field(
        default=5,
        ge=1,
        description="Maximum direct retry attempts per failed session.",
    )
    direct_retry_timeout_seconds: int = Field(
        default=180,
        ge=1,
        description="Timeout for one direct retry inference attempt.",
    )
    cleanup_enabled: bool = Field(
        default=True,
        description="Enable delayed cleanup of uploaded Gemini batch input/output artifacts.",
    )
    cleanup_scan_limit: int = Field(
        default=200,
        ge=1,
        description="Maximum provider batches scanned for cleanup per runner cycle.",
    )
    cleanup_success_retention_seconds: int = Field(
        default=3600,
        ge=0,
        description="How long to retain provider batch artifacts after success.",
    )
    cleanup_failure_retention_seconds: int = Field(
        default=86400,
        ge=0,
        description="How long to retain provider batch artifacts after partial/failed completion.",
    )
    cleanup_retry_delay_seconds: int = Field(
        default=300,
        ge=1,
        description="Delay before retrying failed artifact cleanup attempts.",
    )
    result_file_ttl_seconds: int = Field(
        default=604800,
        ge=0,
        description="Retention for exported final result files in GCS; 0 disables application-side result cleanup.",
    )
    result_file_cleanup_scan_limit: int = Field(
        default=200,
        ge=1,
        description="Maximum queued result-file cleanup records scanned per runner cycle.",
    )
    result_file_cleanup_retry_delay_seconds: int = Field(
        default=300,
        ge=1,
        description="Delay before retrying failed result-file cleanup operations.",
    )
    lease_duration_seconds: int = Field(
        default=180,
        ge=10,
        description="Lease duration for one runner to own an async job during processing.",
    )
    max_submit_attempts: int = Field(
        default=3,
        ge=1,
        description="Maximum Gemini batch submission attempts for one async job.",
    )
    recover_missing_batch_name: bool = Field(
        default=True,
        description="Reset jobs to ACCEPTED when running/submitted rows miss google_batch_job_name.",
    )
    missing_batch_name_grace_seconds: int = Field(
        default=120,
        ge=0,
        description="Grace window before treating submitted/running rows without google_batch_job_name as broken.",
    )
    job_summary_ttl_seconds: int = Field(
        default=21600,
        ge=0,
        description="Redis TTL for async job summaries; 0 means no expiration.",
    )
    result_max_file_size_mb: int = Field(
        default=64,
        ge=1,
        description="Maximum estimated uncompressed JSONL size per exported result file part.",
    )
    result_signed_url_ttl_seconds: int = Field(
        default=86400,
        ge=60,
        description="Signed URL TTL for callback download links.",
    )
    callback_scan_limit: int = Field(
        default=200,
        ge=1,
        description="Maximum provider batches scanned for callback dispatch per cycle.",
    )
    callback_concurrency: int = Field(
        default=10,
        ge=1,
        description="Maximum concurrent callback deliveries.",
    )
    callback_request_interval_seconds: int = Field(
        default=60,
        ge=0,
        description=(
            "Global minimum interval between callback HTTP requests in one service "
            "process; 0 disables interval limiting."
        ),
    )
    callback_timeout_seconds: int = Field(
        default=15,
        ge=1,
        description="HTTP timeout for one callback delivery attempt.",
    )
    callback_max_attempts: int = Field(
        default=8,
        ge=1,
        description="Maximum callback delivery attempts before giving up.",
    )
    callback_retry_base_seconds: int = Field(
        default=30,
        ge=1,
        description="Base backoff seconds for callback retry scheduling.",
    )
    callback_retry_max_seconds: int = Field(
        default=3600,
        ge=1,
        description="Maximum callback retry delay.",
    )
    callback_hmac_secret: str = Field(
        default="",
        description="Optional HMAC secret for callback signature header generation.",
    )
    callback_bypass_proxy: bool = Field(
        default=True,
        description="When true, callback HTTP delivery bypasses system HTTP/HTTPS proxy settings.",
    )
    callback_url: str = Field(
        default="",
        description="Deprecated global callback URL override; callback_url should be provided per request.",
    )
    state_retention_success_seconds: int = Field(
        default=259200,
        ge=0,
        description="Retention for Redis async state after successful callback completion.",
    )
    state_retention_failure_seconds: int = Field(
        default=604800,
        ge=0,
        description="Retention for Redis async state after failed/partial callback flow.",
    )
    state_retention_callback_failed_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description=(
            "Optional retention for Redis async state when callback delivery ends in FAILED. "
            "When unset, falls back to state_retention_failure_seconds."
        ),
    )
    state_hard_ttl_seconds: int = Field(
        default=1209600,
        ge=0,
        description="Hard TTL fallback for Redis async state keys; 0 disables hard TTL.",
    )
    purge_scan_limit: int = Field(
        default=100,
        ge=1,
        description="Maximum provider batches scanned for Redis state purge per cycle.",
    )
    purge_retry_delay_seconds: int = Field(
        default=300,
        ge=1,
        description="Delay before retrying failed Redis state purge operations.",
    )
    http_api_version: str = Field(
        default="v1",
        description="Google GenAI API version for batch operations.",
    )
    http_media_staging_prefix: str = Field(
        default="aihelper/gemini-batch/http-media",
        description="GCS prefix for HTTP/HTTPS multimodal assets converted to gs:// URIs.",
    )
    http_media_download_timeout_seconds: int = Field(
        default=30,
        ge=1,
        description="Timeout for downloading one HTTP/HTTPS multimodal asset.",
    )
    http_media_max_image_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=1,
        description="Maximum bytes for one staged HTTP image.",
    )
    http_media_max_video_bytes: int = Field(
        default=200 * 1024 * 1024,
        ge=1,
        description="Maximum bytes for one staged HTTP video.",
    )
    http_media_max_audio_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1,
        description="Maximum bytes for one staged HTTP audio file.",
    )
    http_media_max_pdf_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1,
        description="Maximum bytes for one staged HTTP PDF file.",
    )


class LimitSettings(BaseSettings):
    max_concurrency: int = Field(
        default=25, ge=1, description="Semaphore limit for concurrent LLM calls."
    )
    max_batch_size: int = Field(
        default=10, ge=1, description="Max conversations per request."
    )
    batch_timeout_seconds: int = Field(
        default=600, ge=1, description="HTTP wait timeout for worker results."
    )
    num_workers: int = Field(
        default=10, ge=1, description="Worker count for batch processing."
    )
    max_queue_size: int = Field(
        default=3000, ge=1, description="Max queued batch jobs."
    )
    conversation_id_cache_ttl_seconds: int = Field(
        default=3600, ge=1, description="TTL for conversation ID retry tracking cache."
    )
    conversation_retry_limit: int = Field(
        default=3, ge=1, description="Max retries allowed per conversation ID."
    )
    sync_llm_retry_attempts: int = Field(
        default=5,
        ge=1,
        description="Maximum retry attempts for one synchronous LLM batch invocation.",
    )
    sync_llm_retry_min_seconds: int = Field(
        default=2,
        ge=1,
        description="Minimum exponential-backoff wait seconds for synchronous LLM retries.",
    )
    sync_llm_retry_max_seconds: int = Field(
        default=60,
        ge=1,
        description="Maximum exponential-backoff wait seconds for synchronous LLM retries.",
    )
    sync_input_length_limit: int = Field(
        default=30720,
        ge=1,
        description="Maximum combined system+conversation input length for synchronous OpenAI-style requests.",
    )


class LanguageSettings(BaseSettings):
    language_type: Dict[str, str] = Field(
        default_factory=lambda: {
            "zh": "简体中文",
            "en": "英文",
            "en-US": "英文",
            "zh-CN": "简体中文",
            "ja-JP": "日语",
            "th-TH": "泰语",
            "my-MM": "缅语",
            "vi-VN": "越语",
            "id-ID": "印尼语",
            "ms-MY": "马来语",
            "km-KH": "高棉语",
            "lo-LA": "老挝语",
            "tl-PH": "菲律宾语",
        },
        description="Supported target languages for translation.",
    )


class RedisSettings(BaseSettings):
    host: str = Field(
        # default="192.168.1.31",
        default="43.134.84.141",
        # default="150.109.24.184",
        description="Redis server host.",
    )
    port: int = Field(
        # default=63790,
        default=6379,
        description="Redis server port.",
    )
    db: int = Field(default=0, ge=0, description="Redis database index.")
    password: Optional[str] = Field(
        default=None, description="Plaintext Redis password."
    )
    password_b64: Optional[str] = Field(
        # default=None,
        # default="Nm5qaW4xR2Q3QUFUVmdOekt3ZDU=",
        default="UDJxeHRWNU1vS3JhTlhIdlpGSFI=",
        description="Base64-encoded Redis password.",
    )
    connect_timeout: float = Field(
        default=2.0,
        gt=0,
        description="Socket connect timeout in seconds.",
    )
    socket_timeout: float = Field(
        default=2.0,
        gt=0,
        description="Socket read/write timeout in seconds.",
    )
    retry_on_timeout: bool = Field(
        default=True,
        description="Whether Redis commands should retry when socket timeout happens.",
    )
    health_check_interval: int = Field(
        default=30,
        ge=0,
        description="Seconds between connection health checks.",
    )
    socket_keepalive: bool = Field(
        default=True,
        description="Enable TCP keepalive on Redis sockets.",
    )
    max_connections: int = Field(
        default=200,
        ge=1,
        description="Max Redis connections in the client connection pool.",
    )


class L1CollectionSettings(BaseSettings):
    max_batch_size: int = Field(
        default=20,
        ge=1,
        description="Maximum conversations allowed in one synchronous ClassifyIssues request.",
    )
    sync_max_inflight_requests: int = Field(
        default=2,
        ge=1,
        description="Maximum in-flight synchronous ClassifyIssues requests allowed per process.",
    )
    sync_acquire_timeout_ms: int = Field(
        default=1500,
        ge=1,
        description="Max milliseconds to wait for a synchronous ClassifyIssues concurrency slot.",
    )
    overload_strategy: str = Field(
        default="reject",
        description="Overload strategy for synchronous ClassifyIssues: reject or keyword_fallback.",
    )
    llm_batch_max_concurrency: int = Field(
        default=8,
        ge=1,
        description="Max provider-side concurrency for one batched LLM invocation in ClassifyIssues.",
    )
    single_fallback_concurrency: int = Field(
        default=4,
        ge=1,
        description="Concurrency for single-conversation fallback LLM calls after batch failure.",
    )
    sync_retry_num_workers: int = Field(
        default=4,
        ge=1,
        description="Worker count for synchronous issue-classification retry queue.",
    )
    sync_retry_queue_size: int = Field(
        default=1000,
        ge=1,
        description="Maximum queued synchronous issue-classification retry jobs.",
    )
    sync_retry_timeout_seconds: int = Field(
        default=120,
        ge=1,
        description="Max seconds to wait for one synchronous issue-classification retry job result.",
    )
    required_rule_id: str = Field(
        default="L1_COLLECTION",
        description="Required rule_id for ClassifyIssues RPC.",
    )
    max_issues_per_conversation: int = Field(
        default=5,
        ge=1,
        description="Max number of L1 issues returned per conversation.",
    )
    output_dir: str = Field(
        default="l1_labels",
        description="Root directory for L1 label collection JSONL files.",
    )
    taxonomy_version: str = Field(
        default="l1-taxonomy-v1",
        description="L1 taxonomy version stamped into output records.",
    )
    prompt_version: str = Field(
        default="l1-prompt-v1",
        description="Prompt or strategy version stamped into output records.",
    )
    model_version_default: str = Field(
        default="l1-keyword-rules-v1",
        description="Default model_version value when request.model_name is empty.",
    )
    dedupe_ttl_seconds: int = Field(
        default=60 * 60 * 24 * 90,
        ge=1,
        description="In-memory dedupe TTL for (conversation_id, taxonomy_version, prompt_version).",
    )
    prompt_cache_enabled: bool = Field(
        default=True,
        description="Whether ClassifyIssues should use prompt cache for Gemini models.",
    )
    prompt_cache_id: str = Field(
        default="l1-label-collection-v1",
        description="Dedicated prompt cache identifier for ClassifyIssues.",
    )
    prompt_cache_force_recreate: bool = Field(
        default=False,
        description="Force recreate prompt cache on next model initialization.",
    )


class L3AggregationSettings(BaseSettings):
    enabled: bool = Field(
        default=True,
        description="Enable offline L3 keyword aggregation job.",
    )
    input_dir: str = Field(
        default="l1_labels",
        description="Root directory of L1 JSONL records to aggregate.",
    )
    output_dir: str = Field(
        default="l3_cloud",
        description="Root directory for L3 aggregation outputs.",
    )
    window_hours: int = Field(
        default=24,
        ge=0,
        description="Lookback window in hours; 0 means aggregate all available files.",
    )
    embedding_model: str = Field(
        default="gemini-embedding-001",
        description="Embedding model used for L3 clustering (Gemini by default).",
    )
    embedding_batch_size: int = Field(
        default=256,
        ge=1,
        description="Batch size for embedding API calls.",
    )
    similarity_threshold: float = Field(
        default=0.86,
        ge=0.0,
        le=1.0,
        description="Legacy single-threshold fallback for clustering term pairs.",
    )
    similarity_threshold_high: float = Field(
        default=0.90,
        ge=0.0,
        le=1.0,
        description="High cosine threshold: pairs above this value are strong merge candidates.",
    )
    similarity_threshold_low: float = Field(
        default=0.84,
        ge=0.0,
        le=1.0,
        description="Low cosine threshold: pairs in [low, high) are sent to pending candidates.",
    )
    bridge_suppression_enabled: bool = Field(
        default=True,
        description="Enable conservative bridge suppression when merging strong-edge components.",
    )
    min_cluster_size: int = Field(
        default=2,
        ge=1,
        description="Minimum alias count required to promote a cluster into draft.",
    )
    kb_path: str = Field(
        default="app/config/l3_kb.json",
        description="Path to L3 keyword knowledge-base JSON.",
    )
    pending_confidence_threshold: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Clusters below this confidence are written into pending_terms.",
    )


class Settings(BaseSettings):
    config: ConfigSettings = ConfigSettings()
    cache: CacheSettings = CacheSettings()
    models: ModelSettings = ModelSettings()
    limits: LimitSettings = LimitSettings()
    languages: LanguageSettings = LanguageSettings()
    redis: RedisSettings = RedisSettings()
    gemini_batch: GeminiBatchSettings = GeminiBatchSettings()
    l1_collection: L1CollectionSettings = L1CollectionSettings()
    l3_agg: L3AggregationSettings = L3AggregationSettings()


    class Config:
        env_nested_delimiter = "__"
        env_prefix = "QA_"
        env_file = _resolve_env_file()
        env_file_encoding = "utf-8"
