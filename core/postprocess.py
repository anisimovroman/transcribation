import re
import logging

logger = logging.getLogger(__name__)


def clean_text(text: str) -> str:
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"\b(\w+)\s+\1\b", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def add_punctuation(text: str, lang: str = "ru") -> str:
    try:
        from rpunct import RestorePuncts
        rp = RestorePuncts()
        words = text.split()
        chunk_size = 400
        chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]
        result = " ".join(rp.punctuate(chunk, lang=lang) for chunk in chunks)
        return result
    except ImportError:
        logger.warning("rpunct не установлен — пунктуация пропущена")
        return text
    except Exception as e:
        logger.warning("rpunct ошибка: %s — пунктуация пропущена", e)
        return text


def postprocess(text: str, method: str, language: str) -> str:
    text = clean_text(text)
    if method == "youtube_captions" and language in ("ru", "en"):
        text = add_punctuation(text, lang=language)
    return text
