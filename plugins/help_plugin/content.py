"""The user's hand-written global menu, preserved in its original order."""

import re
from utils.rendering.models import HelpDocument, ParagraphBlock
from .config import load_config


def load_help_document():
    config = load_config()
    paragraphs = tuple(config.help_text)
    full_text = "\n".join(paragraphs)
    links = tuple(dict.fromkeys(re.findall(r"https?://[^\s<>]+", full_text)))
    document = HelpDocument(
        title=config.bot_name,
        introduction=paragraphs[0] if paragraphs else "",
        paragraphs=tuple(ParagraphBlock(text) for text in paragraphs[1:]),
    )
    return document, links
