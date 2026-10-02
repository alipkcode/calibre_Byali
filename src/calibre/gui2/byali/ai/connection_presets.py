#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) ConnectionPreset dataclass (label, api_url, key hint, optional headers)
#   2) Catalog of major OpenAI-compatible providers
#   3) factory: connection_from_preset() → Connection

"""Add-menu presets that prefill Byali LLM connections for major providers."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from calibre.gui2.byali.ai.connections import Connection
from calibre.utils.localization import _


@dataclass(frozen=True)
class ConnectionPreset:
    id: str
    label: str
    api_url: str
    name: str
    key_placeholder: str = ''
    headers: tuple[tuple[str, str], ...] = ()
    tip: str = ''
    is_custom: bool = False


def connection_presets() -> list[ConnectionPreset]:
    """
    Major providers that speak OpenAI-compatible /v1 APIs.

    Anthropic’s native Messages API is not OpenAI-compatible, so Claude is
    offered via OpenRouter (same pattern many clients use). Gemini uses
    Google’s official OpenAI-compatibility endpoint.
    """
    return [
        ConnectionPreset(
            id='openai',
            label=_('OpenAI'),
            name=_('OpenAI'),
            api_url='https://api.openai.com/v1',
            key_placeholder=_('sk-… from platform.openai.com'),
            tip=_('Official OpenAI Chat Completions API.'),
        ),
        ConnectionPreset(
            id='google-gemini',
            label=_('Google Gemini'),
            name=_('Google Gemini'),
            api_url='https://generativelanguage.googleapis.com/v1beta/openai/',
            key_placeholder=_('API key from aistudio.google.com'),
            tip=_('Google’s OpenAI-compatible Gemini endpoint.'),
        ),
        ConnectionPreset(
            id='claude-openrouter',
            label=_('Anthropic Claude (via OpenRouter)'),
            name=_('Claude (OpenRouter)'),
            api_url='https://openrouter.ai/api/v1',
            key_placeholder=_('sk-or-… from openrouter.ai — then pick an anthropic/… model'),
            tip=_(
                'Claude’s native API is not OpenAI-compatible. '
                'This prefills OpenRouter so you can use Claude models (anthropic/…) here.'
            ),
        ),
        ConnectionPreset(
            id='openrouter',
            label=_('OpenRouter'),
            name=_('OpenRouter'),
            api_url='https://openrouter.ai/api/v1',
            key_placeholder=_('sk-or-… from openrouter.ai'),
            tip=_('One key for many models (OpenAI, Claude, Gemini, and more).'),
        ),
        ConnectionPreset(
            id='deepseek',
            label=_('DeepSeek'),
            name=_('DeepSeek'),
            api_url='https://api.deepseek.com/v1',
            key_placeholder=_('API key from platform.deepseek.com'),
        ),
        ConnectionPreset(
            id='groq',
            label=_('Groq'),
            name=_('Groq'),
            api_url='https://api.groq.com/openai/v1',
            key_placeholder=_('API key from console.groq.com'),
        ),
        ConnectionPreset(
            id='mistral',
            label=_('Mistral AI'),
            name=_('Mistral AI'),
            api_url='https://api.mistral.ai/v1',
            key_placeholder=_('API key from console.mistral.ai'),
        ),
        ConnectionPreset(
            id='xai-grok',
            label=_('xAI Grok'),
            name=_('xAI Grok'),
            api_url='https://api.x.ai/v1',
            key_placeholder=_('API key from console.x.ai'),
        ),
        ConnectionPreset(
            id='together',
            label=_('Together AI'),
            name=_('Together AI'),
            api_url='https://api.together.xyz/v1',
            key_placeholder=_('API key from api.together.xyz'),
        ),
        ConnectionPreset(
            id='ollama',
            label=_('Ollama (local)'),
            name=_('Ollama'),
            api_url='http://localhost:11434/v1',
            key_placeholder=_('Usually leave blank for local Ollama'),
            tip=_('Requires Ollama running locally with OpenAI-compatible /v1 enabled.'),
        ),
        ConnectionPreset(
            id='lm-studio',
            label=_('LM Studio (local)'),
            name=_('LM Studio'),
            api_url='http://localhost:1234/v1',
            key_placeholder=_('Usually leave blank for LM Studio'),
            tip=_('Start the local server in LM Studio, then refresh models.'),
        ),
        ConnectionPreset(
            id='custom',
            label=_('Custom…'),
            name=_('New connection'),
            api_url='https://api.openai.com/v1',
            key_placeholder=_('Optional API key (Bearer)'),
            tip=_('Blank-slate OpenAI-compatible endpoint — edit name and URL yourself.'),
            is_custom=True,
        ),
    ]


def connection_from_preset(preset: ConnectionPreset) -> Connection:
    """Build a new Connection prefilled from a preset (does not save)."""
    headers = [[a, b] for a, b in preset.headers]
    return Connection(
        id=uuid4().hex,
        name=preset.name,
        api_url=preset.api_url,
        api_key='',
        headers=headers,
        timeout=120,
        cached_models=[],
        dns_server='',
        proxy_url='',
    )


def preset_by_id(preset_id: str) -> ConnectionPreset | None:
    for p in connection_presets():
        if p.id == preset_id:
            return p
    return None
