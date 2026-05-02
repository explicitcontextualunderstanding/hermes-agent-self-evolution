# OTel Trace ID Injection into MCP Tool Calls — Investigation & Plan

## Current State Analysis

### 1. hermes-otel Plugin (source: `~/.hermes/profiles/coding/plugins/hermes_otel/`)

**Entry point** (`__init__.py`):
```python
def register(ctx):
    ctx.register_hook("pre_tool_call", hooks.on_pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.on_post_tool_call)
    ...
```

**`on_pre_tool_call`** (`hooks.py:438`): Receives `(tool_name, args, task_id, **kwargs)`. It starts an OTel span for the tool but does **not** modify `args` — it only observes.

**`on_post_tool_call`** (`hooks.py:494`): Receives `(tool_name, args, result, task_id, **kwargs)`. Ends the tool span. Also observer-only.

**No trace_id extraction API exists.** The tracer (`HermesOTelPlugin` in `tracer.py`) uses the OTel SDK internally to create spans. The trace_id is an integer (128-bit) embedded in the OTel `SpanContext`, accessible via `span.get_span_context().trace_id`. There is no public helper on the tracer singleton to get the current trace_id as a hex string.

### 2. model_tools.py — Tool Dispatch (`handle_function_call`)

At line 644:
```python
def handle_function_call(
    function_name, function_args, task_id=None,
    tool_call_id=None, session_id=None, user_task=None,
    enabled_tools=None, skip_pre_tool_call_hook=False,
) -> str:
```

Key flow:
1. Calls `get_pre_tool_call_block_message(...)` which fires `invoke_hook("pre_tool_call", ...)` — observer plugins see args
2. Calls `registry.dispatch(function_name, function_args, ...)` — executes tool with the **original** `function_args`
3. Fires `invoke_hook("post_tool_call", ...)` — observer
4. Fires `invoke_hook("transform_tool_result", ...)` — can replace result, NOT args

**No mechanism to inject trace_id into `function_args` before dispatch.**

### 3. run_agent.py — Conversation Loop (`run_conversation`)

The loop at line 10694:
```python
while (api_call_count < self.max_iterations ...):
    response = client.chat.completions.create(...)
    if response.tool_calls:
        for tool_call in response.tool_calls:
            result = handle_function_call(tool_name, tool_args, ...)
            messages.append(tool_result_message)
```

Tool execution is handled by `_execute_tool_calls()` (line 9190) → `_execute_tool_calls_sequential()` (line 9721) → `handle_function_call()` (line 9995/10015).

**No trace_id is generated or tracked in the agent loop itself.** The agent has no reference to any OTel trace_id.

### 4. Backend (compose-pkl)

The backend's `realize_pod` MCP tool **already accepts** an optional `trace_id` parameter. Per the implementation roadmap (`docs/implementation-roadmap.md:72-95`), the plan is:
```
trace_id = hermes-otel.root_span.trace_id
Every MCP tool call to compose-pkl carries it
request.headers: x-otel-trace-id: trace_id
```

The backend will create child spans keyed to this trace_id.

### 5. The Gap

| Component | Has trace_id | Can inject into MCP args |
|-----------|-------------|------------------------|
| hermes-otel | ✓ (internal OTel spans) | ✗ (observer-only hooks) |
| model_tools.py | ✗ | ✗ |
| run_agent.py | ✗ | ✗ |
| compose-pkl backend | ✓ (accepts in args) | N/A |

**Nothing bridges the trace_id from hermes-otel spans into MCP tool arguments.**

---

## Injection Point Candidates

### Candidate A: `transform_tool_result` Hook (✗ wrong seam)

This fires **after** the tool executes. It can transform the result but cannot modify the input args. **Not viable.**

### Candidate B: `pre_tool_call` Hook in hermes-otel (✓ clean, plugin-side)

The `on_pre_tool_call` hook in `hooks.py` receives `args` as a mutable dict. However, mutating `args` here would not affect `handle_function_call` because:

- `get_pre_tool_call_block_message()` (which fires `invoke_hook("pre_tool_call", ...)`) only looks at **return values** from hooks for block directives
- The original `function_args` dict is passed to `registry.dispatch()` directly, not re-checked after hooks

