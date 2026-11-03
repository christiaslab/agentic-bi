# ADR-0005: Analyst agent runtime, the Messages API with the project's own tool runner

Status: Proposed, 2026-11-02, in S4 ([tasks/s4-analyst-agent.json](../../tasks/s4-analyst-agent.json)); the owner accepts or amends it when reviewing the pull request.

## Context

[ADR-0002](0002-structured-query-and-compiler.md) fixes what the analyst agent may do: emit structured queries, never SQL (points 1 and 5); use only the project's four tools, `catalog`, `schema`, `run_query` and `explain_measure`, with no built-in tool (point 9); treat tool output as data, never as instructions (point 9). The brief leaves the runtime open between two options, the Claude Agent SDK and the Anthropic Messages API with a tool runner, and asks that everything be proven offline with no API key, by replaying recorded model turns. The owner runs the live check with their own key.

## Options

1. **Claude Agent SDK** (`claude-agent-sdk`): Claude Code packaged as a library. It brings the Claude Code harness: built-in tools (Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch), its own system prompt, context management, permissions and sessions, run through a Claude Code CLI process. The four project tools would be an in-process MCP server beside the built-in ones.
2. **Messages API** (`anthropic`): one `messages.create` call per model turn, inside a tool runner, either the SDK's beta helper `client.beta.messages.tool_runner` or a loop of the project's own.

## Decision

1. **Runtime: the Messages API**, through the `anthropic` package, pinned to `anthropic==2.3.0` from PyPI (<https://pypi.org/project/anthropic/2.3.0/>, released 2026-10-23), in `[project] dependencies`. It is the only new direct dependency. `uv.lock`, regenerated with uv 0.12.3 (the CI pin), gains 11 packages, all from PyPI: anthropic 2.3.0, anyio 4.16.0, docstring-parser 0.18.0, h11 0.16.0, httpcore2 2.16.0, httpx2 2.16.0, httpx2-jsfetch 1.0 (for the emscripten platform only, not installed here), jiter 0.18.0, opentelemetry-api 1.45.2, sniffio 1.3.1 and truststore 0.10.5. Release 2.x sends API version `2026-10-21`, which refuses sampling parameters; the agent sends none. The library installed with the lock and is tested without a key (below), so the fallback the brief allows was not needed.
2. **Tool runner: the project's own loop** (`src/agentic_bi/agent/loop.py`), not the SDK's beta `tool_runner`:
   - *Offline proof.* The loop talks to a one-method protocol, `ModelClient.create`. The live `AnthropicModel` calls the SDK; the offline `ReplayModel` returns hand-written turns and checks that the loop answered each tool call in order. Tests drive the very loop a live run uses, with no key, no HTTP mock and no API client.
   - *Control the brief asks for.* Every tool result is wrapped in the data delimiter; refusals and limit errors go back as error results; model calls, tool calls and answer attempts have budgets; the final reply is checked (every figure in the rows, a refusal naming a catalog code) with one chance to correct it; the queries, SQL, parameters and rows of the answer come from the tool layer's own record. The beta runner offers hooks for much of this, but it is a beta surface, and its own way of feeding results back would sit between the tools and the model.
   - *Size.* The loop is under 250 lines with its run state and transcript record, and reads in one sitting.
3. **Not the Agent SDK.** Point 9 of ADR-0002 allows no built-in tool. With the Agent SDK the built-in tools exist and have to be switched off by configuration, so one configuration slip gives the model a shell, files or the web. It also runs a separate process with its own system prompt, so the request is not wholly the project's to pin, record and replay. This is a design comparison; the Agent SDK was not installed.
4. **Request shape**, built in one place (`model.build_request`) and sent on every turn:
   - `model`: a setting, `AgentSettings.model`, default `claude-sonnet-5-5`, overridden by the environment variable `AGENTIC_BI_MODEL` or by `--model`. No prompt names a model; a test checks it.
   - `max_tokens`: 16,000, so a non-streamed request stays within the SDK's timeouts.
   - `thinking`: `{"type": "adaptive", "display": "summarized"}`, so a transcript shows a summary of the model's reasoning for the owner's check. The display does not change what is billed.
   - `output_config.effort`: a setting, default `high`, the model's own default, sent so that the transcript records it.
   - `tool_choice`: `auto`. The current models refuse forced tool use; the system prompt asks for the catalog first.
   - `cache_control`: ephemeral. The system prompt and the tool list are the same on every turn of every question.
   - Not sent: sampling parameters, server tools, strict tool schemas (the structured-query schema uses keywords that strict mode may not accept; the parser and the compiler validate every input instead) and a structured-output format (the loop parses and checks the final reply itself).
5. **Default model: `claude-sonnet-5-5`**, as the brief proposes. Mapping a question onto the catalog is a short tool-use task, where Sonnet's cost and speed fit; the S5a verifier is the second opinion. Another model is a rerun with `--model`, with no code change.
6. **No server-side fallback model.** A model refusal (`stop_reason` `refusal`) ends the question as `failed`, with its category in the transcript. The API's fallback option would let another model answer in its place without a trace in the answer, which would blur what the S5b eval measures with a pinned model.
7. **The live run is the owner's.** `uv run python -m agentic_bi.agent ask "<question>"` and `... session --questions <file> --out <dir>` stop with a clear message when `ANTHROPIC_API_KEY` is not set. The key is read by the SDK only; the project never reads, writes or prints it. [docs/agent.md](../agent.md) says what to check in the transcripts.

## Consequences

- The offline tests prove the loop, the tools, the delimiter and the checks, not the model. The first proof of the request shape against the live API is the owner's run.
- A new model id is a setting. A new request feature (structured outputs, strict tools, a fallback model) is a change to `build_request` and its test.
- An upgrade of `anthropic` is a reviewed change of the pin. The agent uses only `messages.create`, the response's `to_dict` and the SDK's error classes.
