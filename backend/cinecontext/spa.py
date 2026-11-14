from pathlib import Path, PurePosixPath

from starlette.exceptions import HTTPException
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class SpaStaticFiles(StaticFiles):
    """Serve built assets and fall back to index.html for client-side routes."""

    def __init__(self, directory: str | Path):
        super().__init__(directory=directory, html=True)
        self.index_path = Path(directory) / "index.html"

    async def get_response(self, path: str, scope: Scope):
        try:
            response = await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or not self._is_client_route(path, scope):
                raise
            return FileResponse(self.index_path, media_type="text/html")
        if response.status_code == 404 and self._is_client_route(path, scope):
            return FileResponse(self.index_path, media_type="text/html")
        return response

    @staticmethod
    def _is_client_route(path: str, scope: Scope) -> bool:
        accepts_html = b"text/html" in dict(scope.get("headers", [])).get(b"accept", b"")
        return accepts_html and not PurePosixPath(path).suffix
