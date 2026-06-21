"""Regression tests for Ring Stash HTTP authentication."""
from __future__ import annotations

import ast
import asyncio
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


_ROOT = Path(__file__).parent.parent
_INIT_PATH = _ROOT / "custom_components" / "ring_stash" / "__init__.py"
_FRONTEND_PATH = (
    _ROOT
    / "custom_components"
    / "ring_stash"
    / "frontend"
    / "ring-stash-viewer.js"
)


class _HTTPForbidden(Exception):
    status = 403


class _Response:
    def __init__(self, *, status: int = 200, text: str | None = None) -> None:
        self.status = status
        self.text = text


class _FileResponse(_Response):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path


class _DummyHass:
    def __init__(self, download_path: Path) -> None:
        self.data = {"ring_stash": {"_download_path": download_path}}
        self.executor_calls = 0

    async def async_add_executor_job(self, func, *args):
        self.executor_calls += 1
        return func(*args)


class _DummyUser:
    def __init__(self, is_admin: bool) -> None:
        self.is_admin = is_admin


class _DummyRequest(dict):
    def __init__(self, user: _DummyUser, hass: _DummyHass) -> None:
        super().__init__(hass_user=user)
        self.app = {"hass": hass}


def _load_integration_module():
    """Load the media view from __init__.py with minimal runtime stubs."""
    source_tree = ast.parse(_INIT_PATH.read_text(encoding="utf-8"))
    selected_names = {
        "_get_domain_data",
        "_get_download_path",
        "_is_safe_filename",
        "RingClipMediaView",
    }
    selected = [
        node
        for node in source_tree.body
        if (
            isinstance(node, (ast.FunctionDef, ast.ClassDef))
            and node.name in selected_names
        )
    ]
    module_tree = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module_tree)
    namespace = {
        "DEFAULT_DOWNLOAD_PATH": "/media/ring_clips",
        "DOMAIN": "ring_stash",
        "HomeAssistantView": object,
        "HTTPForbidden": _HTTPForbidden,
        "KEY_HASS_USER": "hass_user",
        "Path": Path,
    }
    exec(compile(module_tree, str(_INIT_PATH), "exec"), namespace)
    return types.SimpleNamespace(RingClipMediaView=namespace["RingClipMediaView"])


