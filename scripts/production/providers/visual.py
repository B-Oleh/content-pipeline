"""Visual asset provider boundary (Pexels, Pixabay).

Thin and replaceable per CLAUDE.md "Provider abstraction": asset_acquisition.py
only calls VisualAssetProvider, never a specific API's response shape
directly. Both providers here use their official, documented, free-tier
APIs -- no scraping, no undocumented endpoints.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from scripts.utils.logging_utils import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0


def _raise_for_status(response: requests.Response, provider_name: str) -> None:
    """Like response.raise_for_status(), but never leaks the request URL.

    Pixabay's API key travels as a `key=` query parameter (there is no
    header-based auth option in their public API), so requests' default
    HTTPError -- which includes the full request URL -- would print the API
    key straight into logs on any failure. Never print secret values (see
    CLAUDE.md "Logging requirements" and this task's preflight rule): report
    only the status code and response body, never the URL.
    """
    if response.status_code >= 400:
        raise requests.exceptions.HTTPError(
            f"{provider_name} API request failed with HTTP {response.status_code}: {response.text[:300]}"
        )


@dataclass
class AssetResult:
    """One candidate visual asset found by a provider search."""

    provider: str  # "pexels" | "pixabay"
    page_url: str  # human-viewable page on the provider's site (for attribution)
    download_url: str  # direct file URL
    width: int
    height: int
    is_video: bool
    duration_seconds: Optional[float]
    attribution: str  # e.g. "Video by Jane Doe on Pexels"
    # A small static preview image URL, when the provider's search response
    # includes one -- used by vision_validation.py to fetch a cheap
    # thumbnail for Gemini Vision evaluation instead of the full video/photo
    # file (see that module's docstring and the task's explicit "download or
    # use small thumbnails/previews only for evaluation" requirement).
    thumbnail_url: Optional[str] = None


class VisualAssetProvider(ABC):
    """A source of stock video/photo assets."""

    name: str

    @abstractmethod
    def search_videos(self, query: str, per_page: int = 5) -> list[AssetResult]:
        raise NotImplementedError

    @abstractmethod
    def search_photos(self, query: str, per_page: int = 5) -> list[AssetResult]:
        """Fallback when no usable video is found for a scene."""
        raise NotImplementedError

    def download(self, asset: AssetResult, dest_path: Path) -> None:
        """Stream-download asset.download_url to dest_path. Shared by both providers."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(asset.download_url, stream=True, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            _raise_for_status(response, self.name)
            with open(dest_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    if chunk:
                        handle.write(chunk)


class PexelsProvider(VisualAssetProvider):
    name = "pexels"
    VIDEOS_URL = "https://api.pexels.com/videos/search"
    PHOTOS_URL = "https://api.pexels.com/v1/search"

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._api_key}

    def search_videos(self, query: str, per_page: int = 5) -> list[AssetResult]:
        response = requests.get(
            self.VIDEOS_URL,
            headers=self._headers(),
            params={"query": query, "orientation": "portrait", "per_page": per_page},
            timeout=self._timeout,
        )
        _raise_for_status(response, self.name)
        data = response.json()
        results = []
        for video in data.get("videos", []):
            video_files = sorted(
                (f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")),
                key=lambda f: f["width"],
            )
            preferred = next((f for f in video_files if f["width"] >= 720), None) or (video_files[-1] if video_files else None)
            if not preferred:
                continue
            photographer = video.get("user", {}).get("name", "a Pexels contributor")
            results.append(
                AssetResult(
                    provider=self.name,
                    page_url=video.get("url", ""),
                    download_url=preferred["link"],
                    width=preferred["width"],
                    height=preferred["height"],
                    is_video=True,
                    duration_seconds=video.get("duration"),
                    attribution=f"Video by {photographer} on Pexels",
                    thumbnail_url=video.get("image"),
                )
            )
        return results

    def search_photos(self, query: str, per_page: int = 5) -> list[AssetResult]:
        response = requests.get(
            self.PHOTOS_URL,
            headers=self._headers(),
            params={"query": query, "orientation": "portrait", "per_page": per_page},
            timeout=self._timeout,
        )
        _raise_for_status(response, self.name)
        data = response.json()
        results = []
        for photo in data.get("photos", []):
            src = photo.get("src", {})
            download_url = src.get("large2x") or src.get("large") or src.get("original")
            if not download_url:
                continue
            photographer = photo.get("photographer", "a Pexels contributor")
            results.append(
                AssetResult(
                    provider=self.name,
                    page_url=photo.get("url", ""),
                    download_url=download_url,
                    width=photo.get("width", 0),
                    height=photo.get("height", 0),
                    is_video=False,
                    duration_seconds=None,
                    attribution=f"Photo by {photographer} on Pexels",
                    thumbnail_url=src.get("small") or src.get("medium") or download_url,
                )
            )
        return results


class PixabayProvider(VisualAssetProvider):
    name = "pixabay"
    VIDEOS_URL = "https://pixabay.com/api/videos/"
    PHOTOS_URL = "https://pixabay.com/api/"

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def search_videos(self, query: str, per_page: int = 5) -> list[AssetResult]:
        response = requests.get(
            self.VIDEOS_URL,
            params={"key": self._api_key, "q": query, "per_page": max(per_page, 3)},
            timeout=self._timeout,
        )
        _raise_for_status(response, self.name)
        data = response.json()
        results = []
        for hit in data.get("hits", []):
            videos = hit.get("videos", {})
            preferred = videos.get("medium") or videos.get("large") or videos.get("small") or videos.get("tiny")
            if not preferred or not preferred.get("url"):
                continue
            user = hit.get("user", "a Pixabay contributor")
            results.append(
                AssetResult(
                    provider=self.name,
                    page_url=hit.get("pageURL", ""),
                    download_url=preferred["url"],
                    width=preferred.get("width", 0),
                    height=preferred.get("height", 0),
                    is_video=True,
                    duration_seconds=hit.get("duration"),
                    attribution=f"Video by {user} on Pixabay",
                    thumbnail_url=preferred.get("thumbnail"),
                )
            )
        return results

    def search_photos(self, query: str, per_page: int = 5) -> list[AssetResult]:
        response = requests.get(
            self.PHOTOS_URL,
            params={"key": self._api_key, "q": query, "image_type": "photo", "per_page": max(per_page, 3)},
            timeout=self._timeout,
        )
        _raise_for_status(response, self.name)
        data = response.json()
        results = []
        for hit in data.get("hits", []):
            download_url = hit.get("largeImageURL") or hit.get("webformatURL")
            if not download_url:
                continue
            user = hit.get("user", "a Pixabay contributor")
            results.append(
                AssetResult(
                    provider=self.name,
                    page_url=hit.get("pageURL", ""),
                    download_url=download_url,
                    width=hit.get("imageWidth", 0),
                    height=hit.get("imageHeight", 0),
                    is_video=False,
                    duration_seconds=None,
                    attribution=f"Photo by {user} on Pixabay",
                    thumbnail_url=hit.get("previewURL"),
                )
            )
        return results
