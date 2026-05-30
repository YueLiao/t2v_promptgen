"""Encoding detection for uploaded prompt files.

Strategy (in order):
  1. UTF-8 strict
  2. UTF-8 BOM (signature: EF BB BF)
  3. UTF-16 LE/BE (BOM: FF FE / FE FF)
  4. GB18030 (covers GBK + GB2312)
  5. chardet fallback (if installed)

Returns (decoded_text, detected_encoding) or raises ParseError(ENCODING_FAIL).
"""
from __future__ import annotations

from ..core.rewrite_schema import ParseError


_BOM_UTF8 = b"\xef\xbb\xbf"
_BOM_UTF16_LE = b"\xff\xfe"
_BOM_UTF16_BE = b"\xfe\xff"


def detect_and_decode(data: bytes) -> tuple[str, str]:
    """Decode bytes → str, returning (text, detected_encoding).

    Raises ParseError('PARSE_ENCODING_FAIL') if no codec succeeds.
    """
    if not data:
        # Empty file is "successfully decoded as empty string" — caller
        # decides what to do with empty content.
        return "", "utf-8"

    # 1. BOM-based detection (fast path)
    if data.startswith(_BOM_UTF8):
        try:
            return data[3:].decode("utf-8"), "utf-8-sig"
        except UnicodeDecodeError:
            pass
    if data.startswith(_BOM_UTF16_LE):
        try:
            return data[2:].decode("utf-16-le"), "utf-16-le"
        except UnicodeDecodeError:
            pass
    if data.startswith(_BOM_UTF16_BE):
        try:
            return data[2:].decode("utf-16-be"), "utf-16-be"
        except UnicodeDecodeError:
            pass

    # 2. Try common encodings in order of likelihood
    for enc in ("utf-8", "gb18030", "utf-16", "big5"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue

    # 3. chardet fallback
    try:
        import chardet  # type: ignore
        guess = chardet.detect(data)
        enc = guess.get("encoding")
        confidence = guess.get("confidence", 0.0) or 0.0
        if enc and confidence >= 0.5:
            try:
                return data.decode(enc, errors="replace"), enc
            except (UnicodeDecodeError, LookupError):
                pass
    except ImportError:
        pass

    # 4. Give up
    raise ParseError(
        "PARSE_ENCODING_FAIL",
        "无法识别文件编码,请另存为 UTF-8 后再上传",
    )
