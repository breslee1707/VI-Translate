import logging

log = logging.getLogger(__name__)

__version__ = "1.9.11"
__ruleset__ = "code4life-preservation-v1"
__author__ = "Byaidu"
__maintainer__ = "Lê Ngọc Gia Huy (huyg.ai)"
__maintainer_url__ = "https://www.tiktok.com/@huyg.ai"
__all__ = ["translate", "translate_stream"]


def __getattr__(name):
    if name in {"translate", "translate_stream"}:
        from pdf2zh.high_level import translate, translate_stream

        return {"translate": translate, "translate_stream": translate_stream}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
