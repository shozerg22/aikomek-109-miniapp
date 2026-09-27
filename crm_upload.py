"""Upload attachments through the existing CRM web form."""
import os
import re
import io
import hashlib
import mimetypes
from html import unescape

import aiohttp


class UploadError(RuntimeError):
    pass


def csrf_token(html):
    match = re.search(r'name=[\"\x27]csrfmiddlewaretoken[\"\x27][^>]*value=[\"\x27]([^\"\x27]+)', html)
    if not match:
        raise UploadError("CRM login form is unavailable")
    return unescape(match.group(1))


class CRMUploader:
    def __init__(self):
        self.base = os.getenv("CRM_BASE_URL", "").rstrip("/")
        self.username = os.getenv("CRM_WEB_USERNAME", "")
        self.password = os.getenv("CRM_WEB_PASSWORD", "")

    async def login(self, session):
        if not all((self.base, self.username, self.password)):
            raise UploadError("CRM web credentials are not configured")
        url = self.base + "/login/"
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise UploadError("CRM login page unavailable")
            token = csrf_token(await response.text())
        async with session.post(url, data={"username": self.username, "password": self.password,
                                           "csrfmiddlewaretoken": token},
                                headers={"Referer": url}, allow_redirects=False) as response:
            if response.status not in (302, 303) or "/login" in response.headers.get("Location", ""):
                raise UploadError("CRM rejected web login")

    async def upload(self, appeal_id, content, filename, content_type):
        if not content or len(content) > 10 * 1024 * 1024:
            raise UploadError("Attachment must be between 1 byte and 10 MB")
        if content_type not in ("image/jpeg", "image/png", "image/webp", "video/mp4"):
            raise UploadError("Unsupported attachment format")
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True),
                                            timeout=aiohttp.ClientTimeout(total=45)) as session:
                await self.login(session)
                detail = self.base + f"/appeals/{int(appeal_id)}/"
                async with session.get(detail, allow_redirects=False) as response:
                    if response.status != 200:
                        raise UploadError("CRM appeal is not accessible")
                    html = await response.text()
                    if filename in html:
                        return
                    token = csrf_token(html)
                data = aiohttp.FormData()
                data.add_field("csrfmiddlewaretoken", token)
                data.add_field("file", content, filename=filename, content_type=content_type)
                async with session.post(detail + "upload/", data=data,
                                        headers={"Referer": detail}, allow_redirects=False) as response:
                    if response.status not in (302, 303):
                        raise UploadError("CRM did not accept attachment")
                # A redirect also occurs on validation failure; verify the saved filename.
                async with session.get(detail, allow_redirects=False) as response:
                    html = await response.text()
                    if response.status != 200 or filename not in html:
                        raise UploadError("Attachment saving was not confirmed by CRM")
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise UploadError("CRM attachment connection failed") from exc

    async def upload_telegram(self, bot, appeal_id, file_id):
        try:
            info = await bot.get_file(file_id)
            if (info.file_size or 0) > 10 * 1024 * 1024:
                raise UploadError("Attachment exceeds 10 MB")
            suffix = os.path.splitext(info.file_path or '')[1].lower()
            mime = mimetypes.guess_type('file' + suffix)[0] or 'application/octet-stream'
            name = 'telegram-' + hashlib.sha256(info.file_unique_id.encode()).hexdigest()[:20] + suffix
            content = io.BytesIO()
            await bot.download_file(info.file_path, destination=content)
            await self.upload(appeal_id, content.getvalue(), name, mime)
        except UploadError:
            raise
        except Exception as exc:
            raise UploadError("Could not download Telegram attachment") from exc
