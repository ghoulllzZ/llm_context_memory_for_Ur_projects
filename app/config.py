from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    root_dir: Path
    data_dir: Path
    raw_dir: Path
    asset_dir: Path
    import_dir: Path
    host: str
    port: int
    run_jobs_inline: bool
    llm_api_url: str | None
    llm_api_key: str | None
    llm_text_model: str | None
    llm_vision_model: str | None

    @classmethod
    def from_env(cls) -> "AppConfig":
        root_dir = Path(__file__).resolve().parent.parent
        data_dir = Path(os.environ.get("KB_DATA_DIR", root_dir / "data"))
        raw_dir = data_dir / "raw"
        asset_dir = data_dir / "assets"
        import_dir = data_dir / "imports"
        return cls(
            root_dir=root_dir,
            data_dir=data_dir,
            raw_dir=raw_dir,
            asset_dir=asset_dir,
            import_dir=import_dir,
            host=os.environ.get("KB_HOST", "127.0.0.1"),
            port=int(os.environ.get("KB_PORT", "8765")),
            run_jobs_inline=os.environ.get("KB_INLINE_JOBS", "0") == "1",
            llm_api_url=os.environ.get("LLM_API_URL"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_text_model=os.environ.get("LLM_TEXT_MODEL"),
            llm_vision_model=os.environ.get("LLM_VISION_MODEL"),
        )

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.raw_dir, self.asset_dir, self.import_dir):
            path.mkdir(parents=True, exist_ok=True)