**However** — if we add a new hook type (e.g., `transform_tool_args`) that fires between the pre_tool_call hook and dispatch, the plugin could mutate args there. Or we could make `on_pre_tool_call`'s **return value** merge into the args dict.

**Verdict**: Viable with a small extension to the hook system (see Recommendation below).

### Candidate C: `handle_function_call` in `model_tools.py` (✓ pragmatic)

**Line 687-730**: This is the single choke point for all tool dispatch (except agent-level tools like `todo`/`memory`). We could inject trace_id here.

```python
# After firing pre_tool_call hook, before registry.dispatch:
if function_name in MCP_TOOLS:
    trace_id = get_current_trace_id()  # new utility
    if trace_id:
        function_args = {**function_args, "trace_id": trace_id}
```

**Pros**: Single file change, no new hooks needed.
**Cons**: `model_tools.py` would need to know which tools are "MCP tools" and have a way to get the trace_id.

### Candidate D: Add a `transform_tool_args` Hook (✓ cleanest, extensible)

Add a new hook type `transform_tool_args` that fires between `pre_tool_call` and `registry.dispatch()`. The hermes-otel plugin registers for this hook and injects `trace_id` into the args dict.

**Pros**:
- Clean separation of concerns (plugin handles telemetry, agent handles dispatch)
- Future trace_id sources (e.g., Langfuse, LangSmith) just register the same hook
- No hardcoded tool-name check
- Reuses existing plugin infrastructure

**Cons**: Requires a ~5-line change in `model_tools.py` + registration in `plugins.py` `VALID_HOOKS`.

### Candidate E: Inject in `_execute_tool_calls_sequential()` / `_execute_tool_calls_concurrent()` in `run_agent.py`

Modify the args dict **before** calling `handle_function_call()`:

```python
# In _execute_tool_calls_sequential line ~9744:
function_args = json.loads(tool_call.function.arguments)
# Inject trace_id for MCP tools
trace_id = extract_current_trace_id()
if trace_id and function_name in MCP_TOOLS:
    function_args["trace_id"] = trace_id
```

**Pros**: Close to where args are parsed from JSON, clear visibility.
**Cons**: Duplication across sequential and concurrent paths; `run_agent.py` is already ~14K lines.

---

## Recommendation: Candidate D — Add `transform_tool_args` Hook + Plugin Registration

**This is the cleanest approach** because:

1. **Single responsibility**: The hermes-otel plugin (which owns the OTel pipeline) is responsible for extracting and injecting the trace_id.
2. **No agent loop changes**: The massive `run_agent.py` doesn't need modification.
3. **Extensible**: Other plugins (Langfuse, LangSmith, custom observability) could also inject their own trace IDs.
4. **Backward compatible**: The `transform_tool_args` hook is a no-op when no plugins register for it.
5. **Minimal code**: ~10 lines total across 2 files.

### Implementation Sketch

**File 1: `hermes_cli/plugins.py`** (add to `VALID_HOOKS` set):

```python
VALID_HOOKS: Set[str] = {
    ...
    "transform_tool_args",   # <-- NEW
    ...
}
```

**File 2: `model_tools.py`** (add hook fire before dispatch, around line ~720):

```python
        # ── Plugin transform_tool_args hook ──
        # Allows plugins to inject additional arguments (e.g., trace_id)
        # before the tool is dispatched. The hook receives the current args
        # dict and may return a (possibly modified) dict to replace it.
        try:
            from hermes_cli.plugins import invoke_hook
            transform_results = invoke_hook(
                "transform_tool_args",
                tool_name=function_name,
                args=function_args,
                task_id=task_id or "",
                session_id=session_id or "",
                tool_call_id=tool_call_id or "",
            )
            for tr in transform_results:
                if isinstance(tr, dict):
                    function_args = {**function_args, **tr}
                    break
        except Exception:
            pass
```

**File 3: `plugins/hermes_otel/hooks.py`** (add new callback):

