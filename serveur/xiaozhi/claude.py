"""Fournisseur Claude (SDK Anthropic) pour xiaozhi-esp32-server.

À copier dans main/xiaozhi-server/core/providers/llm/claude/claude.py.
Le serveur garde l'historique au format OpenAI (rôles system/user/assistant/tool,
tool_calls) : on le convertit à l'aller, et on renvoie les appels d'outils sous la
forme que core/connection.py sait fusionner (objets avec id, index, function.name,
function.arguments).

Configuration (data/.config.yaml) :
  LLM:
    ClaudeLLM:
      type: claude
      model_name: claude-haiku-5-5
      max_tokens: 1024
      effort: low
La clé vient de la variable d'environnement ANTHROPIC_API_KEY.
"""

import json
from types import SimpleNamespace

import anthropic

from config.logger import setup_logging
from core.providers.llm.base import LLMProviderBase

TAG = __name__
logger = setup_logging()

# Sans réflexion, un modèle peut écrire un appel d'outil dans son texte au lieu de l'émettre.
TOOL_HINT = ("Quand tu utilises un outil, tu peux dire une courte phrase avant. Si aucun outil ne "
             "permet de faire ce qui est demandé, dis-le au lieu de deviner. N'écris jamais de balises "
             "XML internes ou système dans ta réponse.")
WEB_HINT = (" Tu peux chercher sur le web (outil web_search) pour une information récente ou précise "
            "que tu ne connais pas : horaires, programmes, résultats, actualité d'un sujet. Pour la "
            "météo ou ce que couvrent tes outils maison, utilise plutôt ces outils. Avant "
            "de chercher, dis d'abord une très courte phrase, comme « Je regarde… », puis réponds en "
            "une ou deux phrases, sans lire d'adresse web ; si la recherche ne donne rien de sûr, dis-le.")


def to_anthropic(dialogue):
    """Historique OpenAI → (system, messages) au format de l'API Messages."""
    system, messages = [], []

    def push(role, blocks):
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)  # tours consécutifs fusionnés
        else:
            messages.append({"role": role, "content": blocks})

    for m in dialogue:
        role, content = m.get("role"), m.get("content") or ""
        if role == "system":
            if content:
                system.append(str(content))
        elif role == "tool":
            # Tous les résultats d'un même tour vont dans un seul message user.
            push("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id"),
                           "content": str(content) or "(vide)"}])
        elif role == "assistant":
            blocks = [{"type": "text", "text": str(content)}] if content else []
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"],
                               "input": args if isinstance(args, dict) else {}})
            if blocks:
                push("assistant", blocks)
        elif content:
            push("user", [{"type": "text", "text": str(content)}])

    if messages and messages[0]["role"] != "user":
        messages.insert(0, {"role": "user", "content": [{"type": "text", "text": "Bonjour"}]})
    return "\n\n".join(system), messages


def to_tools(functions):
    """Outils au format OpenAI → outils Anthropic."""
    return [{"name": f["function"]["name"],
             "description": f["function"].get("description", ""),
             "input_schema": f["function"].get("parameters") or {"type": "object", "properties": {}}}
            for f in functions or []]


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.model_name = config.get("model_name", "claude-haiku-5-5")
        self.max_tokens = int(config.get("max_tokens") or 1024)
        self.effort = config.get("effort", "low")
        # Recherche web exécutée par Anthropic, en conversation seulement. Haiku 5.5 refuse la
        # version 20260209 (filtrage dynamique) : on garde 20250305.
        self.web_max_uses = int(config.get("web_max_uses") or 0)
        self.web_location = config.get("web_location")
        # Le module mémoire teste « llm.api_key » : la vraie clé reste dans l'environnement.
        self.api_key = "ANTHROPIC_API_KEY (environnement)"
        self.client = anthropic.Anthropic(timeout=anthropic.Timeout(60.0, connect=5.0))

    def _stream(self, dialogue, functions, max_tokens=None):
        system, messages = to_anthropic(dialogue)
        params = {
            "model": self.model_name,
            # Le résumé mémoire demande 2 000 jetons : avec 1 024, son JSON était coupé et rejeté.
            "max_tokens": int(max_tokens or self.max_tokens),
            "messages": messages,
            # Réponses courtes à l'oral : pas de réflexion, effort bas. L'historique du serveur
            # ne garde pas de blocs de réflexion, il n'y a donc rien à rejouer.
            "thinking": {"type": "disabled"},
            "output_config": {"effort": self.effort},
            "cache_control": {"type": "ephemeral"},
        }
        tools = to_tools(functions)
        if tools and self.web_max_uses:
            web = {"type": "web_search_20250305", "name": "web_search", "max_uses": self.web_max_uses}
            if self.web_location:
                web["user_location"] = dict(self.web_location, type="approximate")
            tools.append(web)
        if tools:
            params["tools"] = tools
            hint = TOOL_HINT + (WEB_HINT if self.web_max_uses else "")
            system = f"{system}\n\n{hint}" if system else hint
        if system:
            params["system"] = system

        # Les appels d'outils sont relayés au fil de l'eau : le serveur lit l'argument « response »
        # de son outil direct_answer pendant qu'il s'écrit et l'envoie aussitôt à la synthèse vocale.
        tool_index = {}  # index du bloc Anthropic → rang de l'appel côté serveur
        with self.client.messages.stream(**params) as stream:
            for event in stream:
                if event.type == "text":
                    yield event.text, None
                elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                    tool_index[event.index] = len(tool_index)
                    yield None, [SimpleNamespace(
                        index=tool_index[event.index], id=event.content_block.id, type="function",
                        function=SimpleNamespace(name=event.content_block.name, arguments=""))]
                elif (event.type == "content_block_delta" and event.delta.type == "input_json_delta"
                      and event.delta.partial_json and event.index in tool_index):
                    # (les blocs de la recherche web, exécutée par Anthropic, ne sont pas relayés)
                    yield None, [SimpleNamespace(
                        index=tool_index[event.index], id=None, type="function",
                        function=SimpleNamespace(name=None, arguments=event.delta.partial_json))]
            final = stream.get_final_message()

        usage = final.usage
        logger.bind(tag=TAG).info(
            f"Claude : {usage.input_tokens} jetons en entrée "
            f"(dont {usage.cache_read_input_tokens or 0} en cache), {usage.output_tokens} en sortie, "
            f"arrêt {final.stop_reason}")
        if final.stop_reason == "pause_turn":
            logger.bind(tag=TAG).warning("recherche web interrompue (pause_turn) : réponse partielle")
        if final.stop_reason == "refusal":
            yield "Désolé, je ne peux pas répondre à ça.", None
        elif final.stop_reason == "max_tokens" and tool_index:
            logger.bind(tag=TAG).warning("appel d'outil coupé par max_tokens : ses arguments sont incomplets")

    def response(self, session_id, dialogue, **kwargs):
        try:
            for text, _ in self._stream(dialogue, None, kwargs.get("max_tokens")):
                if text:
                    yield text
        except anthropic.APIError as e:
            logger.bind(tag=TAG).error(f"Claude indisponible : {e}")
            yield "Je n'arrive pas à joindre mon cerveau, réessaie dans un instant."

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        try:
            yield from self._stream(dialogue, functions)
        except anthropic.APIError as e:
            logger.bind(tag=TAG).error(f"Claude indisponible : {e}")
            yield "Je n'arrive pas à joindre mon cerveau, réessaie dans un instant.", None
