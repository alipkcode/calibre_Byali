#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   Tab 1: ConfigureAI (reuse calibre providers / API keys)
#   Tab 2: CRUD list of Byali rewrite agents (system + user prompts)

"""Settings dialog for Byali AI providers and rewrite agents."""

from __future__ import annotations

from qt.core import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    Qt,
)

from calibre.ai import AICapabilities
from calibre.ai.config import ConfigureAI
from calibre.gui2.byali.ai.agents import (
    Agent,
    delete_agent,
    ensure_defaults_seeded,
    load_agents,
    new_agent_template,
    upsert_agent,
)
from calibre.gui2.widgets2 import Dialog
from calibre.utils.localization import _


class AgentsEditor(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        ensure_defaults_seeded()
        layout = QHBoxLayout(self)
        self.list = QListWidget(self)
        self.list.currentRowChanged.connect(self._load_current)
        layout.addWidget(self.list, 1)

        right = QVBoxLayout()
        layout.addLayout(right, 2)
        form = QFormLayout()
        right.addLayout(form)
        self.name_edit = QLineEdit(self)
        self.system_edit = QPlainTextEdit(self)
        self.system_edit.setPlaceholderText(_('System instructions for this agent'))
        self.user_edit = QPlainTextEdit(self)
        self.user_edit.setPlaceholderText(_('User prompt template. Use {selected} for the selected text.'))
        form.addRow(_('Name:'), self.name_edit)
        form.addRow(_('System prompt:'), self.system_edit)
        form.addRow(_('User template:'), self.user_edit)
        tip = QLabel(_('Tip: keep user templates ending with {selected} so the agent rewrites the highlighted passage.'))
        tip.setWordWrap(True)
        right.addWidget(tip)

        buttons = QHBoxLayout()
        right.addLayout(buttons)
        self.add_btn = QPushButton(_('Add agent'), self)
        self.save_btn = QPushButton(_('Save agent'), self)
        self.del_btn = QPushButton(_('Delete / disable'), self)
        self.add_btn.clicked.connect(self._add)
        self.save_btn.clicked.connect(self._save_current)
        self.del_btn.clicked.connect(self._delete_current)
        buttons.addWidget(self.add_btn)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.del_btn)
        buttons.addStretch()

        self._agents: list[Agent] = []
        self._reload_list()

    def _reload_list(self, select_id: str | None = None):
        self._agents = load_agents(include_disabled=True)
        self.list.clear()
        select_row = 0
        for i, agent in enumerate(self._agents):
            label = agent.name + ('' if agent.enabled else f' ({_("disabled")})')
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, agent.id)
            self.list.addItem(item)
            if select_id and agent.id == select_id:
                select_row = i
        if self._agents:
            self.list.setCurrentRow(select_row)

    def _current_agent(self) -> Agent | None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self._agents):
            return None
        return self._agents[row]

    def _load_current(self, row: int):
        agent = self._current_agent()
        if agent is None:
            return
        self.name_edit.setText(agent.name)
        self.system_edit.setPlainText(agent.system_prompt)
        self.user_edit.setPlainText(agent.user_template)

    def _add(self):
        agent = new_agent_template()
        upsert_agent(agent)
        self._reload_list(select_id=agent.id)

    def _save_current(self):
        agent = self._current_agent()
        if agent is None:
            return
        agent.name = self.name_edit.text().strip() or agent.name
        agent.system_prompt = self.system_edit.toPlainText()
        agent.user_template = self.user_edit.toPlainText() or '{selected}'
        agent.enabled = True
        upsert_agent(agent)
        self._reload_list(select_id=agent.id)

    def _delete_current(self):
        agent = self._current_agent()
        if agent is None:
            return
        if agent.builtin:
            agent.enabled = False
            upsert_agent(agent)
        else:
            delete_agent(agent.id)
        self._reload_list()


class ByaliAISettingsDialog(Dialog):
    def __init__(self, parent=None):
        super().__init__(_('Byali AI Settings'), 'byali-ai-settings', parent=parent)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        layout.addWidget(tabs)

        provider_tab = QWidget(self)
        provider_layout = QVBoxLayout(provider_tab)
        intro = QLabel(
            _(
                'Connect an AI provider (OpenAI, Anthropic, Ollama, Google, …). '
                'API keys are stored securely in calibre’s AI preferences.'
            )
        )
        intro.setWordWrap(True)
        provider_layout.addWidget(intro)
        self.ai_config = ConfigureAI(purpose=AICapabilities.text_to_text, parent=self)
        provider_layout.addWidget(self.ai_config)
        tabs.addTab(provider_tab, _('LLM connection'))

        self.agents_editor = AgentsEditor(self)
        tabs.addTab(self.agents_editor, _('AI Agents'))

        layout.addWidget(self.bb)

    def accept(self):
        if not self.ai_config.commit():
            return
        # Persist any in-progress agent edits
        self.agents_editor._save_current()
        super().accept()
