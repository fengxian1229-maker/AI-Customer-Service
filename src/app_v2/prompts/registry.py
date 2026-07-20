from dataclasses import dataclass
from importlib.resources import files

from app_v2.prompts.versions import PROMPT_VERSIONS


@dataclass(frozen=True)
class PromptAsset:
    name: str
    version: str
    text: str


class PromptRegistry:
    def load(self, name: str) -> PromptAsset:
        filename, version = PROMPT_VERSIONS[name]
        text = files("app_v2.prompts").joinpath(filename).read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"Prompt file is empty: {filename}")
        return PromptAsset(name=name, version=version, text=text)