class TestHttpSecurity(unittest.TestCase):
    """Ensure private clip routes cannot regress to unauthenticated access."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.init_source = _INIT_PATH.read_text(encoding="utf-8")
        cls.init_tree = ast.parse(cls.init_source)
        cls.frontend_source = _FRONTEND_PATH.read_text(encoding="utf-8")
        cls.integration = _load_integration_module()

    def test_all_home_assistant_views_require_authentication(self) -> None:
        view_classes = [
            node
            for node in self.init_tree.body
            if isinstance(node, ast.ClassDef)
            and any(
                isinstance(base, ast.Name) and base.id == "HomeAssistantView"
                for base in node.bases
            )
        ]

        self.assertGreater(len(view_classes), 0)
        for view_class in view_classes:
            requires_auth = next(
                (
                    statement.value
                    for statement in view_class.body
                    if isinstance(statement, ast.Assign)
                    and any(
                        isinstance(target, ast.Name)
                        and target.id == "requires_auth"
                        for target in statement.targets
                    )
                ),
                None,
            )
            self.assertIsNotNone(
                requires_auth,
                f"{view_class.name} must declare requires_auth explicitly",
            )
            self.assertIsInstance(requires_auth, ast.Constant)
            self.assertIs(
                requires_auth.value,
                True,
                f"{view_class.name} must require Home Assistant authentication",
            )

    def test_sidebar_panel_is_admin_only(self) -> None:
        self.assertIn("require_admin=True", self.init_source)
        self.assertNotIn("require_admin=False", self.init_source)

    def test_frontend_fetches_clip_media_with_authentication(self) -> None:
        self.assertIn("this._hass.fetchWithAuth(", self.frontend_source)
        self.assertNotIn("video.src = `${MEDIA_BASE}/", self.frontend_source)
        self.assertNotIn('data-src="${MEDIA_BASE}/', self.frontend_source)

    @staticmethod
    async def _dispatch_media_request(
        *,
        authenticated: bool,
        is_admin: bool,
        download_path: Path,
        filename: str,
    ) -> tuple[int, _DummyHass]:
        view = TestHttpSecurity.integration.RingClipMediaView()
        hass = _DummyHass(download_path)
        if view.requires_auth and not authenticated:
            return 401, hass

        request = _DummyRequest(_DummyUser(is_admin), hass)
        aiohttp_module = types.ModuleType("aiohttp")
        aiohttp_web = types.ModuleType("aiohttp.web")
        aiohttp_web.FileResponse = _FileResponse
        aiohttp_web.Response = _Response
        aiohttp_module.web = aiohttp_web
        with patch.dict(
            "sys.modules",
            {
                "aiohttp": aiohttp_module,
                "aiohttp.web": aiohttp_web,
            },
        ):
            try:
                response = await view.get(request, filename)
            except _HTTPForbidden as error:
                return error.status, hass
        return response.status, hass

    def test_unauthenticated_media_request_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (status, hass) = asyncio.run(
                self._dispatch_media_request(
                    authenticated=False,
                    is_admin=False,
                    download_path=Path(tmpdir),
                    filename="clip.mp4",
                )
            )
        self.assertEqual(status, 401)
        self.assertEqual(hass.executor_calls, 0)

    def test_authenticated_non_admin_media_request_is_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            clip_path = Path(tmpdir) / "clip.mp4"
            clip_path.write_bytes(b"private clip")
            (status, hass) = asyncio.run(
                self._dispatch_media_request(
                    authenticated=True,
                    is_admin=False,
                    download_path=Path(tmpdir),
                    filename=clip_path.name,
                )
            )
        self.assertEqual(status, 403)
        self.assertEqual(hass.executor_calls, 0)

    def test_authenticated_admin_media_request_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            clip_path = Path(tmpdir) / "clip.mp4"
            clip_path.write_bytes(b"private clip")
            (status, hass) = asyncio.run(
                self._dispatch_media_request(
                    authenticated=True,
                    is_admin=True,
                    download_path=Path(tmpdir),
                    filename=clip_path.name,
                )
            )
        self.assertEqual(status, 200)
        self.assertEqual(hass.executor_calls, 1)

    def test_admin_request_rejects_unsafe_filenames_before_disk_access(self) -> None:
        unsafe_names = (".", "..", "../clip.mp4", r"..\clip.mp4")
        with tempfile.TemporaryDirectory() as tmpdir:
            for filename in unsafe_names:
                with self.subTest(filename=filename):
                    (status, hass) = asyncio.run(
                        self._dispatch_media_request(
                            authenticated=True,
                            is_admin=True,
                            download_path=Path(tmpdir),
                            filename=filename,
                        )
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(hass.executor_calls, 0)

    def test_admin_request_returns_not_found_for_missing_clip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (status, hass) = asyncio.run(
                self._dispatch_media_request(
                    authenticated=True,
                    is_admin=True,
                    download_path=Path(tmpdir),
                    filename="missing.mp4",
                )
            )
        self.assertEqual(status, 404)
        self.assertEqual(hass.executor_calls, 1)

    def test_admin_request_does_not_serve_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir) / "not-a-clip.mp4"
            directory.mkdir()
            (status, hass) = asyncio.run(
                self._dispatch_media_request(
                    authenticated=True,
                    is_admin=True,
                    download_path=Path(tmpdir),
                    filename=directory.name,
                )
            )
        self.assertEqual(status, 404)
        self.assertEqual(hass.executor_calls, 1)


if __name__ == "__main__":
    unittest.main()
