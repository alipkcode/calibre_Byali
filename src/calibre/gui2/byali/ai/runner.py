#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Resolve connection for the agent
#   2) Apply per-agent model/temperature via provider prefs override
#   3) Enrich system prompt with reasoning / web-search hints
#   4) Collect streamed response text
#   5) Surface HTTP error bodies; retry once without temperature on 400 if needed

"""Run a Byali agent against selected book text via a named LLM connection."""

from __future__ import annotations

from urllib.error import HTTPError

from calibre.ai import ChatMessage, ChatMessageType
from calibre.ai.openai_compatible import OpenAICompatible
from calibre.ai.prefs import override_prefs_for_providers
from calibre.ai.utils import plugin_for_name
from calibre.gui2.byali.ai import debug_log
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


def _looks_like_temperature_reject(details: str) -> bool:
    d = (details or '').lower()
    if not d:
        return False
    if 'temperature' in d:
        return True
    if 'unsupported_value' in d and 'param' in d:
        return True
    if 'unsupported parameter' in d and 'temperature' in d:
        return True
    return False


def _format_chat_error(exc: BaseException, details: str = '') -> str:
    msg = str(exc).strip() or exc.__class__.__name__
    details = (details or '').strip()
    if details and details not in msg:
        return f'{msg}: {details}'
    return msg


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

    def _run(disable_temperature: bool = False) -> tuple[str, Exception | None, str]:
        overrides = {
            OpenAICompatible.name: connection.provider_prefs(model=model, temperature=agent.temperature),
        }
        if disable_temperature:
            overrides[OpenAICompatible.name]['disable_temperature'] = True
        chunks: list[str] = []
        last_error: Exception | None = None
        last_details = ''
        try:
            with use_connection_transport(connection.dns_server, connection.effective_proxy_url()):
                with override_prefs_for_providers(overrides):
                    for res in plugin.text_chat(messages, model):
                        if res.exception is not None:
                            last_error = res.exception
                            last_details = getattr(res, 'error_details', '') or ''
                            break
                        if res.content:
                            chunks.append(res.content)
        except TransportError as e:
            return '', e, str(e)
        return ''.join(chunks).strip(), last_error, last_details

    debug_log.info(
        'LLM request',
        agent=agent.name,
        model=model,
        api_host=(connection.api_url or '')[:80],
        temperature=agent.temperature,
        has_proxy=bool(connection.effective_proxy_url()),
    )

    text, last_error, last_details = _run(disable_temperature=False)

    if last_error is not None:
        err_msg = _format_chat_error(last_error, last_details)
        debug_log.error(
            'LLM first attempt failed',
            agent=agent.name,
            model=model,
            exc_type=type(last_error).__name__,
            msg=err_msg[:500],
        )
        # One safe retry: many modern models reject temperature with HTTP 400.
        # Also retry when the body is empty — some gateways omit a useful message.
        if isinstance(last_error, HTTPError) and getattr(last_error, 'code', None) == 400:
            if _looks_like_temperature_reject(last_details) or not last_details:
                debug_log.info('LLM retry without temperature', agent=agent.name, model=model)
                text, last_error, last_details = _run(disable_temperature=True)
                if last_error is None and text:
                    debug_log.info('LLM retry without temperature succeeded', agent=agent.name, model=model)
                elif last_error is not None:
                    err_msg = _format_chat_error(last_error, last_details)
                    debug_log.error(
                        'LLM retry without temperature failed',
                        agent=agent.name,
                        model=model,
                        msg=err_msg[:500],
                    )

    if last_error is not None:
        if isinstance(last_error, TransportError):
            raise AgentRunError(str(last_error)) from last_error
        msg = _format_chat_error(last_error, last_details)
        if connection.effective_proxy_url():
            msg = _(
                'Could not reach the provider through the configured proxy. '
                'Check the Proxies tab or try another approved proxy. Details: {0}'
            ).format(msg)
        raise AgentRunError(msg)
    if not text:
        raise AgentRunError(_('The AI returned an empty response.'))
    if len(text) >= 2 and ((text[0] == text[-1] == '"') or (text[0] == text[-1] == "'")):
        text = text[1:-1].strip()
    return text
