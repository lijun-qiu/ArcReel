"""Unit tests for VersionManager.delete_version."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.api_errors import ConflictError, NotFoundError
from lib.version_manager import VersionManager

pytestmark = pytest.mark.unit


def _write_image(path: Path, marker: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(marker)


def test_delete_version_removes_non_current_snapshot(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    current = project / "characters" / "Alice.png"
    v1 = project / "seed_v1.png"
    v2 = project / "seed_v2.png"
    _write_image(v1, b"v1")
    _write_image(v2, b"v2")
    _write_image(current, b"v2")

    vm = VersionManager(project)
    first = vm.add_version("characters", "Alice", "first", source_file=v1)
    second = vm.add_version("characters", "Alice", "second", source_file=v2)
    assert first == 1
    assert second == 2

    before = vm.get_versions("characters", "Alice")
    old = next(item for item in before["versions"] if item["version"] == 1)
    snapshot = project / old["file"]
    assert snapshot.is_file()

    result = vm.delete_version("characters", "Alice", 1)
    assert result == {"deleted_version": 1, "current_version": 2, "cleared_current": False}

    after = vm.get_versions("characters", "Alice")
    assert after["current_version"] == 2
    assert [item["version"] for item in after["versions"]] == [2]
    assert not snapshot.exists()
    assert current.read_bytes() == b"v2"


def test_delete_version_refuses_current_selection(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    seed = project / "seed.png"
    _write_image(seed, b"story")
    vm = VersionManager(project)
    version = vm.add_version("storyboards", "E1S01", "shot", source_file=seed)

    with pytest.raises(ConflictError) as exc:
        vm.delete_version("storyboards", "E1S01", version)
    assert exc.value.key == "cannot_delete_current_version"


def test_delete_version_clears_sole_current_typed_media(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    seed = project / "seed.mp4"
    seed.write_bytes(b"video-bytes")
    canonical = project / "reference_videos" / "E1U01.mp4"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"video-bytes")
    vm = VersionManager(project)
    version = vm.add_version("reference_videos", "E1U01", "clip", source_file=seed)
    assert canonical.is_file()
    history = vm.get_versions("reference_videos", "E1U01")
    snapshot = project / history["versions"][0]["file"]
    assert snapshot.is_file()

    result = vm.delete_version("reference_videos", "E1U01", version)
    assert result == {"deleted_version": version, "current_version": 0, "cleared_current": True}
    after = vm.get_versions("reference_videos", "E1U01")
    assert after["current_version"] == 0
    assert after["versions"] == []
    assert not canonical.exists()
    assert not snapshot.exists()


def test_delete_version_refuses_current_when_history_remains(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    seed1 = project / "seed1.mp4"
    seed2 = project / "seed2.mp4"
    seed1.write_bytes(b"v1")
    seed2.write_bytes(b"v2")
    canonical = project / "reference_videos" / "E1U01.mp4"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"v2")
    vm = VersionManager(project)
    first = vm.add_version("reference_videos", "E1U01", "one", source_file=seed1)
    second = vm.add_version("reference_videos", "E1U01", "two", source_file=seed2)
    assert first == 1
    assert second == 2

    with pytest.raises(ConflictError) as exc:
        vm.delete_version("reference_videos", "E1U01", second)
    assert exc.value.key == "cannot_delete_current_version"
    assert canonical.read_bytes() == b"v2"


def test_delete_version_missing_record(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    seed = project / "seed.png"
    _write_image(seed, b"prop")
    vm = VersionManager(project)
    vm.add_version("props", "玉佩", "prop", source_file=seed)
    vm.add_version("props", "玉佩", "prop2", source_file=seed)

    with pytest.raises(NotFoundError) as exc:
        vm.delete_version("props", "玉佩", 99)
    assert exc.value.key == "version_not_found"
