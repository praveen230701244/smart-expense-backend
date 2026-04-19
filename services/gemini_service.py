"""
Gemini via Google Gen AI SDK (`pip install google-genai` / `from google import genai`).
Long HTTP timeouts + optional retries to reduce ConnectTimeout / SSL handshake issues.
"""

import os
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

_env_backend = Path(__file__).resolve().parent.parent
load_dotenv(_env_backend / ".env")
load_dotenv(_env_backend.parent / ".env")
load_dotenv()

# Prefer Mozilla CA bundle for OpenSSL / httpx (helps some Windows SSL setups)
try:
    import certifi

    _cert_path = certifi.where()
    print("certifi CA bundle:", _cert_path)
    if os.getenv("GEMINI_USE_CERTIFI", "true").lower().strip() in ("1", "true", "yes"):
        os.environ.setdefault("SSL_CERT_FILE", _cert_path)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", _cert_path)
except ImportError:
    print("certifi not installed (optional): pip install certifi")

try:
    from google import genai
    from google.genai import types
    from google.genai.types import HttpOptions
except ImportError as e:  # pragma: no cover
    genai = None  # type: ignore
    types = None  # type: ignore
    HttpOptions = None  # type: ignore
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None

# httpx uses separate connect vs read limits; ReadTimeout happens when the model is slow to stream.
def _httpx_timeout() -> httpx.Timeout:
    read_s = float(os.getenv("GEMINI_HTTP_READ_TIMEOUT", "300"))
    connect_s = float(os.getenv("GEMINI_HTTP_CONNECT_TIMEOUT", "60"))
    write_s = float(os.getenv("GEMINI_HTTP_WRITE_TIMEOUT", "120"))
    pool_s = float(os.getenv("GEMINI_HTTP_POOL_TIMEOUT", "60"))
    return httpx.Timeout(
        connect=connect_s,
        read=read_s,
        write=write_s,
        pool=pool_s,
    )


def _debug_insecure_ssl() -> bool:
    """TEMP DEBUG ONLY — set GEMINI_DEBUG_INSECURE_SSL=true to disable cert verification."""
    return os.getenv("GEMINI_DEBUG_INSECURE_SSL", "").lower().strip() in ("1", "true", "yes")


def _client_http_options() -> Any:
    """Used when constructing genai.Client — pass explicit httpx.Timeout so read isn't too short."""
    if HttpOptions is None:
        return None
    tx = _httpx_timeout()
    client_args: dict[str, Any] = {"timeout": tx}
    if _debug_insecure_ssl():
        client_args["verify"] = False
        print("⚠ GEMINI_DEBUG_INSECURE_SSL: SSL verification disabled (debug only)")
    print("Gemini httpx Timeout:", tx)
    return HttpOptions(
        client_args=client_args,
        retry_options=types.HttpRetryOptions(attempts=3, initial_delay=1.0, max_delay=20.0)
        if types
        else None,
    )


class GeminiService:
    def __init__(self) -> None:
        self.model = "models/gemini-2.5-flash"
        self.client = None
        self.api_key = ""

        api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        self.api_key = api_key

        print("GeminiService init - API KEY:", api_key if api_key else "(empty)")

        if _IMPORT_ERROR is not None or genai is None:
            print("❌ google-genai SDK not available:", repr(_IMPORT_ERROR))
            return

        if not api_key:
            print("❌ No API key found")
            return

        try:
            opts = _client_http_options()
            self.client = genai.Client(api_key=api_key, http_options=opts)
            print("✅ Gemini client initialized (explicit httpx read timeout, retries enabled)")
        except Exception as e:
            print("❌ Gemini client init failed:", e)
            self.client = None

    def generate_advice(
        self, system_prompt: str, user_prompt: str, timeout: float = 20.0
    ) -> Optional[str]:
        if not self.client:
            print("❌ Gemini client is None")
            return None

        _ = max(20.0, float(timeout))
        print("Gemini generate_advice — model:", self.model)

        combined = f"{system_prompt}\n\n{user_prompt}"

        def _once() -> Optional[str]:
            try:
                # Do not pass per-request HttpOptions(timeout=...) — it can override client read limit
                # and cause httpx.ReadTimeout while waiting for the model.
                kwargs: dict[str, Any] = {"model": self.model, "contents": combined}
                response = self.client.models.generate_content(**kwargs)
                text = getattr(response, "text", None)
                if text is None and response is not None:
                    print("Gemini: response has no .text, repr:", repr(response)[:500])
                    return None
                text = str(text).strip() if text else ""
                print("Gemini SUCCESS:", text[:2000] + ("…" if len(text) > 2000 else ""))
                if not text or len(text) < 5:
                    print("Gemini weak response")
                    return None
                return text
            except Exception as e:
                err = repr(e)
                print("GEMINI NETWORK ERROR:", err)
                # Avoid huge tracebacks for common timeout cases
                if "ReadTimeout" in err or "ConnectTimeout" in err:
                    print(
                        "Hint: increase GEMINI_HTTP_READ_TIMEOUT (default 300s) or check network/firewall."
                    )
                else:
                    import traceback

                    traceback.print_exc()
                return None

        first = _once()
        if first:
            return first
        print("Gemini: retrying once…")
        return _once()
