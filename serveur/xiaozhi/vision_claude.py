"""Fournisseur de vision Claude (SDK Anthropic) pour xiaozhi-esp32-server.

À copier en main/xiaozhi-server/core/providers/vllm/claude.py (le nom du fichier est le « type »).
Billy prend une photo (outil self.camera.take_photo, 640 × 480), l'envoie à /mcp/vision/explain
avec une question ; ce fournisseur la décrit, et la réponse est dite telle quelle par Billy :
elle doit donc être courte, orale et en français.

Configuration (data/.config.yaml) :
  selected_module: { VLLM: ClaudeVLLM }
  VLLM:
    ClaudeVLLM: { type: claude, model_name: claude-haiku-5-5, max_tokens: 400 }
La clé vient de la variable d'environnement ANTHROPIC_API_KEY.
"""

import anthropic

from config.logger import setup_logging
from core.providers.vllm.base import VLLMProviderBase

TAG = __name__
logger = setup_logging()

CONSIGNE = ("Tu es Billy, un petit robot de bureau, et tu regardes une photo prise par ta propre caméra "
            "(petite, un peu floue). Réponds à la question en français, à l'oral, en une à trois phrases "
            "courtes, sans liste ni mise en forme, sans parler de « photo » ni d'« image » : dis ce que tu "
            "vois comme si tu regardais. Si c'est trop flou ou trop sombre pour répondre, dis-le simplement.")


def type_image(base64_image: str) -> str:
    """Le serveur annonce du JPEG ; on vérifie la signature pour PNG, GIF ou WebP."""
    debut = base64_image[:16]
    if debut.startswith("iVBOR"):
        return "image/png"
    if debut.startswith("R0lG"):
        return "image/gif"
    if debut.startswith("UklGR"):
        return "image/webp"
    return "image/jpeg"


class VLLMProvider(VLLMProviderBase):
    def __init__(self, config):
        self.model_name = config.get("model_name", "claude-haiku-5-5")
        self.max_tokens = int(config.get("max_tokens") or 400)
        self.client = anthropic.Anthropic(timeout=anthropic.Timeout(30.0, connect=5.0))

    def response(self, question, base64_image):
        message = self.client.messages.create(
            model=self.model_name,
            max_tokens=self.max_tokens,
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            system=CONSIGNE,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": type_image(base64_image),
                                             "data": base64_image}},
                {"type": "text", "text": question or "Qu'est-ce que tu vois ?"},
            ]}],
        )
        if message.stop_reason == "refusal":
            return "Je préfère ne pas décrire ça."
        texte = "".join(b.text for b in message.content if b.type == "text").strip()
        logger.bind(tag=TAG).info(f"Vision Claude : {message.usage.input_tokens} jetons en entrée, {texte[:80]}")
        return texte or "Je n'arrive pas à voir grand-chose."
