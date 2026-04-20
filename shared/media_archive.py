#!/usr/bin/env python3
"""media_archive — Archive all incoming media to Claw-Dash channel (optional, via MEDIA_ARCHIVE_CHANNEL_ID).

Usage (Python):
    from media_archive import archive_media
    archive_media(bot, message, 'hesabdar')

Runs in background thread — non-blocking for the handler.
Uses MAIL_BOT_TOKEN (same as channel_log.py).
"""
import json
import os
import threading
import urllib.request
from datetime import datetime

CHANNEL_ID = os.environ.get('MEDIA_ARCHIVE_CHANNEL_ID', '')
TOKEN = os.environ.get(
    'MAIL_BOT_TOKEN',
    '',
)

BOT_ICONS = {
    'hesabdar':   '\U0001f4b0',   # 💰
    'mail':       '\U0001f4e7',   # 📧
    'poker':      '\U0001f0cf',   # 🃏
    'wabridge':   '\U0001f4f1',   # 📱
    'openclaw':   '\U0001f916',   # 🤖
    'claudecode': '\U0001f4bb',   # 💻
}

# Telegram API method + form-field per media type
_METHOD_MAP = {
    'photo':    ('sendPhoto',    'photo'),
    'document': ('sendDocument', 'document'),
    'voice':    ('sendVoice',    'voice'),
    'audio':    ('sendAudio',    'audio'),
    'video':    ('sendVideo',    'video'),
}


def archive_media(bot_instance, message, bot_name='unknown', extra_caption=''):
    """Archive a media message to the Claw-Dash channel. Non-blocking (background thread)."""
    threading.Thread(
        target=_archive_worker,
        args=(bot_instance, message, bot_name, extra_caption),
        daemon=True,
    ).start()


def _detect_media(message):
    """Return (file_id, media_type, filename_hint) or None."""
    if message.photo:
        return message.photo[-1].file_id, 'photo', 'photo.jpg'
    if message.document:
        fname = getattr(message.document, 'file_name', None) or 'document'
        return message.document.file_id, 'document', fname
    if message.voice:
        return message.voice.file_id, 'voice', 'voice.ogg'
    if message.audio:
        fname = getattr(message.audio, 'file_name', None) or 'audio.mp3'
        return message.audio.file_id, 'audio', fname
    if message.video:
        return message.video.file_id, 'video', 'video.mp4'
    return None


def _build_caption(message, bot_name, extra_caption):
    icon = BOT_ICONS.get(bot_name, '\U0001f4ce')   # 📎
    sender = '?'
    if message.from_user:
        sender = message.from_user.first_name or str(message.from_user.id)
    ts = datetime.now().strftime('%d.%m.%Y %H:%M')
    parts = [f"{icon} {bot_name} | von {sender}", ts]
    if extra_caption:
        parts.append(extra_caption)
    if message.caption:
        parts.append(message.caption)
    caption = '\n'.join(parts)
    return caption[:1024]   # Telegram caption limit


def _upload_to_channel(file_data, filename, media_type, caption):
    """Multipart/form-data upload via urllib — mit Todo/Bookmark/Tagebuch Action-Buttons."""
    method, field = _METHOD_MAP.get(media_type, ('sendDocument', 'document'))
    boundary = '----MediaArchiveBoundary9876'
    body = b''

    # Action-Buttons für jedes Channel-Media (callback_data → mail-bot)
    # Ref = erste Zeile der Caption (gekürzt, ohne |)
    ref = (caption.split('\n', 1)[0] if caption else '')[:40].replace('|', ' ')
    reply_markup = {
        'inline_keyboard': [[
            {'text': '📋 Todo',     'callback_data': f'ch_todo|{ref}'},
            {'text': '📚 Bookmark', 'callback_data': f'ch_bm|{ref}'},
            {'text': '✒️ Tagebuch', 'callback_data': f'ch_diary|{ref}'},
        ]]
    }

    # chat_id
    body += f'--{boundary}\r\n'.encode()
    body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
    body += CHANNEL_ID.encode() + b'\r\n'

    # caption
    body += f'--{boundary}\r\n'.encode()
    body += b'Content-Disposition: form-data; name="caption"\r\n\r\n'
    body += caption.encode('utf-8') + b'\r\n'

    # reply_markup
    body += f'--{boundary}\r\n'.encode()
    body += b'Content-Disposition: form-data; name="reply_markup"\r\n\r\n'
    body += json.dumps(reply_markup).encode('utf-8') + b'\r\n'

    # file
    body += f'--{boundary}\r\n'.encode()
    body += f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode()
    body += b'Content-Type: application/octet-stream\r\n\r\n'
    body += file_data
    body += b'\r\n'

    # close
    body += f'--{boundary}--\r\n'.encode()

    url = f'https://api.telegram.org/bot{TOKEN}/{method}'
    req = urllib.request.Request(url, data=body)
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')

    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())
        return result.get('ok', False)


def _archive_worker(bot_instance, message, bot_name, extra_caption):
    try:
        detected = _detect_media(message)
        if not detected:
            return
        file_id, media_type, filename = detected

        # Download file via the receiving bot
        file_info = bot_instance.get_file(file_id)
        file_data = bot_instance.download_file(file_info.file_path)

        # Build caption
        caption = _build_caption(message, bot_name, extra_caption)

        # Use original filename from file_path if available
        orig_name = os.path.basename(file_info.file_path or '') or filename

        # Upload to Claw-Dash channel
        ok = _upload_to_channel(file_data, orig_name, media_type, caption)
        if ok:
            print(f"[media_archive] {media_type} from {bot_name} archived OK", flush=True)
        else:
            print(f"[media_archive] {media_type} from {bot_name} upload FAILED", flush=True)
    except Exception as e:
        print(f"[media_archive] ERROR ({bot_name}): {e}", flush=True)