```python
def on_transform_tool_args(tool_name: str, args: dict, **kwargs) -> dict:
    """Inject the current OTel trace_id into tool args for MCP tools."""
    # Only inject when the tool accepts trace_id (MCP backend tools)
    if tool_name not in _MCP_BACKEND_TOOLS:
        return {}
    
    trace_id = _get_current_trace_id()
    if trace_id:
        return {"trace_id": trace_id}
    return {}
```

Where `_get_current_trace_id()` extracts the hex trace_id from the active OTel span:

```python
from opentelemetry import trace

def _get_current_trace_id() -> str | None:
    """Return the hex trace_id from the current OTel span, if any."""
    span = trace.get_current_span()
    if not span or span == trace.INVALID_SPAN:
        return None
    span_context = span.get_span_context()
    if span_context.trace_id == trace.INVALID_TRACE_ID:
        return None
    return format(span_context.trace_id, "032x")
```

**File 4: `plugins/hermes_otel/__init__.py`** (register new hook):

```python
ctx.register_hook("transform_tool_args", hooks.on_transform_tool_args)
```

### Alternative: Simpler Approach (if hook system change is undesirable)

If adding a new hook type is too invasive for the initial pass, **Candidate C** (inject in `handle_function_call` directly) is a pragmatic alternative:

```python
# In handle_function_call, around line 720, before registry.dispatch:
if function_name in _MCP_TOOLS:
    try:
        from hermes_cli.plugins import invoke_hook
        results = invoke_hook("pre_tool_call", ...)
        # ... existing block logic ...
    except Exception:
        pass
    # NEW: inject trace_id from hermes-otel
    try:
        from hermes_otel.helpers import get_current_trace_id
        trace_id = get_current_trace_id()
        if trace_id:
            function_args = {**function_args, "trace_id": trace_id}
    except ImportError:
        pass
```

**This is recommended as the first iteration** — it's 8 lines, doesn't require new hook types, and can be refactored to the hook-based approach later.

---

## OTel Trace ID Extraction

The OTel SDK exposes the current span via:

```python
from opentelemetry import trace

span = trace.get_current_span()
if span and span != trace.INVALID_SPAN:
    ctx = span.get_span_context()
    trace_id_hex = format(ctx.trace_id, "032x")  # "0af7651916cd43dd8448eb211c80319c"
```

A thin utility (`hermes_otel/helpers.py`) should wrap this with proper error handling.

---

## Which MCP Tools Need trace_id?

Based on the backend investigation, the primary target is `realize_pod` (and potentially `run_compose_up`). The set should be defined as a configurable list:

```python
_MCP_BACKEND_TOOLS = frozenset({
    "realize_pod",
    "run_compose_up",
    # Future: other MCP tools that accept trace_id
})
```

---

## Estimated Effort

| Approach | Effort | Complexity | Risk |
|----------|--------|------------|------|
| **D** (new hook) | **Low** (2-3 files, ~15 lines) | Low | Very low — hook is a no-op if unregistered |
| **C** (direct injection) | **Low** (1 file, ~8 lines) | Lowest | Low — hermes_otel must be importable |
| **B** (plugin-side only) | Low but needs agent changes too | Medium | Medium — args mutation order fragile |
| **E** (run_agent.py) | Low but duplicated | Medium | Medium — 14K LOC file touch |

**Recommended: Candidate D** (`transform_tool_args` hook) as the long-term design, but **start with Candidate C** (direct injection in `handle_function_call`) for immediate implementation since it requires zero infrastructure changes.

### Implementation Steps (Candidate C — first iteration)

1. Add `get_current_trace_id()` helper to `hermes_otel/helpers.py` (or `__init__.py`)
2. In `model_tools.py` `handle_function_call()`, inject `trace_id` into `function_args` before `registry.dispatch()` for known MCP tools
3. Define `_MCP_TOOLS` set at module level in `model_tools.py`

Total: **~15 lines of production code**

### Implementation Steps (Candidate D — full hook approach)

1. Add `"transform_tool_args"` to `VALID_HOOKS` in `hermes_cli/plugins.py`
2. Add hook fire in `model_tools.py` before `registry.dispatch()`
3. Add `on_transform_tool_args()` callback + `_get_current_trace_id()` helper in `hermes_otel`
4. Register the new hook in `hermes_otel/__init__.py`

Total: **~20 lines of production code**
