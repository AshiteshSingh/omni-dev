"""Offline fake model backend for the Omni-Dev test suite.

The entire suite runs without a network connection (Requirement 8.8). The Agent
Loop and Model Router obtain their model-call callable from
``src.model_router.get_completion_fn``; tests inject a :class:`FakeBackend` via
``src.model_router.set_completion_fn`` so every model call is served from a
scripted queue instead of a provider.

A :class:`FakeBackend` is *callable* like ``litellm.completion`` — it accepts
arbitrary keyword arguments (``model=``, ``messages=``, ``tools=``, ``stream=``,
...), records each call, and returns the next scripted item (or raises it, if the
scripted item is an ``Exception``). This lets tests:

* assert the backend was / was not called (``call_count``, ``called``),
* inspect what was sent (``last_kwargs`` / ``calls`` — model, messages, tools), and
* drive multi-round agent-loop behavior by queueing several responses.

The module also provides lightweight builders that produce ``litellm``-shaped
response objects using :class:`types.SimpleNamespace` (no ``litellm`` import):

* :func:`make_response` — a non-streaming response exposing
  ``choices[0].message.content``, optional ``choices[0].message.tool_calls`` (each
  with ``.id`` and ``.function.name`` / ``.function.arguments``), and ``usage``
  with ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``.
* :func:`make_tool_call` — a single tool-call object in the shape above.
* :func:`make_stream` — an iterable of streaming *chunk* objects, each exposing
  ``choices[0].delta.content`` and ``choices[0].delta.tool_calls``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Union

# A scripted item is either a response object (anything, typically built by the
# helpers below) or an Exception instance/class to be raised when reached.
ScriptItem = Union[Any, BaseException]


# ---------------------------------------------------------------------------
# litellm-shaped object builders (no litellm import)
# ---------------------------------------------------------------------------

def make_tool_call(
    name: str,
    arguments: Union[str, Mapping[str, Any]],
    *,
    call_id: Optional[str] = None,
    index: int = 0,
) -> SimpleNamespace:
    """Build a single native tool-call object in ``litellm``/OpenAI shape.

    The returned object exposes ``.id``, ``.type``, ``.index`` and a nested
    ``.function`` with ``.name`` and ``.arguments``. ``arguments`` is serialized
    to a JSON string when a mapping is supplied (matching provider behavior);
    strings are passed through verbatim so tests can also inject malformed JSON.
    """
    if isinstance(arguments, Mapping):
        arguments_str = json.dumps(arguments)
    else:
        arguments_str = arguments
    resolved_id = call_id if call_id is not None else f"call_{name}_{index}"
    return SimpleNamespace(
        id=resolved_id,
        type="function",
        index=index,
        function=SimpleNamespace(name=name, arguments=arguments_str),
    )


def make_usage(
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> SimpleNamespace:
    """Build a ``usage`` object with prompt/completion/total token counts."""
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


def make_response(
    content: Optional[str] = "",
    *,
    tool_calls: Optional[Sequence[SimpleNamespace]] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    finish_reason: Optional[str] = None,
    model: str = "fake/model",
) -> SimpleNamespace:
    """Build a non-streaming ``litellm``-shaped completion response.

    Exposes ``choices[0].message.content``, ``choices[0].message.tool_calls``
    (``None`` when no tool calls), ``choices[0].finish_reason``, and a top-level
    ``usage``. ``finish_reason`` defaults to ``"tool_calls"`` when tool calls are
    present, otherwise ``"stop"``.
    """
    message = SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=list(tool_calls) if tool_calls else None,
    )
    if finish_reason is None:
        finish_reason = "tool_calls" if tool_calls else "stop"
    choice = SimpleNamespace(index=0, message=message, finish_reason=finish_reason)
    return SimpleNamespace(
        choices=[choice],
        usage=make_usage(prompt_tokens, completion_tokens),
        model=model,
    )


def _make_chunk(
    content: Optional[str] = None,
    tool_calls: Optional[Sequence[SimpleNamespace]] = None,
    *,
    finish_reason: Optional[str] = None,
) -> SimpleNamespace:
    """Build a single streaming chunk with a ``choices[0].delta``."""
    delta = SimpleNamespace(
        content=content,
        tool_calls=list(tool_calls) if tool_calls else None,
    )
    choice = SimpleNamespace(index=0, delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def make_stream(
    content: Optional[str] = None,
    *,
    chunk_size: int = 1,
    tool_call_chunks: Optional[Sequence[Sequence[SimpleNamespace]]] = None,
) -> List[SimpleNamespace]:
    """Build a list of streaming chunk objects for a streamed completion.

    ``content`` is split into pieces of ``chunk_size`` characters, each emitted as
    a chunk whose ``choices[0].delta.content`` carries that piece (this lets the
    renderer test true token-by-token streaming). ``tool_call_chunks`` is an
    optional sequence of per-chunk tool-call fragment lists, appended as
    additional chunks carrying ``choices[0].delta.tool_calls``. A final
    ``finish_reason`` chunk terminates the stream.

    The result is a plain list (an iterable of chunks), matching how
    ``litellm.completion(..., stream=True)`` is consumed.
    """
    chunks: List[SimpleNamespace] = []
    if content:
        for i in range(0, len(content), max(1, chunk_size)):
            chunks.append(_make_chunk(content=content[i : i + max(1, chunk_size)]))
    if tool_call_chunks:
        for fragment in tool_call_chunks:
            chunks.append(_make_chunk(tool_calls=fragment))
    finish = "tool_calls" if tool_call_chunks else "stop"
    chunks.append(_make_chunk(finish_reason=finish))
    return chunks


# ---------------------------------------------------------------------------
# The scripted backend
# ---------------------------------------------------------------------------

class FakeBackend:
    """A scripted, offline stand-in for ``litellm.completion``.

    Construct with an ordered sequence of scripted items. Each call to the backend
    pops the next item: a normal object is returned, an ``Exception`` (instance or
    class) is raised. When the script is exhausted the backend raises
    :class:`IndexError` unless ``default`` was provided, in which case ``default``
    is returned for every subsequent call.

    Every call is recorded: :attr:`call_count`, :attr:`calls` (the full list of
    received keyword-argument mappings), and convenience accessors
    (:attr:`last_kwargs`, :attr:`models`, :attr:`tools_sent`) let tests assert on
    what was — or was not — sent to the model.

    Example::

        backend = FakeBackend([
            make_response(tool_calls=[make_tool_call("read_file", {"path": "a"})]),
            make_response("done"),
        ])
        model_router.set_completion_fn(backend)
        ...
        assert backend.call_count == 2
        assert backend.calls[0]["model"].startswith("ollama_chat/")
    """

    def __init__(
        self,
        responses: Optional[Iterable[ScriptItem]] = None,
        *,
        default: Optional[ScriptItem] = None,
    ) -> None:
        self._script: List[ScriptItem] = list(responses) if responses is not None else []
        self._default: Optional[ScriptItem] = default
        self._has_default: bool = default is not None
        self.calls: List[Dict[str, Any]] = []

    # -- queue management ---------------------------------------------------

    def queue(self, item: ScriptItem) -> "FakeBackend":
        """Append a scripted item to the response queue. Returns ``self`` for chaining."""
        self._script.append(item)
        return self

    def extend(self, items: Iterable[ScriptItem]) -> "FakeBackend":
        """Append several scripted items. Returns ``self`` for chaining."""
        self._script.extend(items)
        return self

    @property
    def remaining(self) -> int:
        """Number of scripted items not yet consumed."""
        return len(self._script)

    # -- call recording accessors ------------------------------------------

    @property
    def call_count(self) -> int:
        """How many times the backend has been invoked."""
        return len(self.calls)

    @property
    def called(self) -> bool:
        """True if the backend was invoked at least once."""
        return bool(self.calls)

    @property
    def last_kwargs(self) -> Optional[Dict[str, Any]]:
        """The keyword arguments of the most recent call (or ``None``)."""
        return self.calls[-1] if self.calls else None

    @property
    def models(self) -> List[Any]:
        """The ``model`` argument captured from each call, in order."""
        return [c.get("model") for c in self.calls]

    @property
    def tools_sent(self) -> List[Any]:
        """The ``tools`` argument captured from each call, in order.

        Useful for asserting tool schemas were (or were not) sent, e.g. the
        retry-without-tools path or the capability-policy decision.
        """
        return [c.get("tools") for c in self.calls]

    # -- the callable interface (mirrors litellm.completion) ----------------

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Record the call and return/raise the next scripted item."""
        record = dict(kwargs)
        if args:
            record["_positional"] = args
        self.calls.append(record)

        if self._script:
            item = self._script.pop(0)
        elif self._has_default:
            item = self._default
        else:
            raise IndexError(
                "FakeBackend script exhausted: "
                f"received call #{self.call_count} with no scripted response "
                "and no default configured."
            )

        return self._yield(item)

    @staticmethod
    def _yield(item: ScriptItem) -> Any:
        """Raise ``item`` if it is an exception, otherwise return it."""
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item()
        return item


def make_streaming_backend(
    streams: Iterable[Sequence[SimpleNamespace]],
    **kwargs: Any,
) -> FakeBackend:
    """Build a :class:`FakeBackend` whose scripted items are streamed chunk lists.

    Each element of ``streams`` is an iterable of chunk objects (as produced by
    :func:`make_stream`). The backend returns one stream per call, matching how
    ``litellm.completion(..., stream=True)`` returns an iterator of chunks.
    """
    return FakeBackend([list(s) for s in streams], **kwargs)


def iter_stream(stream: Sequence[SimpleNamespace]) -> Iterator[SimpleNamespace]:
    """Yield chunks from a built stream (helper for manual consumption in tests)."""
    yield from stream
