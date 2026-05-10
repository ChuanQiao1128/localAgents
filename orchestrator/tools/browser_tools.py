from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserScreenshot:
    url: str
    path: str


class BrowserTools:
    def screenshot(self, url: str, output_path: str) -> BrowserScreenshot:
        if not url.startswith(("http://", "https://", "file://")):
            raise ValueError("Browser screenshot URL must be http, https, or file.")
        raise NotImplementedError("Browser screenshot automation is reserved for the UI/reference phase.")

