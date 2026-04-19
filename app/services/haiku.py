"""Haiku client for Conventional Commit message generation.

Uses OpenRouter as primary (already in Infisical per helix.env), with direct
Anthropic API as fallback.  The prompt is tuned carefully — bad commit
messages mean an untrustworthy git log.

Design notes:
  - Commit messages are generated from the diff, not from intent.  Intent
    is a hint for the subject line only.
  - Subject line strictly ≤ 72 chars, Conventional Commit format.
  - Body explains WHY, not WHAT — the diff already shows what changed.
  - No markdown, no code fences — raw commit message text.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# We prefer Haiku 3.5 — fast, cheap, and plenty smart for commit messages.
# Model name format differs between OpenRouter and direct Anthropic API.
OPENROUTER_MODEL = "anthropic/claude-3.5-haiku"
ANTHROPIC_MODEL = "claude-3-5-haiku-20241022"


SYSTEM_PROMPT = """You are a Conventional Commits message generator. Given a git diff and the author's brief intent, write a clean commit message.

Rules:
1. Subject line: `<type>: <short description>` where type is provided by the caller. Max 72 characters total. Imperative mood ("add" not "added"). No period at end.
2. Blank line, then body explaining WHY the change matters. Body lines wrap at 72 chars. Skip body if the change is trivial (type=chore or single-line docs fix).
3. Body explains motivation, not mechanics. The diff already shows mechanics. Example: GOOD "Haiku was returning stale results when the cache TTL expired mid-request" / BAD "Changed line 45 to check cache.expired_at before returning".
4. No markdown, no code fences, no bullet points unless the change genuinely touches multiple unrelated areas (and that's a code smell worth flagging).
5. If the diff shows ONLY whitespace/formatting, use type=chore and body="Whitespace and formatting; no logic change."
6. If the diff is empty, return an error message starting with "ERROR:" — don't fabricate content.

Your output will be used verbatim as a git commit message. Write ONLY the message.
"""


class HaikuError(Exception):
    """Commit message generation failed.  Caller should surface clearly."""


class Haiku:
    """LLM client for commit message generation.

    Primary: OpenRouter.  Fallback: Anthropic direct.  Both are optional —
    if neither key is set the client raises on first call, which is caught
    by the main endpoint and surfaced as a config error.
    """

    def __init__(
        self,
        openrouter_key: Optional[str] = None,
        anthropic_key: Optional[str] = None,
        timeout_s: float = 30.0,
    ):
        self.openrouter_key = openrouter_key or os.environ.get("OPENROUTER_API_KEY")
        self.anthropic_key = anthropic_key or os.environ.get("ANTHROPIC_API_KEY")
        self.timeout_s = timeout_s

    async def generate_commit_message(
        self,
        commit_type: str,
        intent: str,
        diff: str,
    ) -> str:
        """Generate a Conventional Commit message from the diff.

        commit_type: one of feat/fix/refactor/docs/chore/test/perf/ci
        intent:      caller's brief free-text hint
        diff:        output of `git diff --cached`, truncated upstream if large

        Returns the raw message text.  Raises HaikuError if both providers fail.
        """
        if not diff.strip():
            raise HaikuError("cannot generate commit message: staged diff is empty")

        user_prompt = (
            f"Commit type: {commit_type}\n"
            f"Author intent: {intent}\n\n"
            f"Staged diff:\n```\n{diff}\n```"
        )

        # Try OpenRouter first.
        if self.openrouter_key:
            try:
                msg = await self._call_openrouter(user_prompt)
                return self._post_validate(msg, commit_type)
            except Exception as e:  # noqa: BLE001 — fall through to fallback
                logger.warning("openrouter failed, trying anthropic direct: %s", e)

        # Fallback to direct Anthropic.
        if self.anthropic_key:
            msg = await self._call_anthropic(user_prompt)
            return self._post_validate(msg, commit_type)

        raise HaikuError(
            "no LLM provider available: set OPENROUTER_API_KEY or ANTHROPIC_API_KEY"
        )

    async def _call_openrouter(self, user_prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "Content-Type": "application/json",
            # OpenRouter likes a Referer for attribution; internal tools don't need real value.
            "HTTP-Referer": "https://millyweb.com/git-agent",
            "X-Title": "git-agent",
        }
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 600,
            "temperature": 0.2,  # Deterministic-ish; we want consistent commit voice.
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HaikuError(f"openrouter HTTP {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise HaikuError(f"openrouter unexpected response shape: {e}: {json.dumps(data)[:400]}")

    async def _call_anthropic(self, user_prompt: str) -> str:
        headers = {
            "x-api-key": self.anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": ANTHROPIC_MODEL,
            "max_tokens": 600,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(ANTHROPIC_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HaikuError(f"anthropic HTTP {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        try:
            # Anthropic returns content as a list of blocks; the first should be text.
            return data["content"][0]["text"]
        except (KeyError, IndexError) as e:
            raise HaikuError(f"anthropic unexpected response shape: {e}: {json.dumps(data)[:400]}")

    def _post_validate(self, message: str, commit_type: str) -> str:
        """Enforce our Conventional-Commits invariants on the LLM output.

        Three checks:
          1. Strip any markdown code fences the model sometimes adds despite the prompt.
          2. Enforce that the first line starts with `<type>:` matching our caller's type.
          3. Subject line <= 72 chars (truncate if over, preserving meaning is caller's job).
        """
        message = message.strip()
        # Strip code fences if present (seen occasionally despite prompt).
        if message.startswith("```"):
            lines = message.split("\n")
            # Remove first and last line if they're fences.
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            message = "\n".join(lines).strip()

        if message.upper().startswith("ERROR:"):
            raise HaikuError(f"LLM refused: {message}")

        subject, _, rest = message.partition("\n")

        # Ensure the type prefix matches.  If the LLM forgot, prepend it.
        expected_prefix = f"{commit_type}:"
        if not subject.lower().startswith(expected_prefix.lower()):
            # The subject might have a scope: `feat(scope):` — accept that too.
            scoped_prefix = f"{commit_type}("
            if not subject.lower().startswith(scoped_prefix.lower()):
                subject = f"{commit_type}: {subject.lstrip().rstrip('.')}"

        # Enforce 72-char subject cap.
        if len(subject) > 72:
            subject = subject[:69].rstrip() + "..."

        if rest:
            return f"{subject}\n{rest}"
        return subject
