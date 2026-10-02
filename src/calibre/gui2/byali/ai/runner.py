#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Resolve connection for the agent
#   2) Apply per-agent model/temperature via provider prefs override
#   3) Enrich system prompt with reasoning / web-search hints
#   4) Collect streamed response text

"""Run a Byali agent against selected book text via a named LLM connection."""

from __future__ import annotations

from calibre.ai import ChatMessage, ChatMessageType
from calibre.ai.openai_compatible import OpenAICompatible
from calibre.ai.prefs import override_prefs_for_providers
from calibre.ai.utils import plugin_for_name
from calibre.gui2.byali.ai.agents import Agent
from calibre.gui2.byali.ai.connections import get_connection
from calibre.gui2.byali.ai.transport import TransportError, use_connection_transport
from calibre.utils.localization import _


class AgentRunError(Exception):
    pass


def _format_user_prompt(template: str, selected: str) -> str:
    try:
        return template.format(selected=selected)
    except (KeyError, ValueError, IndexError):
        return f'{template}\n\n{selected}'


def _system_prompt_for_agent(agent: Agent) -> str:
    parts = [agent.system_prompt.strip()] if agent.system_prompt.strip() else []
    if getattr(agent, 'is_note_agent', False):
        parts.append(
            'You are producing a reading annotation/note about the passage. '
            'Do not return a rewritten copy of the passage. Return only the note.'
        )
    match agent.reasoning_strategy:
        case 'none':
            parts.append('Respond directly without extended chain-of-thought. Return only the final text.')
        case 'low':
            parts.append('Use light reasoning; keep the answer concise and return only the final text.')
        case 'medium':
            parts.append('Think carefully before answering, then return only the final text.')
        case 'high':
            parts.append('Use thorough reasoning for accuracy and nuance, then return only the final text.')
        case _:
            pass
    if agent.allow_web_searches:
        parts.append(
            'If you have web-search or up-to-date retrieval capability, use it when it improves factual accuracy.'
        )
    return '\n\n'.join(p for p in parts if p)


def run_agent_on_text(agent: Agent, selected_text: str) -> str:
    selected = (selected_text or '').strip()
    if not selected:
        raise AgentRunError(_('No text is selected.'))

    connection = get_connection(agent.connection_id)
    if connection is None or not connection.api_url.strip():
        raise AgentRunError(_('No LLM connection is configured for this agent. Open Byali AI Settings.'))

    model = (agent.model or '').strip()
    if not model and connection.cached_models:
        model = connection.cached_models[0]
    if not model:
        raise AgentRunError(_('No model selected for this agent. Open Byali AI Settings and choose a model.'))

    plugin = plugin_for_name(OpenAICompatible.name)
    if plugin is None:
        raise AgentRunError(_('OpenAI-compatible AI provider plugin is unavailable.'))

    messages = []
    system = _system_prompt_for_agent(agent)
    if system:
        messages.append(ChatMessage(system, type=ChatMessageType.system))
    messages.append(ChatMessage(_format_user_prompt(agent.user_template, selected)))

    overrides = {
        OpenAICompatible.name: connection.provider_prefs(model=model, temperature=agent.temperature),
    }
    chunks: list[str] = []
    last_error = None
    try:
        with use_connection_transport(connection.dns_server, connection.effective_proxy_url()):
            with override_prefs_for_providers(overrides):
                for res in plugin.text_chat(messages, model):
                    if res.exception is not None:
                        last_error = res.exception
                        break
                    if res.content:
                        chunks.append(res.content)
    except TransportError as e:
        raise AgentRunError(str(e)) from e
    if last_error is not None:
        msg = str(last_error)
        if connection.effective_proxy_url():
            msg = _(
                'Could not reach the provider through the configured proxy. '
                'Check the Proxies tab or try another approved proxy. Details: {0}'
            ).format(msg)
        raise AgentRunError(msg)
    text = ''.join(chunks).strip()
    if not text:
        raise AgentRunError(_('The AI returned an empty response.'))
    if len(text) >= 2 and ((text[0] == text[-1] == '"') or (text[0] == text[-1] == "'")):
        text = text[1:-1].strip()
    return text
