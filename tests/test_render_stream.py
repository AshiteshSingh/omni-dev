"""Unit tests for the streaming renderer in ``src/cli/render.py``.

These cover :func:`stream_response` and the pure
:func:`accumulate_tool_call_deltas` helper added in task 9.5. The dedicated
Hypothesis property test for "streamed output equals non-streamed output"
(Property 9) is implemented separately in task 9.6; these are example/edge-case
unit tests that verify the core streaming mechanics offline.

``stream_response`` is async; since ``pytest-asyncio`` is not a project
dependency, each test drives it via ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
from io import StringIO
from types import SimpleNamespace

from rich.console import Console

from src.cli.render import (
    accumulate_tool_call_deltas,
    clean_for_display,
    stream_response,
)
from tests.fakes import make_stream, make_tool_call


def _capture_console() -> Console:
    """A non-terminal recording console (no Live, deterministic output)."""
    return Console(file=StringIO(), force_terminal=False, legacy_windows=False)


def _run(stream, console=None):
    return asyncio.run(stream_response(stream, console or _capture_console()))


# ---------------------------------------------------------------------------
# accumulate_tool_call_deltas (pure helper)
# ---------------------------------------------------------------------------

def _frag(index, *, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_accumulate_empty_returns_empty():
    assert accumulate_tool_call_deltas([]) == []
    assert accumulate_tool_call_deltas(None) == []


def test_accumulate_single_call_fragmented_arguments():
    fragments = [
        _frag(0, id="call_1", name="read_file", arguments='{"pa'),
        _frag(0, arguments='th": "a.t'),
        _frag(0, arguments='xt"}'),
    ]
    calls = accumulate_tool_call_deltas(fragments)
    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].function.name == "read_file"
    assert calls[0].function.arguments == '{"path": "a.txt"}'


def test_accumulate_multiple_calls_by_index():
    fragments = [
        _frag(0, id="c0", name="read_file", arguments='{"path":'),
        _frag(1, id="c1", name="run_command", arguments='{"command":'),
        _frag(0, arguments='"a"}'),
        _frag(1, arguments='"ls"}'),
    ]
    calls = accumulate_tool_call_deltas(fragments)
    assert [c.function.name for c in calls] == ["read_file", "run_command"]
    assert calls[0].function.arguments == '{"path":"a"}'
    assert calls[1].function.arguments == '{"command":"ls"}'


def test_accumulate_skips_nameless_fragment():
    # A fragment that never resolves a tool name is incomplete and dropped.
    calls = accumulate_tool_call_deltas([_frag(0, arguments='{"x":1}')])
    assert calls == []


def test_accumulate_defaults_missing_arguments_to_empty_object():
    calls = accumulate_tool_call_deltas([_frag(0, id="c", name="think")])
    assert len(calls) == 1
    assert calls[0].function.arguments == "{}"


# ---------------------------------------------------------------------------
# stream_response: content accumulation
# ---------------------------------------------------------------------------

def test_stream_accumulates_content_text():
    stream = make_stream("Hello world", chunk_size=1)
    text, tool_calls = _run(stream)
    assert text == clean_for_display("Hello world")
    assert tool_calls == []


def test_stream_final_text_matches_clean_for_display():
    # Property 9 mechanism: streamed final text is the same cleaned text a
    # one-shot render would produce (token chunk size must not matter).
    content = "# Title\n\nSome **bold** text.\n\n```py\nprint(1)\n```\n"
    by_one, _ = _run(make_stream(content, chunk_size=1))
    by_five, _ = _run(make_stream(content, chunk_size=5))
    assert by_one == by_five == clean_for_display(content)


def test_stream_strips_tool_call_json_from_displayed_text():
    content = 'Here you go.\n```json\n{"name": "read_file", "arguments": {"path": "a"}}\n```'
    text, _ = _run(make_stream(content, chunk_size=3))
    assert "read_file" not in text or '"name"' not in text
    assert "Here you go." in text


# ---------------------------------------------------------------------------
# stream_response: tool-call accumulation from the stream
# ---------------------------------------------------------------------------

def test_stream_returns_assembled_tool_calls():
    # Two fragments for one call: name/id first, then argument tail.
    frag_a = make_tool_call("read_file", '{"path":', index=0)
    frag_b = SimpleNamespace(
        index=0, id=None, function=SimpleNamespace(name=None, arguments='"a.txt"}')
    )
    stream = make_stream("Reading.", chunk_size=2, tool_call_chunks=[[frag_a], [frag_b]])
    text, tool_calls = _run(stream)
    assert "Reading." in text
    assert len(tool_calls) == 1
    assert tool_calls[0].function.name == "read_file"
    assert tool_calls[0].function.arguments == '{"path":"a.txt"}'


# ---------------------------------------------------------------------------
# Robustness (Req 3.8): never raise out of the render path
# ---------------------------------------------------------------------------

def test_non_iterable_stream_returns_empty():
    text, tool_calls = _run(123)
    assert text == ""
    assert tool_calls == []


def test_chunks_missing_choices_or_delta_are_skipped():
    stream = [
        SimpleNamespace(),                       # no choices
        SimpleNamespace(choices=[]),             # empty choices
        SimpleNamespace(choices=[SimpleNamespace(delta=None)]),  # no delta
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", tool_calls=None))]),
    ]
    text, tool_calls = _run(stream)
    assert text == clean_for_display("ok")
    assert tool_calls == []


def test_dict_shaped_chunks_supported():
    stream = [
        {"choices": [{"delta": {"content": "he"}}]},
        {"choices": [{"delta": {"content": "llo"}}]},
    ]
    text, _ = _run(stream)
    assert text == clean_for_display("hello")


def test_async_iterable_stream_supported():
    async def agen():
        for chunk in make_stream("async ok", chunk_size=2):
            yield chunk

    text, tool_calls = asyncio.run(stream_response(agen(), _capture_console()))
    assert text == clean_for_display("async ok")
    assert tool_calls == []


def test_none_console_does_not_raise():
    # Renderer must tolerate a missing console (falls back to plain output).
    text, _ = asyncio.run(stream_response(make_stream("hi", chunk_size=1), None))
    assert text == clean_for_display("hi")
