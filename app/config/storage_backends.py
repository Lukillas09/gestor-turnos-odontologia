import json
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import Storage


class SupabaseStorageError(RuntimeError):
    pass


class SupabaseStorage(Storage):
    def __init__(
        self,
        project_url=None,
        bucket=None,
        service_role_key=None,
        timeout=None,
        cache_control=None,
        signed_url_seconds=None,
    ):
        self.project_url = project_url or settings.SUPABASE_STORAGE_URL
        self.bucket = bucket or settings.SUPABASE_STORAGE_BUCKET
        self.service_role_key = service_role_key or settings.SUPABASE_STORAGE_SERVICE_ROLE_KEY
        self.timeout = timeout or settings.SUPABASE_STORAGE_TIMEOUT
        self.cache_control = cache_control or settings.SUPABASE_STORAGE_CACHE_CONTROL
        self.signed_url_seconds = signed_url_seconds or settings.SUPABASE_STORAGE_SIGNED_URL_SECONDS

        if not self.project_url:
            raise ImproperlyConfigured("SUPABASE_STORAGE_URL debe estar configurado.")

        if not self.bucket:
            raise ImproperlyConfigured("SUPABASE_STORAGE_BUCKET debe estar configurado.")

        if not self.service_role_key:
            raise ImproperlyConfigured("SUPABASE_STORAGE_SERVICE_ROLE_KEY debe estar configurado.")

        self.storage_url = self._normalizar_storage_url(self.project_url)

    def _open(self, name, mode="rb"):
        if "b" not in mode:
            raise ValueError("SupabaseStorage solo soporta lectura binaria.")

        data = self._request("GET", self._object_url(name))
        return ContentFile(data, name=name)

    def _save(self, name, content):
        data = self._leer_contenido(content)
        content_type = getattr(content, "content_type", "") or "application/octet-stream"
        headers = {
            "Content-Type": content_type,
            "cache-control": str(self.cache_control),
            "x-upsert": "false",
        }
        self._request("POST", self._object_url(name), data=data, headers=headers)
        return name

    def delete(self, name):
        body = json.dumps({"prefixes": [name]}).encode("utf-8")
        self._request(
            "DELETE",
            self._bucket_object_url(),
            data=body,
            headers={"Content-Type": "application/json"},
            expected_statuses={200},
        )

    def exists(self, name):
        try:
            self._request("HEAD", self._object_url(name), expected_statuses={200})
        except SupabaseStorageError as error:
            if "400" in str(error) or "404" in str(error):
                return False
            raise

        return True

    def size(self, name):
        headers = self._request_headers("HEAD", self._object_url(name))
        content_length = headers.get("Content-Length")

        if not content_length:
            return 0

        return int(content_length)

    def url(self, name):
        body = json.dumps({"expiresIn": self.signed_url_seconds}).encode("utf-8")
        response = self._request(
            "POST",
            self._signed_url_endpoint(name),
            data=body,
            headers={"Content-Type": "application/json"},
        )
        data = json.loads(response.decode("utf-8"))
        signed_url = data.get("signedURL") or data.get("signedUrl") or ""

        if signed_url.startswith("http"):
            return signed_url

        if signed_url.startswith("/"):
            return f"{self.storage_url}{signed_url}"

        return signed_url

    def _request(self, method, url, data=None, headers=None, expected_statuses=None):
        expected_statuses = expected_statuses or {200, 201}
        request = Request(
            url,
            data=data,
            headers=self._headers(headers),
            method=method,
        )

        try:
            # URL construida desde SUPABASE_STORAGE_URL configurada.
            with urlopen(request, timeout=self.timeout) as response:  # nosec B310
                status = response.status

                if status not in expected_statuses:
                    raise SupabaseStorageError(f"Supabase Storage devolvio HTTP {status}.")

                return response.read()
        except HTTPError as error:
            detalle = error.read().decode("utf-8", errors="replace")
            raise SupabaseStorageError(
                f"Supabase Storage devolvio HTTP {error.code}: {detalle}"
            ) from error
        except URLError as error:
            raise SupabaseStorageError("No se pudo conectar con Supabase Storage.") from error

    def _request_headers(self, method, url):
        request = Request(url, headers=self._headers(), method=method)

        try:
            # URL construida desde SUPABASE_STORAGE_URL configurada.
            with urlopen(request, timeout=self.timeout) as response:  # nosec B310
                return response.headers
        except HTTPError as error:
            detalle = error.read().decode("utf-8", errors="replace")
            raise SupabaseStorageError(
                f"Supabase Storage devolvio HTTP {error.code}: {detalle}"
            ) from error
        except URLError as error:
            raise SupabaseStorageError("No se pudo conectar con Supabase Storage.") from error

    def _headers(self, extra_headers=None):
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
        }
        headers.update(extra_headers or {})
        return headers

    def _object_url(self, name):
        return f"{self._bucket_object_url()}/{self._quote_path(name)}"

    def _bucket_object_url(self):
        return f"{self.storage_url}/object/{quote(self.bucket, safe='')}"

    def _signed_url_endpoint(self, name):
        return (
            f"{self.storage_url}/object/sign/"
            f"{quote(self.bucket, safe='')}/{self._quote_path(name)}"
        )

    @staticmethod
    def _normalizar_storage_url(project_url):
        url = project_url.rstrip("/")

        if url.endswith("/storage/v1"):
            return url

        return f"{url}/storage/v1"

    @staticmethod
    def _quote_path(name):
        return quote(str(name).lstrip("/"), safe="/")

    @staticmethod
    def _leer_contenido(content):
        if hasattr(content, "open"):
            content.open()

        if hasattr(content, "chunks"):
            return b"".join(chunk for chunk in content.chunks())

        data = content.read()

        if isinstance(data, str):
            return data.encode("utf-8")

        if isinstance(data, BytesIO):
            return data.getvalue()

        return data
