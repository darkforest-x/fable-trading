"""Bounded raster attachments stored with the paper book, independent of text edits.

Images are decoded for validation and never treated as HTML/SVG. Removal is
reversible; per-position content hashes make uncertain upload retries safe.
"""
import base64
import hashlib
import io
import uuid
import warnings

from .paper_desk import clock_ms

MAX_BYTES = 6 * 1024 * 1024
MAX_IMAGES = 12
IMAGE_COLUMNS = 'id,position_id,name,mime,width,height,created_ms,deleted,length(data) AS bytes'


def list_images(db, position_id):
    return [dict(row) for row in db.execute('SELECT '+IMAGE_COLUMNS+' FROM journal_images WHERE position_id=? ORDER BY created_ms,id', (position_id,))]


def export_images(db, account_id):
    rows = db.execute('SELECT i.* FROM journal_images i JOIN positions p ON p.id=i.position_id WHERE p.account_id=? ORDER BY i.created_ms,i.id', (account_id,))
    return [dict({k: row[k] for k in row.keys() if k != 'data'}, data_base64=base64.b64encode(row['data']).decode('ascii')) for row in rows]


class JournalImages:
    def __init__(self, desk):
        self.desk = desk

    def add(self, position_id, name, content):
        if not content or len(content) > MAX_BYTES:
            raise ValueError('图片不能为空，且每张不得超过 6 MB')
        from PIL import Image
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    fmt, (width, height) = image.format, image.size
                    if fmt not in {'PNG', 'JPEG', 'WEBP'} or width * height > 20_000_000 or getattr(image, 'n_frames', 1) != 1:
                        raise ValueError('unsupported image')
                    image.verify()
                with Image.open(io.BytesIO(content)) as image:
                    image.load()
        except Exception:
            raise ValueError('请使用有效的 PNG、JPEG 或 WebP 静态图片（不超过 2000 万像素）') from None
        digest = hashlib.sha256(content).hexdigest()
        with self.desk.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.desk.row(db, 'positions', position_id)
            existing = db.execute('SELECT id,deleted FROM journal_images WHERE position_id=? AND sha256=?', (position_id, digest)).fetchone()
            if existing and not existing['deleted']:
                return list_images(db, position_id)
            count = db.execute('SELECT count(*) FROM journal_images WHERE position_id=? AND deleted=0', (position_id,)).fetchone()[0]
            if count >= MAX_IMAGES:
                raise ValueError('每笔日志最多保存 12 张图片，请先移除不需要的图片')
            if existing:
                db.execute('UPDATE journal_images SET deleted=0 WHERE id=?', (existing['id'],))
            else:
                db.execute('INSERT INTO journal_images VALUES(?,?,?,?,?,?,?,?,?,0)',
                           (uuid.uuid4().hex, position_id, (name or '截图').replace('\\', '/').split('/')[-1][:160],
                            {'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[fmt], content, digest, width, height, clock_ms()))
            return list_images(db, position_id)

    def get(self, position_id, image_id):
        with self.desk.connect() as db:
            row = db.execute('SELECT * FROM journal_images WHERE position_id=? AND id=?', (position_id, image_id)).fetchone()
            if row is None:
                raise KeyError('日志图片不存在')
            return dict(row)

    def visibility(self, position_id, image_id, deleted):
        with self.desk.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT deleted FROM journal_images WHERE position_id=? AND id=?', (position_id, image_id)).fetchone()
            if row is None:
                raise KeyError('日志图片不存在')
            if not deleted and row['deleted'] and db.execute('SELECT count(*) FROM journal_images WHERE position_id=? AND deleted=0', (position_id,)).fetchone()[0] >= MAX_IMAGES:
                raise ValueError('每笔日志最多保存 12 张图片')
            db.execute('UPDATE journal_images SET deleted=? WHERE position_id=? AND id=?', (int(deleted), position_id, image_id))
            return list_images(db, position_id)
