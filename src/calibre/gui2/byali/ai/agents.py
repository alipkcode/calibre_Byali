#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Agent model (prompts + per-agent LLM options)
#   2) Default rewrite agents (translate / summarize / explain / ...)
#   3) JSONConfig persistence (additive; never wipe user agents)

"""Byali AI agent definitions and preferences."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any
from uuid import uuid4

from calibre.utils.config import JSONConfig
from calibre.utils.localization import _

REASONING_CHOICES = ('auto', 'none', 'low', 'medium', 'high')


@dataclass
class Agent:
    id: str
    name: str
    system_prompt: str
    user_template: str  # use {selected} for the selected book text
    enabled: bool = True
    builtin: bool = False
    # Per-agent LLM options (connection / model / sampling)
    connection_id: str = ''
    model: str = ''
    temperature: float = 0.7
    reasoning_strategy: str = 'auto'
    allow_web_searches: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Agent:
        reasoning = str(data.get('reasoning_strategy') or 'auto')
        if reasoning not in REASONING_CHOICES:
            reasoning = 'auto'
        try:
            temperature = float(data.get('temperature', 0.7))
        except (TypeError, ValueError):
            temperature = 0.7
        temperature = max(0.0, min(2.0, temperature))
        return cls(
            id=str(data.get('id') or uuid4().hex),
            name=str(data.get('name') or _('Unnamed agent')),
            system_prompt=str(data.get('system_prompt') or ''),
            user_template=str(data.get('user_template') or '{selected}'),
            enabled=bool(data.get('enabled', True)),
            builtin=bool(data.get('builtin', False)),
            connection_id=str(data.get('connection_id') or ''),
            model=str(data.get('model') or ''),
            temperature=temperature,
            reasoning_strategy=reasoning,
            allow_web_searches=bool(data.get('allow_web_searches', False)),
        )


def default_agents() -> list[Agent]:
    return [
        Agent(
            id='byali-translate',
            name=_('Translate'),
            system_prompt=(
                'You are a careful literary translator. Preserve meaning, tone, and paragraph structure. '
                'Return ONLY the translated text with no quotes or commentary.'
            ),
            user_template='Translate the following text into clear natural English:\n\n{selected}',
            builtin=True,
        ),
        Agent(
            id='byali-summarize',
            name=_('Summarize'),
            system_prompt=(
                'You summarize book passages faithfully. Return ONLY the summary text, '
                'no preamble or bullet labels unless they improve clarity.'
            ),
            user_template='Summarize this passage concisely while keeping key ideas:\n\n{selected}',
            builtin=True,
        ),
        Agent(
            id='byali-explain',
            name=_('Explain simply'),
            system_prompt=(
                'You explain book passages in simple language for a smart adult reader. '
                'Return ONLY the rewritten/explained text.'
            ),
            user_template='Rewrite/explain this passage in simpler language:\n\n{selected}',
            builtin=True,
        ),
        Agent(
            id='byali-rewrite',
            name=_('Rewrite clearer'),
            system_prompt=(
                'You improve clarity and flow of book text without changing meaning. '
                'Return ONLY the rewritten passage.'
            ),
            user_template='Rewrite this passage more clearly while preserving meaning:\n\n{selected}',
            builtin=True,
        ),
        Agent(
            id='byali-grammar',
            name=_('Fix grammar'),
            system_prompt=(
                'You correct grammar, spelling, and punctuation only. '
                'Return ONLY the corrected text.'
            ),
            user_template='Fix grammar and spelling in this passage:\n\n{selected}',
            builtin=True,
        ),
    ]


@lru_cache(2)
def prefs() -> JSONConfig:
    ans = JSONConfig('byali_ai')
    ans.defaults['agents'] = [a.to_dict() for a in default_agents()]
    ans.defaults['connections'] = []
    ans.defaults['last_agent_id'] = 'byali-translate'
    return ans


def load_agents(include_disabled: bool = False) -> list[Agent]:
    raw = prefs().get('agents') or []
    agents = [Agent.from_dict(x) for x in raw if isinstance(x, dict)]
    if not agents:
        agents = default_agents()
        save_agents(agents)
    if include_disabled:
        return agents
    return [a for a in agents if a.enabled]


def save_agents(agents: list[Agent]) -> None:
    prefs().set('agents', [a.to_dict() for a in agents])


def upsert_agent(agent: Agent) -> None:
    agents = load_agents(include_disabled=True)
    for i, existing in enumerate(agents):
        if existing.id == agent.id:
            agents[i] = agent
            break
    else:
        agents.append(agent)
    save_agents(agents)


def delete_agent(agent_id: str) -> None:
    agents = load_agents(include_disabled=True)
    kept = []
    for a in agents:
        if a.id != agent_id:
            kept.append(a)
        elif a.builtin:
            a.enabled = False
            kept.append(a)
    save_agents(kept)


def new_agent_template() -> Agent:
    from calibre.gui2.byali.ai.connections import load_connections

    connections = load_connections()
    conn_id = connections[0].id if connections else ''
    return Agent(
        id=uuid4().hex,
        name=_('New agent'),
        system_prompt=_('You rewrite the selected book text. Return ONLY the replacement text.'),
        user_template='{selected}',
        builtin=False,
        connection_id=conn_id,
    )


def ensure_defaults_seeded() -> None:
    """Append any missing builtin agents without overwriting user edits."""
    agents = load_agents(include_disabled=True)
    have = {a.id for a in agents}
    changed = False
    for builtin in default_agents():
        if builtin.id not in have:
            agents.append(deepcopy(builtin))
            changed = True
    if changed:
        save_agents(agents)
