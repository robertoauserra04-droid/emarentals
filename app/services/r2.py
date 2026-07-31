"""Cliente Cloudflare R2 (boto3, S3-compatible): subir media y servir por URL pública.

Port de bienesraicesEnrique. endpoint = https://{ACCOUNT_ID}.r2.cloudflarestorage.com,
region "auto". URL pública = {R2_PUBLIC_URL}/{key}. Si faltan credenciales, `disponible()`
es False y los endpoints de subida responden 503 (la feature se degrada, no rompe el arranque).
"""
from functools import lru_cache
from typing import BinaryIO

from app.config import settings


def disponible() -> bool:
    return bool(settings.r2_account_id and settings.r2_access_key_id
               and settings.r2_secret_access_key and settings.r2_bucket and settings.r2_public_url)


@lru_cache
def _client():
    import boto3  # import perezoso: no obligar la dependencia si no se usa R2
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def subir(fileobj: BinaryIO, key: str, content_type: str | None = None) -> str:
    """Sube un archivo a R2 bajo `key` y devuelve su URL pública."""
    extra = {"ContentType": content_type} if content_type else {}
    _client().upload_fileobj(fileobj, settings.r2_bucket, key, ExtraArgs=extra)
    return url_publica(key)


def url_publica(key: str) -> str:
    return f"{settings.r2_public_url.rstrip('/')}/{key}"


def key_de_url(url: str) -> str | None:
    """Deriva la key desde su URL pública (None si no pertenece a este bucket)."""
    base = settings.r2_public_url.rstrip("/") + "/"
    return url[len(base):] if url and url.startswith(base) else None


def borrar(key: str) -> None:
    _client().delete_object(Bucket=settings.r2_bucket, Key=key)
