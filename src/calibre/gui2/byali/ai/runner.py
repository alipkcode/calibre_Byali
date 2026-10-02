#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Resolve text-to-text AI provider
#   2) Build ChatMessages from Agent + selected text
#   3) Collect streaming response into one string

"""Run a Byali agent against selected book text via calibre AI providers."""

from __future__ import annotations

from calibre.ai import AICapabilities, ChatMessage, ChatMessageType
from calibre.ai.prefs import plugin_for_purpose
from calibre.gui2.byali.ai.agents import Agent
from calibre.utils.localization import _


class AgentRunError(Exception):
    pass


def _format_user_prompt(template: str, selected: str) -> str:
    try:
        return template.format(selected=selected)
    except (KeyError, ValueError, IndexError):
        return f'{template}\n\n{selected}'


def run_agent_on_text(agent: Agent, selected_text: str) -> str:
    selected = (selected_text or '').strip()
    if not selected:
        raise AgentRunError(_('No text is selected.'))

    plugin = plugin_for_purpose(AICapabilities.text_to_text)
    if plugin is None or not plugin.is_ready_for_use:
        raise AgentRunError(_('No AI provider is configured. Open Byali AI Settings and connect a provider.'))

    messages = []
    if agent.system_prompt.strip():
        messages.append(ChatMessage(agent.system_prompt.strip(), type=ChatMessageType.system))
    messages.append(ChatMessage(_format_user_prompt(agent.user_template, selected)))

    chunks: list[str] = []
    last_error = None
    for res in plugin.text_chat(messages):
        if res.exception is not None:
            last_error = res.exception
            break
        if res.content:
            chunks.append(res.content)
    if last_error is not None:
        raise AgentRunError(str(last_error))
    text = ''.join(chunks).strip()
    if not text:
        raise AgentRunError(_('The AI returned an empty response.'))
    if len(text) >= 2 and ((text[0] == text[-1] == '"') or (text[0] == text[-1] == "'")):
        text = text[1:-1].strip()
    return text
