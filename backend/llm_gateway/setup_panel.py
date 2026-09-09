"""Local settings draft for the TUI; persistence stays in SetupService."""

from __future__ import annotations

import time
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import replace

from llm_gateway.openai_oauth import REFRESH_MARGIN
from llm_gateway.runtime_config import OAuthCredential, RuntimeConfigurationError
from llm_gateway.runtime_store import prepare_setup_directory
from llm_gateway.setup_cli import (
    SetupConsole,
    _parser,
    _read_current_config,
    _save_selection,
    _selection,
)
from llm_gateway.setup_service import SetupError, SetupService, _PreparedAuthentication
from llm_gateway.setup_terminal import SelectionCancelled, SetupOption, SetupScreen


class SetupPanel:
    def __init__(
        self,
        service: SetupService,
        console: SetupConsole,
        environment: Mapping[str, str],
    ) -> None:
        self.service = service
        self.console = console
        self.environment = environment
        self.prepared: _PreparedAuthentication | None = None
        self.notice = "Not checked: credentials or backend activation."
        self.focus = "model"
        try:
            if prepare_setup_directory(service.store.home):
                self.notice = "Corrected runtime directory permissions to 0700."
            self.saved = _read_current_config(service)
        except (OSError, RuntimeConfigurationError, SetupError):
            self.saved = None
            self.notice = "Saved configuration is invalid or unreadable. Configure a connection to repair it."
        self.draft = self.saved

    @property
    def dirty(self) -> bool:
        return self.draft != self.saved or (
            self.prepared is not None
            and self.prepared.credential != self.prepared.previous
        )

    def title(self) -> SetupScreen:
        status = (
            "Unsaved changes"
            if self.dirty
            else "Saved locally"
            if self.saved
            else "Not configured"
        )
        image = ""
        if self.draft and not self.service.choices(self.draft.provider).image:
            image = "\nImage generation unavailable for this connection."
        return SetupScreen(
            "Hybro", tab="models", status=status, notice=f"{self.notice}{image}"
        )

    def options(self) -> tuple[SetupOption, ...]:
        connection = "Not configured"
        if self.draft:
            provider = {
                "openai": "OpenAI",
                "deepseek": "DeepSeek",
                "anthropic": "Anthropic",
            }[self.draft.provider.id]
            auth = (
                "ChatGPT/Codex OAuth"
                if self.draft.provider.auth == "oauth"
                else "API key"
            )
            connection = f"{provider} / {auth}"
        options = [SetupOption("connection", f"Connection     {connection}")]
        if self.draft:
            options.append(
                SetupOption("model", f"Text model     {self.draft.models.text}")
            )
            if self.service.choices(self.draft.provider).image:
                options.append(
                    SetupOption(
                        "image", f"Image model    {self.draft.models.image or 'None'}"
                    )
                )
            options.append(
                SetupOption(
                    "save",
                    "Verify and save",
                    hint="Quota/billing applies. One text request; no auto-restart.",
                )
            )
        options.append(SetupOption("services", "Services [Tab]", shortcut=b"\t"))
        return tuple(
            replace(option, current=option.value == self.focus) for option in options
        )

    def _authenticate(self) -> _PreparedAuthentication:
        assert self.draft is not None
        credential = self.prepared.credential if self.prepared else None
        if self.prepared is None or (
            isinstance(credential, OAuthCredential)
            and credential.expires_at <= time.time() + REFRESH_MARGIN
        ):
            self.prepared = self.service.authenticate(
                self.draft.provider,
                self.environment,
                non_interactive=False,
                read_secret=self.console.read_secret,
                report_source=self.console.write,
            )
        return self.prepared

    def edit(self, action: str) -> None:
        self.focus = action
        if prepare_setup_directory(self.service.store.home):
            self.console.write("Corrected runtime directory permissions to 0700.")
        if action == "connection":
            draft, prepared = _selection(
                _parser().parse_args([]),
                self.service,
                self.console,
                self.environment,
                initial=self.draft,
            )
            self.draft, self.prepared = draft, prepared
            self.focus = "save"
        elif action in {"model", "image"}:
            assert self.draft is not None
            # Stored valid authentication is reused without another provider/auth menu.
            prepared = self.prepared
            try:
                self._authenticate()
                choices = self.service.choices(self.draft.provider)
                current = (
                    self.draft.models.text
                    if action == "model"
                    else self.draft.models.image or "none"
                )
                models = choices.text if action == "model" else ("none", *choices.image)
                selected = self.console.select(
                    "Text model" if action == "model" else "Image model",
                    tuple(
                        SetupOption(
                            model,
                            "None" if model == "none" else model,
                            current=model == current,
                        )
                        for model in models
                    ),
                )
            except SelectionCancelled:
                self.prepared = prepared
                raise
            key = "text" if action == "model" else "image"
            self.draft = self.draft.model_copy(
                update={
                    "models": self.draft.models.model_copy(
                        update={key: None if selected == "none" else selected}
                    )
                }
            )
        elif action == "save":
            assert self.draft is not None
            prepared = self._authenticate()
            # Restore SIGINT after each save, not only when the long-lived panel exits.
            with ExitStack() as cleanup:
                _save_selection(
                    self.draft, prepared, self.service, self.console, cleanup
                )
            self.saved = self.draft
            self.prepared = None
            self.focus = "model"
            self.notice = (
                "Saved locally; backend was not restarted.\n"
                "Apply: docker compose up -d --build --no-deps --force-recreate backend"
            )
            return
        self.notice = "Changes are local until Verify and save; images are not probed."

    def can_exit(self) -> bool:
        if not self.dirty:
            return True
        try:
            return (
                self.console.select(
                    "Discard unsaved changes?",
                    (
                        SetupOption("keep", "Keep editing"),
                        SetupOption("discard", "Discard and exit"),
                    ),
                )
                == "discard"
            )
        except SelectionCancelled:
            return False
