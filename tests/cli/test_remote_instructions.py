"""``seahorse.cli.remote_instructions`` — the single source for paste blocks.

The same strings back the human terminal, the ``--json`` payload and
``docs/connect.md``; these tests pin the exact contract (URL + token
interpolation, honest caveats, ``--app`` filtering) so the block bodies can
only change deliberately.
"""

from __future__ import annotations

import pytest

from seahorse.cli import remote_instructions as ri

URL = "https://example-words.trycloudflare.com/mcp"
TOKEN = "cafe" * 8


def test_security_warning_names_the_token_barrier_and_the_stop_command():
    assert "token" in ri.SECURITY_WARNING
    assert "the only barrier" in ri.SECURITY_WARNING
    assert "seahorse remote stop" in ri.SECURITY_WARNING


def test_chatgpt_block_interpolates_url_token_and_carries_the_note():
    block = ri.chatgpt_block(URL, TOKEN)
    assert URL in block
    assert TOKEN in block
    assert "Scan Tools" in block
    assert ri.CHATGPT_UNVERIFIED in block


def test_gemini_web_block_is_an_honest_placeholder():
    # Pre-experiment: no invented credential instructions — the pending note
    # is the contract; the token stays OUT of the block until verified.
    block = ri.gemini_web_block(URL, TOKEN)
    assert URL in block
    assert ri.GEMINI_WEB_PENDING in block
    assert TOKEN not in block


def test_gemini_cli_block_is_a_paste_ready_command():
    block = ri.gemini_cli_block(URL, TOKEN)
    assert block.count("\n") == 1
    assert URL in block
    assert f'--header "Authorization: Bearer {TOKEN}"' in block


def test_claude_code_block_is_a_paste_ready_command():
    block = ri.claude_code_block(URL, TOKEN)
    assert block.count("\n") == 1
    assert "-s user --transport http seahorse-remote" in block
    assert f'--header "Authorization: Bearer {TOKEN}"' in block


@pytest.mark.parametrize(
    ("app", "expected_keys"),
    [
        ("all", ("chatgpt", "gemini_web", "gemini_cli", "claude_code")),
        ("chatgpt", ("chatgpt",)),
        ("gemini", ("gemini_web",)),
        ("gemini-cli", ("gemini_cli",)),
        ("claude-code", ("claude_code",)),
    ],
)
def test_instruction_blocks_filter_by_app_choice(app, expected_keys):
    blocks = ri.instruction_blocks(app, mcp_url=URL, token=TOKEN)
    assert tuple(blocks) == expected_keys


def test_instruction_blocks_unknown_choice_raises():
    with pytest.raises(ValueError, match="unknown app choice"):
        ri.instruction_blocks("slack", mcp_url=URL, token=TOKEN)


def test_human_instructions_single_block_is_the_block():
    assert ri.human_instructions("gemini-cli", mcp_url=URL, token=TOKEN) == (
        ri.gemini_cli_block(URL, TOKEN)
    )


def test_human_instructions_all_blocks_blank_line_separated():
    text = ri.human_instructions("all", mcp_url=URL, token=TOKEN)
    for header in ("— ChatGPT", "— Gemini (web app)", "— Gemini CLI", "— Claude Code"):
        assert header in text
    assert text.count("\n\n—") == 3  # 4 blocks, 3 blank-line seams