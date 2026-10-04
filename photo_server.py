"""Authenticated, upload-only staging endpoint for the Telegram Mini App."""
import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
from pathlib import Path

from aiohttp import web
from PIL import Image, UnidentifiedImageError

MAX_FILE = 10 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000


def ticket(user_id, bot_token):
    data = f'{int(user_id)}:{int(time.time()) + 86400}'
    signature = hmac.new(bot_token.encode(), ('photo-upload:' + data).encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f'{data}:{signature}'.encode()).decode()


def ticket_user(value, bot_token):
    try:
        user, expiry, signature = base64.urlsafe_b64decode(value).decode().split(':')
        expected = hmac.new(bot_token.encode(), f'photo-upload:{user}:{expiry}'.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected) or int(expiry) < time.time() or int(user) <= 0:
            raise ValueError()
        return int(user)
    except (ValueError, UnicodeError):
        raise web.HTTPUnauthorized(text='Reopen the form from the bot')


def batch_files(root, batch, user_id):
    if not re.fullmatch(r'[0-9a-f]{32}', batch):
        raise ValueError('Invalid photo batch')
    folder = Path(root) / batch
    metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
    if metadata['user_id'] != user_id:
        raise ValueError('Photo batch belongs to another user')
    return [(folder / item['name'], item['mime']) for item in metadata['files']]


def make_app(bot_token, root, origin):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    limiter = asyncio.Semaphore(2)

    @web.middleware
    async def cors(request, handler):
        if request.headers.get('Origin') not in (None, origin):
            raise web.HTTPForbidden()
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = exc
        response.headers.update({'Access-Control-Allow-Origin': origin,
                                 'Access-Control-Allow-Headers': 'Authorization,Content-Type',
                                 'Access-Control-Allow-Methods': 'POST,OPTIONS',
                                 'Cache-Control': 'no-store', 'Vary': 'Origin'})
        return response

    async def upload(request):
        user_id = ticket_user(request.headers.get('Authorization', '').removeprefix('Bearer '), bot_token)
        # Bound storage per user and globally; unfinished batches are never sent automatically.
        folders = list(root.glob('*/metadata.json'))
        if len(folders) >= 1000:
            raise web.HTTPTooManyRequests(text='Photo storage is full')
        recent = 0
        for path in folders:
            item = json.loads(path.read_text(encoding='utf-8'))
            if item['user_id'] == user_id and item['created'] > time.time() - 3600:
                recent += 1
        if recent >= 10:
            raise web.HTTPTooManyRequests(text='Try again later')
        async with limiter:
            async with asyncio.timeout(90):
                reader = await request.multipart()
                files = []
                while (part := await reader.next()) is not None:
                    if part.name != 'photo' or len(files) >= 3:
                        raise web.HTTPBadRequest(text='Select up to three photos')
                    content = bytearray()
                    while chunk := await part.read_chunk():
                        content.extend(chunk)
                        if len(content) > MAX_FILE:
                            raise web.HTTPRequestEntityTooLarge(max_size=MAX_FILE, actual_size=len(content))
                    try:
                        with Image.open(io.BytesIO(content)) as image:
                            format_name = image.format
                            if format_name not in ('JPEG', 'PNG', 'WEBP'):
                                raise ValueError()
                            image.verify()
                    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
                        raise web.HTTPBadRequest(text='Use JPEG, PNG or WebP photos')
                    suffix, mime = {'JPEG': ('jpg', 'image/jpeg'), 'PNG': ('png', 'image/png'), 'WEBP': ('webp', 'image/webp')}[format_name]
                    files.append((bytes(content), suffix, mime))
                if not files:
                    raise web.HTTPBadRequest(text='No photos')
                batch = secrets.token_hex(16)
                folder = root / batch
                folder.mkdir()
                metadata = {'user_id': user_id, 'created': time.time(), 'files': []}
                for i, (content, suffix, mime) in enumerate(files):
                    name = f'photo-{batch}-{i}.{suffix}'
                    (folder / name).write_bytes(content)
                    metadata['files'].append({'name': name, 'mime': mime})
                (folder / 'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
                return web.json_response({'batch': batch, 'count': len(files)})

    async def preflight(request):
        return web.Response(status=204)

    async def health(request):
        return web.json_response({'ok': True})

    app = web.Application(middlewares=[cors], client_max_size=31 * 1024 * 1024)
    app.router.add_post('/photos', upload)
    app.router.add_options('/photos', preflight)
    app.router.add_get('/health', health)
    return app


async def start_server(bot_token, data_dir):
    runner = web.AppRunner(make_app(bot_token, Path(data_dir) / 'photo_batches',
                                   'https://shozerg22.github.io'), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', int(os.getenv('PHOTO_UPLOAD_PORT', '8091'))).start()
    return runner
